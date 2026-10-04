"""Modèles SPEC-10 : CONVERSATION, PARTICIPANT_CONVERSATION, MESSAGE, BLOCAGE
([[02-modele-donnees]] §2.7).

- Une `Conversation` n'existe que rattachée à une relation autorisée (une
  commande ou un accompagnement) dont l'utilisateur est partie ; **une seule**
  conversation par relation (hypothèse Q-46a, contrainte d'unicité) ;
- un `Message` porte `contenu` et/ou `fichier`, avec `id_client` **unique par
  auteur** : c'est la clé d'idempotence qui évite tout doublon après une
  reconnexion (docs/08 SPEC-10, prompt F-13 point 4) ;
- `Blocage` empêche l'envoi de messages dans les deux sens (journal Q-15),
  sans toucher aux commandes et livraisons en cours.
"""

from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class TypeConversation(models.TextChoices):
    COMMANDE = "commande", "Commande"
    ACCOMPAGNEMENT = "accompagnement", "Accompagnement"


class Conversation(ModeleBase):
    """Fil de discussion rattaché à une commande ou à un accompagnement."""

    type = models.CharField(max_length=20, choices=TypeConversation.choices)
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.PROTECT,
        related_name="conversations",
        null=True,
        blank=True,
    )
    accompagnement = models.ForeignKey(
        "accompagnements.Accompagnement",
        on_delete=models.PROTECT,
        related_name="conversations",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "conversation"
        verbose_name = "Conversation"
        verbose_name_plural = "Conversations"
        ordering = ["-cree_le"]
        constraints = [
            # Une seule conversation par commande / par accompagnement (Q-46a).
            # MySQL autorise plusieurs NULL dans un index UNIQUE : la contrainte
            # ne s'applique donc qu'aux lignes réellement rattachées.
            models.UniqueConstraint(
                fields=["commande"],
                name="uniq_conversation_commande",
            ),
            models.UniqueConstraint(
                fields=["accompagnement"],
                name="uniq_conversation_accompagnement",
            ),
        ]

    def __str__(self) -> str:
        return f"Conversation {self.type} {self.id[:8]}…"

    def participant(self, utilisateur: object | None) -> "Participant | None":
        """Ligne de participant de `utilisateur` dans cette conversation (ou None)."""
        if utilisateur is None:
            return None
        return self.participants.filter(utilisateur_id=getattr(utilisateur, "id", None)).first()

    def dernier_message(self) -> "Message | None":
        """Dernier message visible (soft delete exclus), pour l'aperçu de liste."""
        return self.messages.filter(supprime_le__isnull=True).order_by("-cree_le", "-id").first()

    def non_lus(self, utilisateur: object | None) -> int:
        """Messages des autres postérieurs au dernier lu par `utilisateur`."""
        ligne = self.participant(utilisateur)
        requete = self.messages.filter(supprime_le__isnull=True).exclude(
            auteur_id=getattr(utilisateur, "id", None)
        )
        if ligne is not None and ligne.dernier_message_lu_id is not None:
            pointeur = Message.objects.filter(id=ligne.dernier_message_lu_id).first()
            if pointeur is not None:
                requete = requete.filter(cree_le__gt=pointeur.cree_le)
        return requete.count()


class Message(ModeleBase):
    """Message d'une conversation : texte et/ou pièce jointe, idempotent."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="messages_ecrits",
    )
    id_client = models.CharField(
        max_length=64,
        help_text="Identifiant client (UUID) : clé d'idempotence, unique par auteur.",
    )
    contenu = models.TextField(
        null=True,
        blank=True,
        help_text="Texte brut ; rendu comme du texte côté client (aucune exécution de script).",
    )
    fichier = models.ForeignKey(
        "fichiers.Fichier",
        on_delete=models.PROTECT,
        related_name="messages",
        null=True,
        blank=True,
    )
    supprime_le = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Soft delete ; null = message visible.",
    )

    class Meta:
        db_table = "message"
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        ordering = ["cree_le", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["auteur", "id_client"],
                name="uniq_message_auteur_id_client",
            ),
        ]
        indexes = [
            models.Index(
                fields=["conversation", "cree_le"],
                name="idx_message_conv_cree",
            ),
        ]

    def __str__(self) -> str:
        return f"Message {self.id[:8]}… par {self.auteur_id}"


class Participant(ModeleBase):
    """Membre d'une conversation, avec pointeur de lecture (`dernier_message_lu`)."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="participations",
    )
    dernier_message_lu = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
        help_text="Dernier message lu par ce participant (accusé de lecture).",
    )

    class Meta:
        db_table = "participant_conversation"
        verbose_name = "Participant de conversation"
        verbose_name_plural = "Participants de conversation"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "utilisateur"],
                name="uniq_participant_conversation_utilisateur",
            ),
        ]
        indexes = [
            models.Index(
                fields=["utilisateur", "conversation"],
                name="idx_participant_util_conv",
            ),
        ]

    def __str__(self) -> str:
        return f"Participant {self.utilisateur_id} dans {self.conversation_id}"


class Blocage(ModeleBase):
    """Blocage unidirectionnel enregistré ; appliqué dans les deux sens (Q-15)."""

    bloqueur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocages_emis",
    )
    bloque = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocages_subis",
    )

    class Meta:
        db_table = "blocage"
        verbose_name = "Blocage"
        verbose_name_plural = "Blocages"
        constraints = [
            models.UniqueConstraint(
                fields=["bloqueur", "bloque"],
                name="uniq_blocage_couple",
            ),
        ]

    def __str__(self) -> str:
        return f"Blocage {self.bloqueur_id} → {self.bloque_id}"

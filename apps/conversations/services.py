"""Logique métier du chat temps réel (F-13, docs/02 §2.7, docs/08 SPEC-10).

Toutes les règles vivent ici ; les vues restent minces (valident la forme,
appellent le service, répondent). Rien d'autre que les services n'accède aux
modèles pour écrire. Le temps réel (publier vers les canaux) et la
notification (`notifier`) sont déclenchés depuis ces services, jamais depuis
une vue.
"""

from typing import Any

from commun.exceptions import AccesInterdit, NonTrouve, ValidationMetier
from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.accompagnements.models import Accompagnement
from apps.commandes.models import Commande
from apps.conversations.models import (
    Blocage,
    Conversation,
    Message,
    Participant,
    TypeConversation,
)
from apps.fichiers.models import ContexteFichier, Fichier
from apps.notifications.services import notifier
from apps.utilisateurs.models import Utilisateur


def _autre_participant(conversation: Conversation, utilisateur_id: str) -> Utilisateur | None:
    ligne = conversation.participants.exclude(utilisateur_id=utilisateur_id).first()
    return ligne.utilisateur if ligne is not None else None


def _enveloppe_message(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "conversation": message.conversation_id,
        "type_conversation": message.conversation.type,
        "auteur": message.auteur_id,
        "contenu": message.contenu,
        "fichier_id": message.fichier_id,
        "id_client": message.id_client,
        "cree_le": message.cree_le.isoformat(),
    }


class ServiceConversation:
    """Ouverture/obtention d'une conversation et listing des siennes."""

    @staticmethod
    def _parties_relation(données: dict[str, Any]) -> tuple[Utilisateur, Utilisateur]:
        """Résout les deux parties autorisées d'une relation, ou lève 404."""
        type_relation = données["type"]
        if type_relation == TypeConversation.COMMANDE:
            commande = (
                Commande.objects.select_related("acheteur", "agriculteur")
                .filter(id=données.get("commande_id"))
                .first()
            )
            if commande is None:
                raise NonTrouve(
                    code="conversation_introuvable",
                    message="Relation introuvable.",
                )
            return commande.acheteur, commande.agriculteur
        accompagnement = (
            Accompagnement.objects.select_related("agriculteur", "ingenieur")
            .filter(id=données.get("accompagnement_id"))
            .first()
        )
        if accompagnement is None or accompagnement.ingenieur is None:
            raise NonTrouve(
                code="conversation_introuvable",
                message="Relation introuvable.",
            )
        return accompagnement.agriculteur, accompagnement.ingenieur

    @staticmethod
    @transaction.atomic
    def creer_ou_obtenir(utilisateur: Utilisateur, données: dict[str, Any]) -> Conversation:
        """Une conversation par relation (Q-46a) ; l'appelant doit être partie."""
        partie_a, partie_b = ServiceConversation._parties_relation(données)
        if utilisateur.id not in (partie_a.id, partie_b.id):
            # Ressource privée d'autrui : 404, jamais 403 (conventions §5).
            raise NonTrouve(
                code="conversation_introuvable",
                message="Relation introuvable.",
            )
        type_relation = données["type"]
        if type_relation == TypeConversation.COMMANDE:
            filtre: dict[str, Any] = {"commande_id": données.get("commande_id")}
        else:
            filtre = {"accompagnement_id": données.get("accompagnement_id")}
        conversation = Conversation.objects.filter(**filtre).first()
        if conversation is None:
            conversation = Conversation.objects.create(type=type_relation, **filtre)
        for partie in (partie_a, partie_b):
            Participant.objects.get_or_create(conversation=conversation, utilisateur=partie)
        return conversation

    @staticmethod
    def vue_liste(utilisateur: Utilisateur) -> list[Conversation]:
        """Les conversations de l'utilisateur, plus récentes d'abord."""
        return list(
            Conversation.objects.filter(participants__utilisateur=utilisateur)
            .select_related("commande", "accompagnement")
            .prefetch_related("participants__utilisateur")
            .distinct()
            .order_by("-maj_le", "-cree_le")
        )

    @staticmethod
    def obtenir_participante(utilisateur: Utilisateur, identifiant: str) -> Conversation:
        """Conversation dont `utilisateur` est participant, sinon 404 (privée)."""
        conversation = Conversation.objects.filter(
            id=identifiant, participants__utilisateur=utilisateur
        ).first()
        if conversation is None:
            raise NonTrouve(
                code="conversation_introuvable",
                message="Conversation introuvable.",
            )
        return conversation


class ServiceMessage:
    """Envoi idempotent, lecture de l'historique et marquage « lu »."""

    @staticmethod
    def _fichier_piece(auteur: Utilisateur, fichier_id: str) -> Fichier:
        fichier = Fichier.objects.filter(id=fichier_id).first()
        if fichier is None:
            raise NonTrouve(code="ressource_introuvable", message="Fichier introuvable.")
        # La pièce doit appartenir à l'émetteur et avoir été déclarée en contexte `chat`
        # (contrôle d'accès fichiers F-03 : un tiers ne peut pas joindre un fichier privé).
        if fichier.proprietaire_id != auteur.id or fichier.contexte != ContexteFichier.CHAT:
            raise NonTrouve(code="ressource_introuvable", message="Fichier introuvable.")
        return fichier

    @staticmethod
    def _blocage_actif(conversation: Conversation, auteur: Utilisateur) -> bool:
        autre = _autre_participant(conversation, auteur.id)
        if autre is None:
            return False
        return Blocage.objects.filter(
            Q(bloqueur_id=auteur.id, bloque_id=autre.id)
            | Q(bloqueur_id=autre.id, bloque_id=auteur.id)
        ).exists()

    @staticmethod
    @transaction.atomic
    def envoyer(
        conversation: Conversation,
        auteur: Utilisateur,
        contenu: str | None,
        fichier_id: str | None,
        id_client: str,
    ) -> tuple[Message, bool]:
        """Envoie un message ; renvoie (message, créé). Idempotent par `id_client`.

        Lève `message_vide` (422) si ni contenu ni fichier, `blocage_actif` (403)
        si l'une des deux parties a bloqué l'autre (Q-15).
        """
        # 1. Idempotence : le même (auteur, id_client) ne crée jamais de doublon.
        existant = Message.objects.filter(auteur=auteur, id_client=id_client).first()
        if existant is not None:
            return existant, False

        contenu_propre = (contenu or "").strip()
        if not contenu_propre and not fichier_id:
            raise ValidationMetier(
                code="message_vide",
                message="Un message doit contenir du texte ou une pièce jointe.",
            )

        # 2. Blocage dans les deux sens (Q-15).
        if ServiceMessage._blocage_actif(conversation, auteur):
            raise AccesInterdit(
                code="blocage_actif",
                message=(
                    "Vous ne pouvez plus écrire : l'un des deux participants a bloqué "
                    "l'échange dans cette conversation."
                ),
            )

        fichier = ServiceMessage._fichier_piece(auteur, fichier_id) if fichier_id else None

        # 3. Stockage : contenu texte brut, jamais rendu comme du HTML.
        try:
            message = Message.objects.create(
                conversation=conversation,
                auteur=auteur,
                id_client=id_client,
                contenu=contenu_propre or None,
                fichier=fichier,
            )
        except IntegrityError:
            # Course : un (auteur, id_client) concurrent a gagné → on le relit.
            gagnant = Message.objects.filter(auteur=auteur, id_client=id_client).first()
            if gagnant is not None:
                return gagnant, False
            raise

        # 4. Notification + temps réel vers l'autre partie (best effort Q-46c).
        autre = _autre_participant(conversation, auteur.id)
        if autre is not None:
            notifier(
                autre,
                "chat",
                f"Nouveau message de {auteur.nom}",
                (contenu_propre[:120] if contenu_propre else "Pièce jointe"),
                lien=f"/messages/{conversation.id}",
            )
        from apps.conversations.consommateurs import publier

        publier(
            autre.id if autre is not None else auteur.id,
            {"type": "message.nouveau", "donnees": _enveloppe_message(message)},
        )
        # Le canal de l'émetteur reçoit aussi l'event (multi-onglets cohérents).
        publier(
            auteur.id,
            {"type": "message.nouveau", "donnees": _enveloppe_message(message)},
        )
        return message, True

    @staticmethod
    def vue_messages(
        conversation: Conversation, utilisateur: Utilisateur, avant: str | None, taille: int = 50
    ) -> list[Message]:
        """Historique paginé par curseur `avant` (id d'un message le plus ancien)."""
        requete = conversation.messages.filter(supprime_le__isnull=True).select_related(
            "auteur", "fichier"
        )
        if avant:
            pointeur = Message.objects.filter(id=avant, conversation=conversation).first()
            if pointeur is None:
                raise NonTrouve(
                    code="conversation_introuvable",
                    message="Message de curseur introuvable.",
                )
            requete = requete.filter(cree_le__lt=pointeur.cree_le)
        # Fenêtre la plus récente, puis remise en ordre chronologique croissant.
        fenetre = list(requete.order_by("-cree_le", "-id")[:taille])
        del utilisateur  # l'accès participant est contrôlé par la vue/service appelant
        return list(reversed(fenetre))

    @staticmethod
    @transaction.atomic
    def marquer_lu(
        conversation: Conversation, utilisateur: Utilisateur, dernier_message_lu_id: str
    ) -> Participant:
        """Pointe le dernier message lu ; notifie l'autre partie via WS `message.lu`."""
        ligne = conversation.participant(utilisateur)
        if ligne is None:
            raise NonTrouve(
                code="conversation_introuvable",
                message="Conversation introuvable.",
            )
        message = Message.objects.filter(
            id=dernier_message_lu_id, conversation=conversation
        ).first()
        if message is None:
            raise NonTrouve(code="ressource_introuvable", message="Message introuvable.")
        ligne.dernier_message_lu = message
        ligne.save(update_fields=["dernier_message_lu", "maj_le"])

        autre = _autre_participant(conversation, utilisateur.id)
        if autre is not None:
            from apps.conversations.consommateurs import publier

            publier(
                autre.id,
                {
                    "type": "message.lu",
                    "donnees": {
                        "conversation": conversation.id,
                        "lu_par": utilisateur.id,
                        "dernier_message_lu": message.id,
                    },
                },
            )
        return ligne


class ServiceBlocage:
    """Blocage/déblocage entre deux utilisateurs (Q-15)."""

    @staticmethod
    @transaction.atomic
    def bloquer(bloqueur: Utilisateur, cible_id: str) -> Blocage:
        if bloqueur.id == cible_id:
            raise ValidationMetier(
                code="blocage_invalide",
                message="Impossible de se bloquer soi-même.",
            )
        cible = Utilisateur.objects.filter(id=cible_id).first()
        if cible is None:
            raise NonTrouve(code="ressource_introuvable", message="Utilisateur introuvable.")
        blocage, _ = Blocage.objects.get_or_create(bloqueur=bloqueur, bloque=cible)
        return blocage

    @staticmethod
    def debloquer(bloqueur: Utilisateur, cible_id: str) -> bool:
        supprimés, _ = Blocage.objects.filter(bloqueur=bloqueur, bloque_id=cible_id).delete()
        return supprimés > 0

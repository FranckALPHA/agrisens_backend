"""Modèle NOTIFICATION ([[02-modele-donnees]] §2.7, prompt F-03 backend point 2).

Notifications internes uniquement en V1 (push et SMS reportés). La livraison
se fait par interrogation périodique côté client ; le canal WebSocket arrive
en F-13 et réutilisera ce modèle.
"""

from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class TypeNotification(models.TextChoices):
    """Catégories de la page FE-05 ([[09-dossier-frontend]] §3)."""

    COMMANDE = "commande", "Commande"
    PAIEMENT = "paiement", "Paiement"
    LIVRAISON = "livraison", "Livraison"
    ACCOMPAGNEMENT = "accompagnement", "Accompagnement"
    ABONNEMENT = "abonnement", "Abonnement"
    ADMINISTRATION = "administration", "Administration"
    SIGNALEMENT = "signalement", "Signalement"
    CHAT = "chat", "Chat"


class Notification(ModeleBase):
    """Message interne destiné à un utilisateur, avec état lu/non lu."""

    destinataire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="Utilisateur destinataire",
    )
    type = models.CharField(
        max_length=30,
        choices=TypeNotification.choices,
        help_text="Catégorie affichée dans FE-05",
    )
    titre = models.CharField(max_length=150)
    contenu = models.CharField(max_length=500, blank=True, default="")
    lien = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="Chemin applicatif à ouvrir (ex. /admin/utilisateurs) ; null si sans cible",
    )
    lue_le = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date de lecture ; null = non lue",
    )

    class Meta:
        db_table = "notification"
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(
                fields=["destinataire", "lue_le"],
                name="idx_notification_dest_lue",
            ),
        ]

    def __str__(self) -> str:
        return f"Notification [{self.type}] pour {self.destinataire_id}"

    @property
    def non_lue(self) -> bool:
        return self.lue_le is None

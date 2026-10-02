"""Modèles des livraisons, itinéraires et incidents (SPEC-06, 02 §2.3 et §3.2).

Le code de remise (6 chiffres) est stocké CHIFFRÉ (Fernet, Q-27) : jamais
en clair en base, jamais dans les journaux, comparé en temps constant.
"""

from commun.champs_geo import ChampLigne, ChampPoint
from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models

from apps.commandes.models import Commande
from apps.fichiers.models import Fichier


class StatutLivraison(models.TextChoices):
    PROPOSEE = "proposee", "Proposée"
    ACCEPTEE = "acceptee", "Acceptée"
    EN_COURS = "en_cours", "En cours"
    LIVREE = "livree", "Livrée"
    CLIENT_ABSENT = "client_absent", "Client absent"
    ECHEC_LIVRAISON = "echec_livraison", "Échec de livraison"
    ANNULEE = "annulee", "Annulée"


class Livraison(ModeleBase):
    commande = models.OneToOneField(
        Commande,
        on_delete=models.PROTECT,
        related_name="livraison",
    )
    livreur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="livraisons",
    )
    statut = models.CharField(
        max_length=20,
        choices=StatutLivraison.choices,
        default=StatutLivraison.PROPOSEE,
    )
    numero_tentative = models.IntegerField(default=1)
    code_remise_chiffre = models.CharField(
        max_length=255,
        help_text="Code à 6 chiffres chiffré (Q-27), jamais en clair",
    )
    tentatives_code_echouees = models.IntegerField(default=0)
    acceptee_le = models.DateTimeField(null=True, blank=True)
    demarree_le = models.DateTimeField(null=True, blank=True)
    livree_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "livraison"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["statut"], name="livraison_statut_idx"),
            models.Index(fields=["livreur", "statut"], name="livraison_livreur_statut_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.commande_id} ({self.statut})"


class SourceItineraire(models.TextChoices):
    LIGNE_DROITE = "ligne_droite", "Ligne droite"
    SERVICE_ROUTAGE = "service_routage", "Service de routage"


class Itineraire(ModeleBase):
    livraison = models.OneToOneField(
        Livraison,
        on_delete=models.CASCADE,
        related_name="itineraire",
    )
    point_depart = ChampPoint()
    point_arrivee = ChampPoint()
    trajet = ChampLigne(null=True, blank=True)
    distance_km = models.DecimalField(max_digits=8, decimal_places=2)
    duree_estimee_min = models.IntegerField(null=True, blank=True)
    source = models.CharField(
        max_length=20,
        choices=SourceItineraire.choices,
        default=SourceItineraire.LIGNE_DROITE,
    )

    class Meta:
        db_table = "itineraire"

    def __str__(self) -> str:
        return f"{self.livraison_id} : {self.distance_km} km"


class TypeIncident(models.TextChoices):
    CLIENT_ABSENT = "client_absent", "Client absent"
    ECHEC_LIVRAISON = "echec_livraison", "Échec de livraison"
    PRODUIT_ENDOMMAGE = "produit_endommage", "Produit endommagé"
    AUTRE = "autre", "Autre"


class IncidentLivraison(ModeleBase):
    livraison = models.ForeignKey(
        Livraison,
        on_delete=models.CASCADE,
        related_name="incidents",
    )
    declare_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="incidents_declares",
    )
    type = models.CharField(max_length=20, choices=TypeIncident.choices)
    description = models.TextField(blank=True, default="")
    position = ChampPoint(null=True, blank=True)
    fichier = models.ForeignKey(
        Fichier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="incidents",
    )

    class Meta:
        db_table = "incident_livraison"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"{self.livraison_id} : {self.type}"

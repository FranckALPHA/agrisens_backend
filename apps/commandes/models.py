"""Modèles des commandes et de leurs lignes (SPEC-04, modèle 02 §2.3 et §3.1).

Montants en entiers FCFA, quantités en Decimal. Les copies figées (noms,
prix, adresses, positions, paramètres) rendent chaque commande insensible
aux modifications ultérieures (produits, tarifs).
"""

from commun.champs_geo import ChampPoint
from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models

from apps.produits.models import Produit


class StatutCommande(models.TextChoices):
    EN_ATTENTE = "en_attente", "En attente"
    ACCEPTEE = "acceptee", "Acceptée"
    PAIEMENT_EN_ATTENTE = "paiement_en_attente", "Paiement en attente"
    PAYEE = "payee", "Payée"
    PREPARATION = "preparation", "En préparation"
    REMISE_AU_LIVREUR = "remise_au_livreur", "Remise au livreur"
    LIVREE = "livree", "Livrée"
    REJETEE = "rejetee", "Rejetée"
    ANNULEE = "annulee", "Annulée"
    CLIENT_ABSENT = "client_absent", "Client absent"
    ECHEC_LIVRAISON = "echec_livraison", "Échec de livraison"
    REMBOURSEMENT_EN_ATTENTE = "remboursement_en_attente", "Remboursement en attente"
    REMBOURSEE = "remboursee", "Remboursée"


class Commande(ModeleBase):
    reference = models.CharField(max_length=20, unique=True)
    acheteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="achats",
    )
    agriculteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ventes",
    )
    statut = models.CharField(
        max_length=30,
        choices=StatutCommande.choices,
        default=StatutCommande.EN_ATTENTE,
    )
    adresse_livraison_texte = models.TextField()
    zone_livraison = models.CharField(
        max_length=200,
        default="",
        help_text="Zone affichée à l'agriculteur avant paiement (D-21)",
    )
    position_livraison = ChampPoint()
    position_recuperation = ChampPoint()
    distance_km = models.IntegerField()
    sous_total_fcfa = models.IntegerField()
    frais_livraison_fcfa = models.IntegerField()
    total_fcfa = models.IntegerField()
    commission_taux_pb = models.IntegerField(null=True, blank=True)
    commission_fcfa = models.IntegerField(null=True, blank=True)
    parametres_appliques = models.JSONField(default=dict)
    expire_le = models.DateTimeField(null=True, blank=True)
    motif_rejet = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "commande"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["acheteur", "statut"], name="commande_ach_statut_idx"),
            models.Index(fields=["agriculteur", "statut"], name="commande_agri_statut_idx"),
            models.Index(fields=["statut", "expire_le"], name="commande_statut_expire_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sous_total_fcfa__gte=0),
                name="commande_sous_total_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(frais_livraison_fcfa__gte=0),
                name="commande_frais_positifs",
            ),
            models.CheckConstraint(
                condition=models.Q(total_fcfa__gte=0),
                name="commande_total_positif",
            ),
        ]

    def __str__(self) -> str:
        return self.reference


class LigneCommande(ModeleBase):
    commande = models.ForeignKey(
        Commande,
        on_delete=models.CASCADE,
        related_name="lignes",
    )
    produit = models.ForeignKey(
        Produit,
        on_delete=models.PROTECT,
        related_name="lignes_commande",
    )
    nom_produit = models.CharField(max_length=150)
    unite = models.CharField(max_length=50)
    quantite = models.DecimalField(max_digits=12, decimal_places=3)
    prix_unitaire_fcfa = models.IntegerField()
    total_ligne_fcfa = models.IntegerField()

    class Meta:
        db_table = "ligne_commande"
        ordering = ["cree_le"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantite__gt=0),
                name="ligne_quantite_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nom_produit} × {self.quantite}"


class CompteurReference(ModeleBase):
    """Compteur annuel de références lisibles (CMD-AAAA-NNNNNN…)."""

    prefixe = models.CharField(max_length=10)
    annee = models.IntegerField()
    dernier_numero = models.IntegerField(default=0)

    class Meta:
        db_table = "compteur_reference"
        constraints = [
            models.UniqueConstraint(
                fields=["prefixe", "annee"],
                name="compteur_reference_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.prefixe}-{self.annee}-{self.dernier_numero:06d}"

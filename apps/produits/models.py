"""Modèles des produits et de leurs photos (SPEC-03, modèle 02 §2.2).

Stock disponible = `quantite_stock − quantite_reservee` (jamais stocké).
Le statut `disponible`/`epuise` est maintenu par le service ; `retire`
est une décision volontaire de l'agriculteur, sans suppression.
"""

from decimal import Decimal

from commun.modeles import ModeleBase
from django.db import models

from apps.exploitations.models import Exploitation
from apps.fichiers.models import Fichier


class StatutProduit(models.TextChoices):
    DISPONIBLE = "disponible", "Disponible"
    EPUISE = "epuise", "Épuisé"
    RETIRE = "retire", "Retiré"


class Produit(ModeleBase):
    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.PROTECT,
        related_name="produits",
    )
    nom = models.CharField(max_length=150)
    categorie = models.CharField(max_length=50)
    description = models.TextField(blank=True, default="")
    unite = models.CharField(max_length=50)
    prix_unitaire_fcfa = models.IntegerField()
    quantite_stock = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    quantite_reservee = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    statut = models.CharField(
        max_length=20,
        choices=StatutProduit.choices,
        default=StatutProduit.DISPONIBLE,
    )

    class Meta:
        db_table = "produit"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["statut"], name="produit_statut_idx"),
            models.Index(fields=["exploitation", "statut"], name="produit_exploit_statut_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantite_stock__gte=0),
                name="produit_stock_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(quantite_reservee__gte=0),
                name="produit_reserve_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(quantite_reservee__lte=models.F("quantite_stock")),
                name="produit_reserve_inferieur_stock",
            ),
            models.CheckConstraint(
                condition=models.Q(prix_unitaire_fcfa__gte=0),
                name="produit_prix_positif",
            ),
        ]

    def __str__(self) -> str:
        return self.nom

    @property
    def quantite_disponible(self) -> Decimal:
        """Stock commandable = physique moins réservé (jamais stocké)."""
        return self.quantite_stock - self.quantite_reservee


class ProduitPhoto(ModeleBase):
    produit = models.ForeignKey(
        Produit,
        on_delete=models.CASCADE,
        related_name="liens_photos",
    )
    fichier = models.ForeignKey(
        Fichier,
        on_delete=models.PROTECT,
        related_name="liens_produits",
    )
    ordre = models.IntegerField(default=0)

    class Meta:
        db_table = "produit_photo"
        ordering = ["ordre", "cree_le"]
        constraints = [
            models.UniqueConstraint(
                fields=["produit", "fichier"],
                name="produit_photo_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.produit_id} / {self.fichier_id} ({self.ordre})"

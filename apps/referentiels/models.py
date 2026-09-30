"""Modèles pour les référentiels de données."""

from commun.modeles import ModeleBase
from django.db import models


class Referentiel(ModeleBase):
    """Entité portant une valeur de référence stable (région, unité, culture, etc.)."""

    type = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Type de référentiel (region, unite, categorie_produit, etc.)",
    )
    code = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Identifiant code stable utilisé par les autres entités",
    )
    libelle = models.CharField(
        max_length=100,
        help_text="Libellé affiché aux utilisateurs",
    )
    ordre = models.IntegerField(
        default=0,
        help_text="Ordre de tri pour l'affichage",
    )
    actif = models.BooleanField(
        default=True,
        help_text="Indique si la valeur est disponible dans les formulaires",
    )

    class Meta:
        db_table = "referentiel"
        verbose_name = "Référentiel"
        verbose_name_plural = "Référentiels"
        unique_together = [("type", "code")]
        ordering = ["ordre", "libelle"]

    def __str__(self) -> str:
        return f"[{self.type}] {self.libelle} ({self.code})"

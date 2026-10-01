"""Modèles des profils professionnels ([[02-modele-donnees]] §2.1, prompt F-03 point 3).

- PROFIL_INGENIEUR et PROFIL_LIVREUR sont en 1–1 avec UTILISATEUR
  (`utilisateur_id` = PK/FK) ;
- PIECE_JUSTIFICATIVE rattache un fichier privé au dossier d'un ingénieur ;
- les colonnes `biographie`, `modalites` et `photo_id` répondent à la page
  ING-02 ([[09-dossier-frontend]] §6) que §2.1 ne décrit pas
  (hypothèse Q-36, voir journal).
"""

from typing import Any

from commun.champs_geo import ChampPoint
from commun.horloge import maintenant
from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class StatutValidation(models.TextChoices):
    EN_ATTENTE = "en_attente", "En attente"
    VALIDE = "valide", "Validé"
    REFUSE = "refuse", "Refusé"


class TypeJustificatif(models.TextChoices):
    DIPLOME = "diplome", "Diplôme"
    PIECE_IDENTITE = "piece_identite", "Pièce d'identité"
    AUTRE = "autre", "Autre"


class StatutJustificatif(models.TextChoices):
    SOUMISE = "soumise", "Soumise"
    ACCEPTEE = "acceptee", "Acceptée"
    REJETEE = "rejetee", "Rejetée"


class IngenieurProfil(models.Model):
    """Profil professionnel d'un ingénieur agronome, validé par un admin (D-13).

    1–1 avec UTILISATEUR : `utilisateur` est la PK/FK (02 §2.1), pas d'id
    séparé ; horodatages UTC comme `ModeleBase`.
    """

    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="profil_ingenieur",
        help_text="1–1 avec UTILISATEUR : utilisateur_id = PK/FK",
    )
    cree_le = models.DateTimeField(default=maintenant, editable=False)
    maj_le = models.DateTimeField(default=maintenant)
    specialites = models.JSONField(
        default=list,
        blank=True,
        help_text="Liste de codes du référentiel `specialite` (validés par le service)",
    )
    zone_centre = ChampPoint(
        null=True,
        blank=True,
        help_text="Centre de la zone d'intervention, POINT SRID 4326",
    )
    zone_rayon_km = models.IntegerField(
        null=True,
        blank=True,
        help_text="Rayon d'intervention en km, borné par le paramètre plateforme",
    )
    tarif_consultation_fcfa = models.IntegerField(
        null=True,
        blank=True,
        help_text="Prix de la consultation en FCFA (entier), hors déplacement",
    )
    disponible = models.BooleanField(default=False)
    region = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Région d'intervention (filtre GET /ingenieurs)",
    )
    statut_validation = models.CharField(
        max_length=20,
        choices=StatutValidation.choices,
        default=StatutValidation.EN_ATTENTE,
    )
    motif_refus = models.TextField(null=True, blank=True)
    valide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="validations_ingénieur",
    )
    valide_le = models.DateTimeField(null=True, blank=True)
    # Hypothèse Q-36 : champs exigés par la page ING-02 mais absents de 02 §2.1.
    biographie = models.TextField(null=True, blank=True)
    modalites = models.TextField(
        null=True,
        blank=True,
        help_text="Modalités d'intervention (déplacements, visio, créneaux…)",
    )
    photo = models.ForeignKey(
        "fichiers.Fichier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "profil_ingenieur"
        verbose_name = "Profil ingénieur"
        verbose_name_plural = "Profils ingénieurs"

    def __str__(self) -> str:
        return f"Profil ingénieur {self.utilisateur_id} [{self.statut_validation}]"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.maj_le = maintenant()
        super().save(*args, **kwargs)


class PieceJustificative(ModeleBase):
    """Document à l'appui de la candidature d'un ingénieur (fichier privé)."""

    ingenieur = models.ForeignKey(
        IngenieurProfil,
        on_delete=models.CASCADE,
        related_name="justificatifs",
    )
    type = models.CharField(
        max_length=20,
        choices=TypeJustificatif.choices,
        help_text="Nature du document",
    )
    fichier = models.ForeignKey(
        "fichiers.Fichier",
        on_delete=models.CASCADE,
        related_name="justificatifs",
        help_text="Fichier téléversé en contexte `justificatif`",
    )
    statut = models.CharField(
        max_length=20,
        choices=StatutJustificatif.choices,
        default=StatutJustificatif.SOUMISE,
    )

    class Meta:
        db_table = "piece_justificative"
        verbose_name = "Pièce justificative"
        verbose_name_plural = "Pièces justificatives"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"Justificatif {self.type} [{self.statut}] pour {self.ingenieur_id}"


class LivreurProfil(models.Model):
    """Profil livreur : disponibilité et dernière position connue (02 §2.1).

    1–1 avec UTILISATEUR : `utilisateur` est la PK/FK.
    """

    utilisateur = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="profil_livreur",
        help_text="1–1 avec UTILISATEUR : utilisateur_id = PK/FK",
    )
    cree_le = models.DateTimeField(default=maintenant, editable=False)
    maj_le = models.DateTimeField(default=maintenant)
    disponible = models.BooleanField(default=False)
    position = ChampPoint(
        null=True,
        blank=True,
        help_text="Dernière position, mise à jour en disponibilité ou en livraison",
    )
    position_maj_le = models.DateTimeField(null=True, blank=True)
    zone = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Zone d'intervention déclarée",
    )

    class Meta:
        db_table = "profil_livreur"
        verbose_name = "Profil livreur"
        verbose_name_plural = "Profils livreurs"

    def __str__(self) -> str:
        etat = "disponible" if self.disponible else "indisponible"
        return f"Profil livreur {self.utilisateur_id} [{etat}]"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.maj_le = maintenant()
        super().save(*args, **kwargs)

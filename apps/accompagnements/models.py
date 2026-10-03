"""Modèles de l'accompagnement agronomique ([[02-modele-donnees]] §2.5, §3.3).

- ACCOMPAGNEMENT : demande d'un agriculteur vers un ingénieur, en ligne ou
  en présentiel ; montants figés à la confirmation, jamais recalculés après ;
- AFFECTATION_INGENIEUR : historique des ingénieurs qui se sont succédés sur
  une même demande (décision client D-18, journal Q-29).

Argent en entiers FCFA, géométrie SRID 4326 (`commun/champs_geo`), horodatages
UTC (`ModeleBase`) — conventions techniques §2 et §3.
"""

from commun.champs_geo import ChampPoint
from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class Modalite(models.TextChoices):
    EN_LIGNE = "en_ligne", "En ligne"
    PRESENTIEL = "presentiel", "Présentiel"


class StatutAccompagnement(models.TextChoices):
    """Machine à états [[02-modele-donnees]] §3.3 — 7 statuts, rien de plus."""

    DEMANDEE = "demandee", "Demandée"
    ACCEPTEE = "acceptee", "Acceptée"
    REFUSEE = "refusee", "Refusée"
    ANNULEE = "annulee", "Annulée"
    PLANIFIEE = "planifiee", "Planifiée"
    TERMINEE = "terminee", "Terminée"
    MANQUEE = "manquee", "Manquée"


class MotifFinAffectation(models.TextChoices):
    """Motifs de fin d'affectation (02 §2.5) ; `changement` est l'hypothèse Q-44."""

    INDISPONIBILITE = "indisponibilite", "Indisponibilité"
    DESISTEMENT = "desistement", "Désistement"
    REFUS = "refus", "Refus"
    TERMINE = "termine", "Terminé"
    CHANGEMENT = "changement", "Changement d'ingénieur"


class AbsentRDV(models.TextChoices):
    """Qui ne s'est pas présenté au rendez-vous (journal Q-30)."""

    INGENIEUR = "ingenieur", "Ingénieur absent"
    AGRICULTEUR = "agriculteur", "Agriculteur absent"


class Accompagnement(ModeleBase):
    """Demande d'accompagnement humain dirigée vers un ingénieur (D-14, D-15)."""

    agriculteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="accompagnements_demandes",
    )
    exploitation = models.ForeignKey(
        "exploitations.Exploitation",
        on_delete=models.PROTECT,
        related_name="accompagnements",
    )
    ingenieur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accompagnements_affectes",
        help_text="Ingénieur actuellement affecté",
    )
    modalite = models.CharField(max_length=20, choices=Modalite.choices)
    motif = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    statut = models.CharField(
        max_length=20,
        choices=StatutAccompagnement.choices,
        default=StatutAccompagnement.DEMANDEE,
    )
    lieu_rdv = ChampPoint(
        null=True,
        blank=True,
        help_text="Point du rendez-vous en présentiel, SRID 4326",
    )
    lieu_rdv_texte = models.TextField(null=True, blank=True)
    lien_visio = models.URLField(
        null=True,
        blank=True,
        max_length=500,
        help_text="Lien de visioconférence en modalité « en ligne » (hypothèse Q-44)",
    )
    date_rdv_debut = models.DateTimeField(null=True, blank=True)
    date_rdv_fin = models.DateTimeField(null=True, blank=True)
    prix_consultation_fcfa = models.IntegerField(
        null=True,
        blank=True,
        help_text="Figé à la confirmation (02 §2.5)",
    )
    frais_deplacement_fcfa = models.IntegerField(
        null=True,
        blank=True,
        help_text="Figé à la confirmation, 0 si en ligne (02 §2.5)",
    )
    distance_km = models.IntegerField(
        null=True,
        blank=True,
        help_text="Aller simple, arrondie au km supérieur (02 §2.5, conventions §3)",
    )
    commission_consultation_fcfa = models.IntegerField(
        null=True,
        blank=True,
        help_text="Commission retenue sur la consultation, figée à la confirmation (Q-06)",
    )
    observation = models.TextField(
        null=True,
        blank=True,
        help_text="Compte rendu obligatoire de l'ingénieur à la clôture",
    )
    nb_rdv_manques = models.IntegerField(default=0)

    class Meta:
        db_table = "accompagnement"
        verbose_name = "Accompagnement"
        verbose_name_plural = "Accompagnements"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["agriculteur", "statut"], name="accomp_agri_statut_idx"),
            models.Index(fields=["ingenieur", "statut"], name="accomp_ing_statut_idx"),
            models.Index(fields=["exploitation"], name="accomp_exploit_idx"),
            models.Index(
                fields=["ingenieur", "date_rdv_debut"],
                name="accomp_ing_creneau_idx",
            ),
        ]
        constraints = [
            # « en ligne » n'a ni déplacement ni lieu présentiel.
            models.CheckConstraint(
                condition=(
                    ~models.Q(modalite=Modalite.EN_LIGNE)
                    | (
                        models.Q(frais_deplacement_fcfa__isnull=True)
                        | models.Q(frais_deplacement_fcfa=0)
                    )
                ),
                name="accompagnement_en_ligne_sans_deplacement",
            ),
        ]

    def __str__(self) -> str:
        return f"Accompagnement {self.id} [{self.statut}]"


class AffectationIngenieur(ModeleBase):
    """Historisation des affectations successives d'un accompagnement (D-18)."""

    accompagnement = models.ForeignKey(
        Accompagnement,
        on_delete=models.CASCADE,
        related_name="affectations",
    )
    ingenieur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="affectations",
    )
    debut = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    motif_fin = models.CharField(
        max_length=20,
        choices=MotifFinAffectation.choices,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "affectation_ingenieur"
        verbose_name = "Affectation ingénieur"
        verbose_name_plural = "Affectations ingénieurs"
        ordering = ["-debut"]
        indexes = [
            models.Index(fields=["accompagnement"], name="affectation_accomp_idx"),
            models.Index(fields=["ingenieur", "fin"], name="affectation_ing_fin_idx"),
        ]

    def __str__(self) -> str:
        return f"Affectation {self.ingenieur_id} → {self.accompagnement_id}"

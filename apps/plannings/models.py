"""Modèles SPEC-08 : GENERATION_IA + CORRECTION_IA ([[02-modele-donnees]] §2.5).

- `GENERATION_IA` : une génération (planning ou recommandation) produite par
  un fournisseur IA pour une exploitation donnée, avec l'**exact** payload
  envoyé (audit « aucune donnée personnelle » 01 §1ter) et la réponse
  originale **immuable** ;
- `CORRECTION_IA` : commentaires / corrigés apportés par un ingénieur
  affecté à un accompagnement actif sur l'exploitation, sans écraser la
  proposition IA (journal D-11).

Statut `en_cours` n'existe que le temps de l'appel synchrone au fournisseur ;
`reussie` consomme le quota mensuel, `echouee` ne consomme rien (prompt F-12
point 3, docs/08 SPEC-08 §tests unitaires « erreur technique non décomptée »).
"""

from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class TypeGeneration(models.TextChoices):
    PLANNING = "planning", "Planning cultural"
    RECOMMANDATION = "recommandation", "Recommandation agronomique"


class StatutGeneration(models.TextChoices):
    EN_COURS = "en_cours", "En cours"
    REUSSIE = "reussie", "Réussie"
    ECHOUEE = "echouee", "Échouée"


class GenerationIA(ModeleBase):
    """Une génération IA (planning ou recommandation) rattachée à une exploitation."""

    type = models.CharField(max_length=20, choices=TypeGeneration.choices)
    exploitation = models.ForeignKey(
        "exploitations.Exploitation",
        on_delete=models.PROTECT,
        related_name="generations_ia",
    )
    demandeur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="generations_ia_demandees",
        help_text="Agriculteur propriétaire qui a déclenché la génération.",
    )
    culture = models.CharField(max_length=100)
    periode_debut = models.DateField()
    periode_fin = models.DateField()
    entrees = models.JSONField(
        help_text=(
            "Payload exact envoyé au fournisseur IA (audit « aucune donnée "
            "personnelle », 01 §1ter). Construit par ServiceEntrees.construire()."
        )
    )
    reponse_originale = models.JSONField(
        null=True,
        blank=True,
        help_text="Réponse validée par le schéma Pydantic ; jamais mutée après `reussie`.",
    )
    fournisseur_ia = models.CharField(max_length=50, default="factice")
    modele_ia = models.CharField(max_length=100, blank=True, default="")
    version_prompt = models.CharField(max_length=20)
    statut = models.CharField(
        max_length=20,
        choices=StatutGeneration.choices,
        default=StatutGeneration.EN_COURS,
    )
    erreur = models.TextField(
        null=True,
        blank=True,
        help_text="Code + message lisible si statut = `echouee`.",
    )
    mois_quota = models.CharField(
        max_length=7,
        help_text="Clé `AAAA-MM` UTC de la génération, utilisée pour la remise à zéro mensuelle.",
    )
    cle_idempotence = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="En-tête `Idempotency-Key` de la requête POST (null si absent).",
    )

    class Meta:
        db_table = "generation_ia"
        verbose_name = "Génération IA"
        verbose_name_plural = "Générations IA"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(
                fields=["demandeur", "type", "mois_quota", "statut"],
                name="generationia_quota_idx",
            ),
            models.Index(
                fields=["exploitation", "type", "-cree_le"],
                name="generationia_exploit_idx",
            ),
            models.Index(
                fields=["demandeur", "cle_idempotence"],
                name="generationia_idem_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["demandeur", "cle_idempotence"],
                condition=models.Q(cle_idempotence__isnull=False),
                name="generationia_idempotence_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"Génération {self.type} [{self.statut}] #{self.id[:8]}"


class CorrectionIA(ModeleBase):
    """Commentaire ou corrigé d'un ingénieur ; la réponse originale reste intacte."""

    generation = models.ForeignKey(
        GenerationIA,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    ingenieur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="corrections_ia",
    )
    commentaire = models.TextField()
    contenu_corrige = models.JSONField(
        null=True,
        blank=True,
        help_text="Réponse complète corrigée ; peut rester null pour un simple avis.",
    )

    class Meta:
        db_table = "correction_ia"
        verbose_name = "Correction IA"
        verbose_name_plural = "Corrections IA"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["generation"], name="correctionia_gen_idx"),
            models.Index(fields=["ingenieur"], name="correctionia_ing_idx"),
        ]

    def __str__(self) -> str:
        return f"Correction #{self.id[:8]} de {self.ingenieur_id} sur {self.generation_id}"

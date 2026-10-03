"""Modèles des litiges : remboursements, signalements et évaluations.

SPEC-05 (remboursements) et SPEC-10 (signalements), modèle 02 §2.3
(EVALUATION), §2.4 (REMBOURSEMENT), §2.7 (SIGNALEMENT) et §3.5.

Argent en entiers FCFA (jamais de flottant). Aucun tarif en dur : le délai
de demande vient du service `parametres` (`remboursement.delai_demande_h`).
"""

from commun.modeles import ModeleBase
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.commandes.models import Commande
from apps.fichiers.models import Fichier
from apps.paiements.models import Paiement


class StatutRemboursement(models.TextChoices):
    """Machine à états 02 §3.5 (exécution manuelle Q-26)."""

    DEMANDE = "demande", "Demandé"
    EN_EXAMEN = "en_examen", "En examen"
    APPROUVE = "approuve", "Approuvé"
    REFUSE = "refuse", "Refusé"
    EN_COURS = "en_cours", "En cours"
    EFFECTUE = "effectue", "Effectué"
    ECHOUE = "echoue", "Échoué"


# Statuts dans lesquels une demande existe déjà (double demande refusée).
STATUTS_ACTIFS_REMBOURSEMENT = frozenset(
    {
        StatutRemboursement.DEMANDE,
        StatutRemboursement.EN_EXAMEN,
        StatutRemboursement.APPROUVE,
        StatutRemboursement.EN_COURS,
    }
)


class MotifRemboursement(models.TextChoices):
    PRODUIT_ENDOMMAGE = "produit_endommage", "Produit endommagé"
    PRODUIT_INCORRECT = "produit_incorrect", "Produit incorrect"
    NON_RECUS = "non_recu", "Non reçu"
    ANNULATION = "annulation", "Annulation"
    AUTRE = "autre", "Autre"


# Justificatif (photo/fichier) obligatoire pour ces motifs (07 §15.3).
MOTIFS_JUSTIFICATIF_OBLIGATOIRE = frozenset(
    {
        MotifRemboursement.PRODUIT_ENDOMMAGE,
        MotifRemboursement.PRODUIT_INCORRECT,
        MotifRemboursement.NON_RECUS,
    }
)


class CibleSignalement(models.TextChoices):
    UTILISATEUR = "utilisateur", "Utilisateur"
    MESSAGE = "message", "Message"
    PRODUIT = "produit", "Produit"
    COMMANDE = "commande", "Commande"
    LIVRAISON = "livraison", "Livraison"
    ACCOMPAGNEMENT = "accompagnement", "Accompagnement"


class StatutSignalement(models.TextChoices):
    OUVERT = "ouvert", "Ouvert"
    EN_EXAMEN = "en_examen", "En examen"
    CLOS_FONDE = "clos_fonde", "Closé — fondé"
    CLOS_NON_FONDE = "clos_non_fonde", "Closé — non fondé"


class Signalement(ModeleBase):
    """Signalement d'un problème sur une cible (02 §2.7).

    Les messages de chat s'ajouteront en F-13 ; la cible `message` est déjà
    prévue par la spec mais reste sans producteur de contenu en V1.
    """

    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="signalements_emis",
    )
    cible_type = models.CharField(max_length=20, choices=CibleSignalement.choices)
    cible_id = models.CharField(max_length=36)
    motif = models.CharField(max_length=50)
    description = models.TextField(blank=True, default="")
    statut = models.CharField(
        max_length=20, choices=StatutSignalement.choices, default=StatutSignalement.OUVERT
    )
    traite_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signalements_traites",
    )
    decision = models.TextField(null=True, blank=True)
    fichiers = models.ManyToManyField(
        Fichier,
        blank=True,
        related_name="signalements",
    )

    class Meta:
        db_table = "signalement"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["statut"], name="signalement_statut_idx"),
            models.Index(fields=["cible_type", "cible_id"], name="signalement_cible_idx"),
        ]

    def __str__(self) -> str:
        return f"Signalement {self.cible_type}:{self.cible_id} ({self.statut})"


class Remboursement(ModeleBase):
    """Demande de remboursement liée au paiement d'une commande (02 §2.4)."""

    paiement = models.ForeignKey(
        Paiement,
        on_delete=models.PROTECT,
        related_name="remboursements",
    )
    commande = models.ForeignKey(
        Commande,
        on_delete=models.PROTECT,
        related_name="remboursements",
    )
    demandeur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="remboursements_demandes",
    )
    signalement = models.ForeignKey(
        Signalement,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="remboursements",
    )
    motif = models.CharField(max_length=30, choices=MotifRemboursement.choices)
    montant_demande_fcfa = models.IntegerField()
    montant_accorde_fcfa = models.IntegerField(null=True, blank=True)
    statut = models.CharField(
        max_length=20,
        choices=StatutRemboursement.choices,
        default=StatutRemboursement.DEMANDE,
    )
    # Restauration du statut de commande si l'admin refuse (02 §3.1).
    statut_commande_origine = models.CharField(max_length=30)
    motif_decision = models.TextField(null=True, blank=True)
    decide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="remboursements_decides",
    )
    decide_le = models.DateTimeField(null=True, blank=True)
    reference_operateur = models.CharField(max_length=128, null=True, blank=True)
    justificatifs = models.ManyToManyField(
        Fichier,
        blank=True,
        related_name="remboursements_justificatifs",
    )

    class Meta:
        db_table = "remboursement"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["statut"], name="remboursement_statut_idx"),
            models.Index(fields=["demandeur", "statut"], name="remboursement_dem_statut_idx"),
            models.Index(fields=["commande"], name="remboursement_commande_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.commande_id} ({self.statut})"


class Evaluation(ModeleBase):
    """Évaluation d'une commande livrée (02 §2.3, décision D-31 / Q-12)."""

    commande = models.OneToOneField(
        Commande,
        on_delete=models.PROTECT,
        related_name="evaluation",
    )
    acheteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="evaluations_ecrites",
    )
    note_agriculteur = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    note_livreur = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    commentaire = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "evaluation"
        ordering = ["-cree_le"]
        constraints = [
            models.UniqueConstraint(fields=["commande"], name="evaluation_commande_unique"),
        ]

    def __str__(self) -> str:
        return f"Évaluation {self.commande_id} : {self.note_agriculteur}/5"

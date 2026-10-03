"""Modèles transverses d'administration : paramètres, audit et historique de statut.

Conventions techniques §2.8 et prompt F-00 point 7.
"""

from commun.horloge import maintenant
from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class ParametrePlateforme(ModeleBase):
    """Paramètre de configuration de la plateforme modifiable par super-admin."""

    cle = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Clé identifiant le paramètre (ex: commission.taux_pb)",
    )
    valeur = models.IntegerField(
        help_text="Valeur numérique entière (FCFA, points de base, heures, etc.)",
    )
    date_effet = models.DateTimeField(
        default=maintenant,
        help_text="Date à partir de laquelle la valeur s'applique",
    )
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parametres_modifies",
        help_text="Administrateur ayant défini la valeur",
    )
    motif = models.TextField(
        null=True,
        blank=True,
        help_text="Justification de la modification",
    )

    class Meta:
        db_table = "parametre_plateforme"
        verbose_name = "Paramètre plateforme"
        verbose_name_plural = "Paramètres plateforme"
        indexes = [
            models.Index(fields=["cle", "-date_effet"]),
        ]

    def __str__(self) -> str:
        return f"{self.cle} = {self.valeur}"


class HistoriqueStatut(ModeleBase):
    """Historique générique des transitions d'états des entités métier."""

    entite_type = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Type d'entité (commande, livraison, accompagnement, etc.)",
    )
    entite_id = models.CharField(
        max_length=36,
        db_index=True,
        help_text="Identifiant UUID de l'entité",
    )
    ancien_statut = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        help_text="Statut avant la transition",
    )
    nouveau_statut = models.CharField(
        max_length=40,
        help_text="Nouveau statut atteint",
    )
    acteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transitions_effectuees",
        help_text="Utilisateur à l'origine de la transition (null si système)",
    )
    motif = models.TextField(
        null=True,
        blank=True,
        help_text="Motif ou explication de la transition",
    )

    class Meta:
        db_table = "historique_statut"
        verbose_name = "Historique de statut"
        verbose_name_plural = "Historiques de statuts"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return (
            f"[{self.entite_type}:{self.entite_id}] {self.ancien_statut} -> {self.nouveau_statut}"
        )


class JournalAudit(ModeleBase):
    """Journal d'audit de toutes les actions administratives et sensibles."""

    acteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actions_audit",
        help_text="Utilisateur auteur de l'action",
    )
    action = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Action effectuée (ex: modifier_parametre, suspendre_compte)",
    )
    entite_type = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Type de ressource ciblée",
    )
    entite_id = models.CharField(
        max_length=36,
        null=True,
        blank=True,
        db_index=True,
        help_text="Identifiant de la ressource ciblée",
    )
    ancienne_valeur = models.JSONField(
        null=True,
        blank=True,
        help_text="État avant action",
    )
    nouvelle_valeur = models.JSONField(
        null=True,
        blank=True,
        help_text="État après action",
    )
    adresse_ip = models.CharField(
        max_length=45,
        null=True,
        blank=True,
        help_text="Adresse IP de la requête",
    )

    class Meta:
        db_table = "journal_audit"
        verbose_name = "Journal d'audit"
        verbose_name_plural = "Journaux d'audit"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"Audit {self.action} sur {self.entite_type} par {self.acteur}"


class OffreAbonnement(ModeleBase):
    """Offre d'abonnement **versionnée** : une nouvelle ligne par modification.

    Décision P-05 / D-04 : en V1 seule l'offre `gratuit` est utilisée (quotas IA),
    les offres payantes sont enregistrées mais non vendues. Le super-admin peut
    modifier les limites et le prix avec une `date_effet` ; la version en vigueur
    est la ligne active de plus grande `date_effet` ≤ maintenant. La souscription
    (ABONNEMENT) arrivera en F-17 et référencera la version exacte choisie.
    """

    code = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Code stable de l'offre (gratuit, standard, premium)",
    )
    nom = models.CharField(max_length=100)
    prix_mensuel_fcfa = models.IntegerField(default=0)
    quota_planning_mois = models.IntegerField(default=0)
    quota_recommandation_mois = models.IntegerField(default=0)
    fonctionnalites = models.JSONField(default=dict, blank=True)
    date_effet = models.DateTimeField(default=maintenant)
    active = models.BooleanField(default=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="offres_crees",
    )

    class Meta:
        db_table = "offre_abonnement"
        verbose_name = "Offre d'abonnement"
        verbose_name_plural = "Offres d'abonnement"
        ordering = ["code", "-date_effet"]
        indexes = [
            models.Index(fields=["code", "-date_effet"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.prix_mensuel_fcfa} FCFA)"

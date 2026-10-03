"""Modèles des paiements et des webhooks (SPEC-05, modèle 02 §2.4 et §3.4).

Un paiement est confirmé UNIQUEMENT par webhook signé (jamais par le retour
navigateur). `EVENEMENT_WEBHOOK` garantit l'idempotence : un même événement
externe n'est traité qu'une fois.
"""

from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class ObjetPaiement(models.TextChoices):
    COMMANDE = "commande", "Commande"
    ABONNEMENT = "abonnement", "Abonnement"
    ACCOMPAGNEMENT = "accompagnement", "Accompagnement"


class OperateurPaiement(models.TextChoices):
    MTN_MOMO = "mtn_momo", "MTN Mobile Money"
    ORANGE_MONEY = "orange_money", "Orange Money"


class StatutPaiement(models.TextChoices):
    INITIE = "initie", "Initié"
    EN_ATTENTE = "en_attente", "En attente"
    REUSSI = "reussi", "Réussi"
    ECHOUE = "echoue", "Échoué"
    EXPIRE = "expire", "Expiré"


class Paiement(ModeleBase):
    objet = models.CharField(max_length=20, choices=ObjetPaiement.choices)
    commande = models.ForeignKey(
        "commandes.Commande",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="paiements",
    )
    # F-07 : seul l'objet `commande` existe. La colonne `abonnement` deviendra
    # une clé étrangère en F-17 (abonnements), avec le CHECK « une seule FK
    # cohérente avec objet » du modèle 02 §4.
    abonnement = models.CharField(max_length=36, null=True, blank=True)
    # F-11 : `accompagnement` devient une FK nullable vers
    # `accompagnements.Accompagnement` (migration `0003_accompagnement_fk`).
    accompagnement = models.ForeignKey(
        "accompagnements.Accompagnement",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="paiements",
    )
    payeur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="paiements",
    )
    montant_fcfa = models.IntegerField()
    operateur = models.CharField(
        max_length=20, choices=OperateurPaiement.choices, null=True, blank=True
    )
    reference_interne = models.CharField(max_length=64, unique=True)
    reference_notchpay = models.CharField(max_length=128, unique=True, null=True, blank=True)
    cle_idempotence = models.CharField(max_length=64, unique=True)
    statut = models.CharField(
        max_length=20, choices=StatutPaiement.choices, default=StatutPaiement.INITIE
    )
    url_paiement = models.TextField(null=True, blank=True)
    confirme_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "paiement"
        ordering = ["-cree_le"]
        indexes = [
            models.Index(fields=["statut"], name="paiement_statut_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reference_interne} ({self.statut})"


class Reversement(ModeleBase):
    """Versement manuel d'un solde à un bénéficiaire (Q-11, 02 §2.4).

    L'admin effectue le virement Mobile Money **hors plateforme** puis enregistre
    ici la référence opérateur. Le solde est calculé (jamais stocké) par le
    service `soldes` ; un reversement ne peut excéder le solde disponible au
    moment de l'enregistrement. Aucun montant en dur, argent en entiers FCFA.
    """

    beneficiaire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reversements",
    )
    montant_fcfa = models.IntegerField()
    operateur = models.CharField(max_length=20, choices=OperateurPaiement.choices)
    reference_operateur = models.CharField(max_length=128)
    effectue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversements_enregistres",
    )
    effectue_le = models.DateTimeField()
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "reversement"
        ordering = ["-effectue_le"]
        indexes = [
            models.Index(fields=["beneficiaire"], name="reversement_benef_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(montant_fcfa__gt=0),
                name="reversement_montant_positif",
            ),
        ]

    def __str__(self) -> str:
        return f"Reversement {self.montant_fcfa} FCFA → {self.beneficiaire_id}"


class EvenementWebhook(ModeleBase):
    fournisseur = models.CharField(max_length=30)
    identifiant_externe = models.CharField(max_length=128, unique=True)
    type_evenement = models.CharField(max_length=60)
    signature_valide = models.BooleanField()
    charge_utile = models.JSONField(default=dict)
    traite_le = models.DateTimeField(null=True, blank=True)
    erreur = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "evenement_webhook"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"{self.fournisseur}:{self.identifiant_externe} ({self.type_evenement})"

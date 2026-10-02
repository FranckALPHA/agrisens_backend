"""Serializers des paiements (SPEC-05, partie paiement)."""

from rest_framework import serializers

from apps.paiements.models import OperateurPaiement, Paiement


class InitierPaiementSerializer(serializers.Serializer):
    """POST /commandes/{id}/paiements — opérateur + numéro Mobile Money."""

    operateur = serializers.ChoiceField(choices=OperateurPaiement.choices)
    telephone = serializers.CharField(required=False, allow_blank=True)


class PaiementSerializer(serializers.ModelSerializer):
    commande_reference = serializers.CharField(source="commande.reference", read_only=True)
    commande_statut = serializers.CharField(source="commande.statut", read_only=True)

    class Meta:
        model = Paiement
        fields = [
            "id",
            "objet",
            "commande",
            "commande_reference",
            "commande_statut",
            "montant_fcfa",
            "operateur",
            "reference_interne",
            "reference_notchpay",
            "statut",
            "url_paiement",
            "confirme_le",
            "cree_le",
        ]
        read_only_fields = fields

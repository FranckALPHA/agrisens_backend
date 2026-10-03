"""Serializers F-10 : soldes bénéficiaires et reversements (Q-11, SPEC-05)."""

from rest_framework import serializers

from apps.paiements.models import OperateurPaiement, Reversement


class SoldeDetailSerializer(serializers.Serializer):
    """GET /soldes/moi — décomposition du solde (02 §2.4, jamais stocké)."""

    gains_acquis = serializers.IntegerField()
    remboursements_imputes = serializers.IntegerField()
    deja_reverse = serializers.IntegerField()
    solde = serializers.IntegerField()


class SoldeLigneSerializer(serializers.Serializer):
    """GET /admin/soldes — un bénéficiaire au solde positif."""

    beneficiaire_id = serializers.CharField()
    nom = serializers.CharField()
    role = serializers.CharField()
    gains_acquis = serializers.IntegerField()
    remboursements_imputes = serializers.IntegerField()
    deja_reverse = serializers.IntegerField()
    solde = serializers.IntegerField()


class ReversementSerializer(serializers.ModelSerializer):
    """Ligne d'historique de reversement (route admin seule : référence visible)."""

    beneficiaire_id = serializers.CharField(read_only=True)
    beneficiaire_nom = serializers.CharField(source="beneficiaire.nom")
    effectue_par_nom = serializers.CharField(
        source="effectue_par.nom", allow_null=True, default=None
    )

    class Meta:
        model = Reversement
        fields = [
            "id",
            "beneficiaire_id",
            "beneficiaire_nom",
            "montant_fcfa",
            "operateur",
            "reference_operateur",
            "effectue_par_nom",
            "effectue_le",
            "note",
        ]
        read_only_fields = fields


class ReversementCreationSerializer(serializers.Serializer):
    """POST /admin/reversements — bénéficiaire, montant, opérateur, référence."""

    beneficiaire_id = serializers.CharField()
    montant_fcfa = serializers.IntegerField(min_value=1)
    operateur = serializers.ChoiceField(choices=OperateurPaiement.choices)
    reference_operateur = serializers.CharField(max_length=128)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_reference_operateur(self, valeur: str) -> str:
        if not valeur.strip():
            raise serializers.ValidationError("La référence de l'opérateur est obligatoire.")
        return valeur.strip()

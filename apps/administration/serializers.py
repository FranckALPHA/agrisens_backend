"""Serializers pour le module administration."""

from rest_framework import serializers

from apps.administration.models import HistoriqueStatut, JournalAudit, ParametrePlateforme


class ParametrePlateformeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParametrePlateforme
        fields = ["id", "cle", "valeur", "date_effet", "motif", "cree_le", "maj_le"]


class HistoriqueStatutSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoriqueStatut
        fields = [
            "id",
            "entite_type",
            "entite_id",
            "ancien_statut",
            "nouveau_statut",
            "motif",
            "cree_le",
        ]


class JournalAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = JournalAudit
        fields = [
            "id",
            "action",
            "entite_type",
            "entite_id",
            "ancienne_valeur",
            "nouvelle_valeur",
            "cree_le",
        ]

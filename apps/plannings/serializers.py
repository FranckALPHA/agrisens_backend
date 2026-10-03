"""Serializers DRF de la génération IA (SPEC-08)."""

from typing import Any

from rest_framework import serializers

from apps.plannings.models import (
    CorrectionIA,
    GenerationIA,
)


class PlanningCreationSerializer(serializers.Serializer):
    """POST /exploitations/{id}/plannings."""

    culture = serializers.CharField(max_length=100)
    periode_debut = serializers.DateField()
    periode_fin = serializers.DateField()
    infos_agronomiques = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        debut = attrs.get("periode_debut")
        fin = attrs.get("periode_fin")
        if debut and fin and fin < debut:
            raise serializers.ValidationError(
                {"periode_fin": "La date de fin doit suivre la date de début."}
            )
        return attrs


class RecommandationCreationSerializer(serializers.Serializer):
    """POST /exploitations/{id}/recommandations."""

    sujet = serializers.CharField(max_length=200)
    culture = serializers.CharField(
        max_length=100, required=False, allow_blank=True, allow_null=True
    )
    contexte = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CorrectionCreationSerializer(serializers.Serializer):
    """POST /generations-ia/{id}/corrections."""

    commentaire = serializers.CharField(max_length=4000)
    contenu_corrige = serializers.JSONField(required=False, allow_null=True)


class CorrectionIASerializer(serializers.ModelSerializer):
    ingenieur_nom = serializers.CharField(source="ingenieur.nom", read_only=True)

    class Meta:
        model = CorrectionIA
        fields = [
            "id",
            "ingenieur",
            "ingenieur_nom",
            "commentaire",
            "contenu_corrige",
            "cree_le",
        ]
        read_only_fields = ["id", "ingenieur", "ingenieur_nom", "cree_le"]


class GenerationIAListSerializer(serializers.ModelSerializer):
    """Vue légère pour l'historique d'une exploitation."""

    class Meta:
        model = GenerationIA
        fields = [
            "id",
            "type",
            "statut",
            "culture",
            "periode_debut",
            "periode_fin",
            "fournisseur_ia",
            "modele_ia",
            "version_prompt",
            "mois_quota",
            "cree_le",
        ]
        read_only_fields = fields


class GenerationIADetailSerializer(serializers.ModelSerializer):
    """Détail complet : payload, réponse originale immuable, corrections."""

    corrections = CorrectionIASerializer(many=True, read_only=True)
    avertissement = serializers.SerializerMethodField()

    class Meta:
        model = GenerationIA
        fields = [
            "id",
            "type",
            "exploitation",
            "demandeur",
            "culture",
            "periode_debut",
            "periode_fin",
            "entrees",
            "reponse_originale",
            "avertissement",
            "fournisseur_ia",
            "modele_ia",
            "version_prompt",
            "statut",
            "erreur",
            "mois_quota",
            "cle_idempotence",
            "corrections",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = fields

    def get_avertissement(self, objet: GenerationIA) -> str | None:
        reponse = objet.reponse_originale or {}
        if isinstance(reponse, dict):
            return reponse.get("avertissement")
        return None

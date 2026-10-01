"""Serializers du module profils (forme uniquement, le métier est dans services)."""

from typing import Any, cast

from commun.champs_geo import ChampGeoJSONSerializer
from rest_framework import serializers

from apps.fichiers.serializers import FichierSerializer
from apps.profils.models import (
    IngenieurProfil,
    LivreurProfil,
    PieceJustificative,
    TypeJustificatif,
)


class IngenieurProfilSerializer(serializers.ModelSerializer):
    """GET/PUT /ingenieurs/moi/profil — vue complète pour l'ingénieur lui-même."""

    zone_centre = ChampGeoJSONSerializer(types=("Point",), required=False, allow_null=True)
    justificatifs = serializers.SerializerMethodField()
    # Sérialisé via JustificatifSerializer (déclaré plus bas).

    class Meta:
        model = IngenieurProfil
        fields = [
            "specialites",
            "region",
            "zone_centre",
            "zone_rayon_km",
            "tarif_consultation_fcfa",
            "disponible",
            "biographie",
            "modalites",
            "photo_id",
            "statut_validation",
            "motif_refus",
            "valide_le",
            "justificatifs",
        ]
        read_only_fields = ["statut_validation", "motif_refus", "valide_le"]

    def get_justificatifs(self, obj: IngenieurProfil) -> list[dict[str, Any]]:
        pieces = JustificatifSerializer(obj.justificatifs.all(), many=True)
        return cast("list[dict[str, Any]]", pieces.data)


class JustificatifSerializer(serializers.ModelSerializer):
    """Pièce justificative avec son fichier (sans chemin de stockage)."""

    fichier = FichierSerializer(read_only=True)

    class Meta:
        model = PieceJustificative
        fields = ["id", "type", "statut", "fichier", "cree_le"]
        read_only_fields = fields


class JustificatifDepotSerializer(serializers.Serializer):
    """POST /ingenieurs/moi/justificatifs — fichier déjà téléversé (`fichier_id`)."""

    fichier_id = serializers.CharField(max_length=36)
    type = serializers.ChoiceField(choices=TypeJustificatif.choices)


class RefusIngenieurSerializer(serializers.Serializer):
    """POST /admin/ingenieurs/{id}/refuser — presence de `motif` documentée ;
    le contrôle métier (motif non vide → 422 `motif_obligatoire`) reste dans
    le service, seul point de vérité (conventions §3)."""

    motif = serializers.CharField(max_length=2000, allow_blank=True)


class IngenieurPublicSerializer(serializers.ModelSerializer):
    """GET /ingenieurs et /ingenieurs/{id} — profil public, sans téléphone (03)."""

    id = serializers.CharField(source="utilisateur_id", read_only=True)
    nom = serializers.CharField(source="utilisateur.nom", read_only=True)
    zone_centre = ChampGeoJSONSerializer(read_only=True)

    class Meta:
        model = IngenieurProfil
        fields = [
            "id",
            "nom",
            "specialites",
            "region",
            "zone_centre",
            "zone_rayon_km",
            "tarif_consultation_fcfa",
            "disponible",
            "biographie",
            "modalites",
            "photo_id",
        ]


class DisponibiliteLivreurSerializer(serializers.Serializer):
    """PUT /livreurs/moi/disponibilite — disponible + position optionnelle."""

    disponible = serializers.BooleanField()
    position = ChampGeoJSONSerializer(types=("Point",), required=False, allow_null=True)
    zone = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)


class LivreurProfilSerializer(serializers.ModelSerializer):
    """GET /livreurs/moi/disponibilite — état courant du profil livreur."""

    position = ChampGeoJSONSerializer(read_only=True, allow_null=True)

    class Meta:
        model = LivreurProfil
        fields = ["disponible", "position", "position_maj_le", "zone"]
        read_only_fields = fields

"""Serializers des commandes (SPEC-04)."""

from typing import Any

from commun.champs_geo import ChampGeoJSONSerializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.commandes.models import Commande, LigneCommande


class LigneCommandeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneCommande
        fields = [
            "produit",
            "nom_produit",
            "unite",
            "quantite",
            "prix_unitaire_fcfa",
            "total_ligne_fcfa",
        ]
        read_only_fields = fields


class LigneSaisieSerializer(serializers.Serializer):
    """Ligne du panier envoyée au devis / à la création."""

    produit_id = serializers.CharField()
    quantite = serializers.DecimalField(max_digits=12, decimal_places=3)


class DevisSerializer(serializers.Serializer):
    """POST /commandes/devis — lignes + adresse (enregistrée ou saisie)."""

    lignes = LigneSaisieSerializer(many=True, allow_empty=False)
    adresse_id = serializers.CharField(required=False)
    adresse_texte = serializers.CharField(required=False, allow_blank=True)
    position = ChampGeoJSONSerializer(types=("Point",), required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("adresse_id") and not (
            attrs.get("adresse_texte") and attrs.get("position")
        ):
            raise serializers.ValidationError(
                "Indiquez une adresse enregistrée (adresse_id) ou un texte + une position."
            )
        return attrs


class CreationCommandeSerializer(DevisSerializer):
    """POST /commandes — mêmes champs que le devis, avec écriture."""


class LigneHistoriqueSerializer(serializers.Serializer):
    ancien_statut = serializers.CharField(allow_null=True)
    nouveau_statut = serializers.CharField()
    motif = serializers.CharField(allow_null=True)
    acteur_nom = serializers.CharField(allow_null=True)
    cree_le = serializers.DateTimeField()


class CommandeListeSerializer(serializers.ModelSerializer):
    agriculteur_nom = serializers.CharField(source="agriculteur.nom", read_only=True)
    acheteur_nom = serializers.CharField(source="acheteur.nom", read_only=True)
    nombre_lignes = serializers.SerializerMethodField()

    @extend_schema_field(serializers.IntegerField())
    def get_nombre_lignes(self, obj: Commande) -> int:
        return obj.lignes.count()

    class Meta:
        model = Commande
        fields = [
            "id",
            "reference",
            "statut",
            "total_fcfa",
            "sous_total_fcfa",
            "frais_livraison_fcfa",
            "agriculteur_nom",
            "acheteur_nom",
            "nombre_lignes",
            "expire_le",
            "cree_le",
        ]


class CommandeDetailSerializer(serializers.ModelSerializer):
    lignes = LigneCommandeSerializer(many=True, read_only=True)
    agriculteur_nom = serializers.CharField(source="agriculteur.nom", read_only=True)
    acheteur_nom = serializers.CharField(source="acheteur.nom", read_only=True)
    adresse_livraison = serializers.SerializerMethodField()
    adresse_exacte_visible = serializers.SerializerMethodField()
    commission_taux_pb = serializers.IntegerField(read_only=True, allow_null=True)
    commission_fcfa = serializers.IntegerField(read_only=True, allow_null=True)

    @extend_schema_field(serializers.CharField())
    def get_adresse_livraison(self, obj: Commande) -> str:
        from apps.commandes.services import ServiceCommande

        utilisateur = self.context.get("utilisateur")
        return str(ServiceCommande.adresse_pour(obj, utilisateur)["texte"])

    @extend_schema_field(serializers.BooleanField())
    def get_adresse_exacte_visible(self, obj: Commande) -> bool:
        from apps.commandes.services import ServiceCommande

        utilisateur = self.context.get("utilisateur")
        return bool(ServiceCommande.adresse_pour(obj, utilisateur)["exacte"])

    class Meta:
        model = Commande
        fields = [
            "id",
            "reference",
            "statut",
            "lignes",
            "adresse_livraison",
            "adresse_exacte_visible",
            "zone_livraison",
            "distance_km",
            "sous_total_fcfa",
            "frais_livraison_fcfa",
            "total_fcfa",
            "commission_taux_pb",
            "commission_fcfa",
            "parametres_appliques",
            "expire_le",
            "motif_rejet",
            "agriculteur_nom",
            "acheteur_nom",
            "cree_le",
            "maj_le",
        ]


class MotifSerializer(serializers.Serializer):
    """Rejet d'une commande : motif obligatoire."""

    motif = serializers.CharField()

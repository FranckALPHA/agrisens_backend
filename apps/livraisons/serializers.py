"""Serializers des livraisons (SPEC-06)."""

from commun.champs_geo import ChampGeoJSONSerializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.livraisons.models import IncidentLivraison, Livraison, TypeIncident
from apps.paiements.models import OperateurPaiement


class ItineraireSerializer(serializers.Serializer):
    """Itinéraire : points GeoJSON + distance + durée (champs maison)."""

    point_depart = serializers.JSONField()
    point_arrivee = serializers.JSONField()
    distance_km = serializers.DecimalField(max_digits=8, decimal_places=2)
    duree_estimee_min = serializers.IntegerField(allow_null=True)
    source = serializers.CharField()  # type: ignore[assignment]


class LivraisonDisponibleSerializer(serializers.ModelSerializer):
    """Vue livreur avant acceptation : zone et distance, jamais d'adresse
    exacte ni de téléphone (D-21)."""

    commande_reference = serializers.CharField(source="commande.reference", read_only=True)
    frais_livraison_fcfa = serializers.IntegerField(
        source="commande.frais_livraison_fcfa", read_only=True
    )
    zone_livraison = serializers.CharField(source="commande.zone_livraison", read_only=True)
    distance_km = serializers.SerializerMethodField()
    duree_estimee_min = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DecimalField(max_digits=8, decimal_places=2))
    def get_distance_km(self, obj: Livraison) -> object:
        itineraire = getattr(obj, "itineraire", None)
        return itineraire.distance_km if itineraire else None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_duree_estimee_min(self, obj: Livraison) -> object:
        itineraire = getattr(obj, "itineraire", None)
        return itineraire.duree_estimee_min if itineraire else None

    class Meta:
        model = Livraison
        fields = [
            "id",
            "commande_reference",
            "statut",
            "numero_tentative",
            "frais_livraison_fcfa",
            "zone_livraison",
            "distance_km",
            "duree_estimee_min",
            "acceptee_le",
        ]


class LivraisonDetailSerializer(LivraisonDisponibleSerializer):
    """Vue détaillée : itinéraire + données sensibles déjà filtrées en amont."""

    itineraire = serializers.SerializerMethodField()
    livreur_nom = serializers.SerializerMethodField()
    adresse_exacte = serializers.SerializerMethodField()
    adresse_exacte_visible = serializers.SerializerMethodField()
    telephone_acheteur = serializers.SerializerMethodField()
    telephone_livreur = serializers.SerializerMethodField()
    position_livreur = serializers.SerializerMethodField()

    @extend_schema_field(ItineraireSerializer())
    def get_itineraire(self, obj: Livraison) -> object:
        itineraire = getattr(obj, "itineraire", None)
        if itineraire is None:
            return None
        depart = itineraire.point_depart
        arrivee = itineraire.point_arrivee
        return {
            "point_depart": depart.__geo_interface__
            if hasattr(depart, "__geo_interface__")
            else depart,
            "point_arrivee": arrivee.__geo_interface__
            if hasattr(arrivee, "__geo_interface__")
            else arrivee,
            "distance_km": str(itineraire.distance_km),
            "duree_estimee_min": itineraire.duree_estimee_min,
            "source": itineraire.source,
        }

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_livreur_nom(self, obj: Livraison) -> object:
        return self.context.get("sensibles", {}).get("livreur_nom")

    @extend_schema_field(serializers.CharField())
    def get_adresse_exacte(self, obj: Livraison) -> object:
        return self.context.get("sensibles", {}).get("adresse_exacte")

    @extend_schema_field(serializers.BooleanField())
    def get_adresse_exacte_visible(self, obj: Livraison) -> object:
        return self.context.get("sensibles", {}).get("adresse_exacte_visible", False)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_telephone_acheteur(self, obj: Livraison) -> object:
        return self.context.get("sensibles", {}).get("telephone_acheteur")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_telephone_livreur(self, obj: Livraison) -> object:
        return self.context.get("sensibles", {}).get("telephone_livreur")

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_position_livreur(self, obj: Livraison) -> object:
        return self.context.get("sensibles", {}).get("position_livreur")

    class Meta(LivraisonDisponibleSerializer.Meta):
        fields = [
            *LivraisonDisponibleSerializer.Meta.fields,
            "itineraire",
            "livreur_nom",
            "adresse_exacte",
            "adresse_exacte_visible",
            "telephone_acheteur",
            "telephone_livreur",
            "position_livreur",
            "tentatives_code_echouees",
            "demarree_le",
            "livree_le",
        ]


class CodeRemiseSerializer(serializers.Serializer):
    """Saisie du code par le livreur : exactement 6 chiffres."""

    code = serializers.RegexField(r"^[0-9]{6}$", max_length=6, min_length=6)


class IncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncidentLivraison
        fields = ["id", "type", "description", "fichier", "cree_le"]
        read_only_fields = ["id", "cree_le"]


class IncidentCreationSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=TypeIncident.choices)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    fichier_id = serializers.CharField(required=False, allow_null=True, default=None)


class ReprogrammerSerializer(serializers.Serializer):
    """Reprogrammation : nouvelle adresse optionnelle + paiement si frais dus."""

    adresse_id = serializers.CharField(required=False, allow_null=True, default=None)
    # Choix unique `OperateurPaiement` (F-14 : supprime la collision d'enum OpenAPI).
    operateur = serializers.ChoiceField(
        choices=OperateurPaiement.choices,
        required=False,
        allow_null=True,
        default=None,
    )
    telephone = serializers.CharField(required=False, allow_blank=True, default="")


class PositionLivreurSerializer(serializers.Serializer):
    """POST /livraisons/{id}/position — GeoJSON Point pendant la course."""

    position = ChampGeoJSONSerializer(types=("Point",))

"""Serializers DRF de l'accompagnement (SPEC-07)."""

from typing import Any

from commun.champs_geo import ChampGeoJSONSerializer
from rest_framework import serializers

from apps.accompagnements.models import (
    AbsentRDV,
    Accompagnement,
    AffectationIngenieur,
    Modalite,
    StatutAccompagnement,
)


class DevisCreationSerializer(serializers.Serializer):
    """POST /accompagnements/devis — calcule le prix sans rien créer."""

    exploitation_id = serializers.CharField(max_length=36)
    ingenieur_id = serializers.CharField(max_length=36)
    modalite = serializers.ChoiceField(choices=Modalite.choices)


class DevisReponseSerializer(serializers.Serializer):
    consultation_fcfa = serializers.IntegerField()
    deplacement_fcfa = serializers.IntegerField()
    distance_km_aller = serializers.IntegerField(allow_null=True)
    distance_exacte_km = serializers.FloatField(allow_null=True)
    commission_consultation_fcfa = serializers.IntegerField()
    taux_commission_pb = serializers.IntegerField()
    total_fcfa = serializers.IntegerField()
    gain_ingénieur_fcfa = serializers.IntegerField()


class AccompagnementCreationSerializer(serializers.Serializer):
    """POST /accompagnements — crée une demande en `demandee`."""

    exploitation_id = serializers.CharField(max_length=36)
    ingenieur_id = serializers.CharField(max_length=36)
    modalite = serializers.ChoiceField(choices=Modalite.choices)
    motif = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class RefusSerializer(serializers.Serializer):
    """POST /{id}/refuser — motif obligatoire (règle dans le service)."""

    motif = serializers.CharField(max_length=1000, allow_blank=True)


class PlanifierSerializer(serializers.Serializer):
    """POST /{id}/planifier — date, durée, lieu ou lien."""

    date_debut = serializers.DateTimeField()
    date_fin = serializers.DateTimeField()
    lieu_rdv = ChampGeoJSONSerializer(required=False, allow_null=True, types=("Point",))
    lieu_rdv_texte = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    lien_visio = serializers.URLField(required=False, allow_blank=True, allow_null=True)


class ConfirmerSerializer(serializers.Serializer):
    """POST /{id}/confirmer — l'agriculteur accepte le montant et paie."""

    operateur = serializers.ChoiceField(choices=(("mtn_momo", "MTN"), ("orange_money", "Orange")))
    telephone = serializers.CharField(max_length=20, required=False, allow_blank=True)


class ChangerIngenieurSerializer(serializers.Serializer):
    """POST /{id}/changer-ingenieur — historisé (Q-29)."""

    ingenieur_id = serializers.CharField(max_length=36)


class MarquerManqueSerializer(serializers.Serializer):
    """POST /{id}/marquer-manque — qui est absent (Q-30)."""

    absent = serializers.ChoiceField(choices=AbsentRDV.choices)


class TerminerSerializer(serializers.Serializer):
    """POST /{id}/terminer — observation obligatoire."""

    observation = serializers.CharField(allow_blank=True)


class AffectationIngenieurSerializer(serializers.ModelSerializer):
    ingenieur_nom = serializers.CharField(source="ingenieur.nom", read_only=True)

    class Meta:
        model = AffectationIngenieur
        fields = ["id", "ingenieur", "ingenieur_nom", "debut", "fin", "motif_fin", "cree_le"]
        read_only_fields = fields


class AccompagnementSerializer(serializers.ModelSerializer):
    """Lecture publique d'un accompagnement (liste + détail)."""

    agriculteur_nom = serializers.CharField(source="agriculteur.nom", read_only=True)
    ingenieur_nom = serializers.CharField(
        source="ingenieur.nom", read_only=True, allow_null=True
    )
    exploitation_nom = serializers.CharField(
        source="exploitation.nom", read_only=True, allow_null=True
    )
    lieu_rdv = ChampGeoJSONSerializer(required=False, allow_null=True, types=("Point",))

    class Meta:
        model = Accompagnement
        fields = [
            "id",
            "agriculteur",
            "agriculteur_nom",
            "ingenieur",
            "ingenieur_nom",
            "exploitation",
            "exploitation_nom",
            "modalite",
            "motif",
            "description",
            "statut",
            "lieu_rdv",
            "lieu_rdv_texte",
            "lien_visio",
            "date_rdv_debut",
            "date_rdv_fin",
            "prix_consultation_fcfa",
            "frais_deplacement_fcfa",
            "distance_km",
            "commission_consultation_fcfa",
            "observation",
            "nb_rdv_manques",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = fields


class AccompagnementDetailSerializer(AccompagnementSerializer):
    """Détail + historique des transitions et affectations."""

    historique = serializers.SerializerMethodField()
    affectations = AffectationIngenieurSerializer(many=True, read_only=True)

    def get_historique(self, obj: Accompagnement) -> list[dict[str, Any]]:
        from apps.accompagnements.services import ServiceAccompagnement

        lignes = ServiceAccompagnement.historique(obj)
        return [
            {
                "id": ligne.id,
                "ancien_statut": ligne.ancien_statut,
                "nouveau_statut": ligne.nouveau_statut,
                "acteur_id": ligne.acteur_id,
                "motif": ligne.motif,
                "cree_le": ligne.cree_le.isoformat(),
            }
            for ligne in lignes
        ]

    class Meta(AccompagnementSerializer.Meta):
        fields = AccompagnementSerializer.Meta.fields + ["historique", "affectations"]
        read_only_fields = fields


# Réexport pour typer l'enveloppe de liste.
STATUTS_ENUM = StatutAccompagnement.values

"""Serializers de la feature F-10 : paramètres, offres, audit, référentiels.

Représentations API de l'administration (SPEC-11). Les valeurs d'audit
anciennes/nouvelles sont exposées telles que journalisées (aucune donnée
sensible : codes, valeurs entières, motifs).
"""

from typing import Any

from rest_framework import serializers

# --- Paramètres (ADM-04) ---------------------------------------------------


class ValeurPlanifieeSerializer(serializers.Serializer):
    """Planification d'une valeur à date d'effet future."""

    valeur = serializers.IntegerField()
    date_effet = serializers.DateTimeField()
    motif = serializers.CharField(allow_null=True)


class ParametreAdminSerializer(serializers.Serializer):
    """Ligne ADM-04 : un paramètre avec sa valeur en vigueur et ses bornes."""

    cle = serializers.CharField()
    groupe = serializers.CharField()
    valeur = serializers.IntegerField()
    minimum = serializers.IntegerField()
    maximum = serializers.IntegerField()
    public = serializers.BooleanField()
    planifiees = ValeurPlanifieeSerializer(many=True)


class ParametreCreationSerializer(serializers.Serializer):
    """POST /admin/parametres/{cle} — valeur, date d'effet, motif obligatoire."""

    valeur = serializers.IntegerField()
    date_effet = serializers.DateTimeField(required=False)
    motif = serializers.CharField(max_length=500)

    def validate_motif(self, valeur: str) -> str:
        if not valeur.strip():
            raise serializers.ValidationError("Le motif est obligatoire.")
        return valeur.strip()


class ParametreHistoriqueSerializer(serializers.Serializer):
    """Historique d'un paramètre (02 §2.8 : une ligne par changement)."""

    id = serializers.CharField()
    valeur = serializers.IntegerField()
    date_effet = serializers.DateTimeField()
    motif = serializers.CharField(allow_null=True)
    auteur_nom = serializers.CharField(allow_null=True, allow_blank=True)
    cree_le = serializers.DateTimeField()


# --- Offres d'abonnement (P-05, F-10 point 2) ------------------------------


class VersionOffreSerializer(serializers.Serializer):
    id = serializers.CharField()
    prix_mensuel_fcfa = serializers.IntegerField()
    quota_planning_mois = serializers.IntegerField()
    quota_recommandation_mois = serializers.IntegerField()
    date_effet = serializers.DateTimeField()
    active = serializers.BooleanField()


class OffreAdminSerializer(serializers.Serializer):
    """GET /admin/offres — version en vigueur + versions historiques par code."""

    code = serializers.CharField()
    nom = serializers.CharField()
    prix_mensuel_fcfa = serializers.IntegerField(allow_null=True)
    quota_planning_mois = serializers.IntegerField(allow_null=True)
    quota_recommandation_mois = serializers.IntegerField(allow_null=True)
    fonctionnalites = serializers.DictField(child=serializers.JSONField(), required=False)
    versions = VersionOffreSerializer(many=True)


class OffreCreationSerializer(serializers.Serializer):
    """POST /admin/offres — nouvelle version d'une offre existante ou nouvelle."""

    code = serializers.CharField(max_length=30)
    nom = serializers.CharField(max_length=100)
    prix_mensuel_fcfa = serializers.IntegerField(min_value=0)
    quota_planning_mois = serializers.IntegerField(min_value=0)
    quota_recommandation_mois = serializers.IntegerField(min_value=0)
    fonctionnalites = serializers.DictField(
        child=serializers.JSONField(), required=False
    )
    date_effet = serializers.DateTimeField(required=False)

    def validate_code(self, valeur: str) -> str:
        return valeur.strip().lower()


# --- Audit (ADM-05) ---------------------------------------------------------


class AuditEntreeSerializer(serializers.Serializer):
    """Ligne du journal d'audit, détaillée pour l'admin (ancienne/nouvelle)."""

    id = serializers.CharField()
    action = serializers.CharField()
    entite_type = serializers.CharField()
    entite_id = serializers.CharField(allow_null=True, allow_blank=True)
    acteur_nom = serializers.SerializerMethodField()
    ancienne_valeur = serializers.JSONField(allow_null=True)
    nouvelle_valeur = serializers.JSONField(allow_null=True)
    cree_le = serializers.DateTimeField()

    def get_acteur_nom(self, obj: Any) -> str | None:
        return obj.acteur.nom if obj.acteur else None


# --- Référentiels (gestion super-admin) --------------------------------------


class ReferentielAdminSerializer(serializers.Serializer):
    """Ligne de gestion : code stable + libellé + drapeau actif.

    Le champ `type` est porteur à la lecture ; à l'écriture il vient de l'URL
    (/admin/referentiels/{type}), donc non obligatoire dans le corps.
    """

    id = serializers.CharField(read_only=True)
    type = serializers.CharField(required=False)
    code = serializers.CharField()
    libelle = serializers.CharField()
    ordre = serializers.IntegerField(required=False, default=0)
    actif = serializers.BooleanField(read_only=True)


class ReferentielRenommageSerializer(serializers.Serializer):
    libelle = serializers.CharField(max_length=100)

    def validate_libelle(self, valeur: str) -> str:
        if not valeur.strip():
            raise serializers.ValidationError("Le nouveau libellé est obligatoire.")
        return valeur.strip()

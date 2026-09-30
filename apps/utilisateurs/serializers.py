"""Serializers du module utilisateurs (forme et validation uniquement).

La logique métier est dans `services.py` ; ici : validation des champs,
longueur minimale du mot de passe (conventions §6 : 8 caractères),
choix des rôles inscriptibles et format GeoJSON des positions.
"""

from commun.champs_geo import ChampGeoJSONSerializer
from rest_framework import serializers

from apps.utilisateurs.models import Adresse, Utilisateur
from apps.utilisateurs.services import ROLES_INSCRIVABLES


class InscriptionSerializer(serializers.Serializer):
    """POST /auth/inscription — rôles agriculteur, acheteur, ingénieur, livreur."""

    nom = serializers.CharField(max_length=150)
    telephone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=255)
    mot_de_passe = serializers.CharField(min_length=8, write_only=True)
    role = serializers.ChoiceField(choices=sorted(ROLES_INSCRIVABLES))
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate_mot_de_passe(self, valeur: str) -> str:
        if len(valeur) < 8:
            raise serializers.ValidationError(
                "Le mot de passe doit comporter au moins 8 caractères."
            )
        return valeur


class ConnexionSerializer(serializers.Serializer):
    """POST /auth/connexion — téléphone E.164 ou email + mot de passe."""

    identifiant = serializers.CharField(max_length=255)
    mot_de_passe = serializers.CharField(write_only=True, trim_whitespace=False)


class DemandeReinitialisationSerializer(serializers.Serializer):
    """POST /auth/demande-reinitialisation (Q-23) — téléphone du compte."""

    telephone = serializers.CharField(max_length=20)


class UtilisateurSerializer(serializers.ModelSerializer):
    """Représentation de « moi » pour tout utilisateur connecté."""

    class Meta:
        model = Utilisateur
        fields = [
            "id",
            "nom",
            "telephone",
            "email",
            "role",
            "statut_compte",
            "region",
            "doit_changer_mot_de_passe",
            "reinitialisation_demandee_le",
            "derniere_connexion_le",
            "cree_le",
        ]
        read_only_fields = fields


class ReponseSessionSerializer(serializers.Serializer):
    """Corps commun de /auth/inscription, /connexion et /rafraichir.

    Sert uniquement à documenter le schéma OpenAPI ; la réponse est construite
    dans `views._reponse_session`.
    """

    utilisateur = UtilisateurSerializer(read_only=True)
    jeton_acces = serializers.CharField(read_only=True)
    expire_le = serializers.DateTimeField(read_only=True)


class ReponseMessageSerializer(serializers.Serializer):
    """Réponse neutre uniforme (demande de réinitialisation, Q-23)."""

    message = serializers.CharField(read_only=True)


class MiseAJourMoiSerializer(serializers.Serializer):
    """PATCH /utilisateurs/moi — nom, email, région, téléphone ; jamais le rôle."""

    nom = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    region = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    telephone = serializers.CharField(max_length=20, required=False)

    def validate_nom(self, valeur: str) -> str:
        valeur = valeur.strip()
        if not valeur:
            raise serializers.ValidationError("Le nom est obligatoire.")
        return valeur


class MotDePasseSerializer(serializers.Serializer):
    """PUT /utilisateurs/moi/mot-de-passe."""

    mot_de_passe_actuel = serializers.CharField(write_only=True, trim_whitespace=False)
    nouveau_mot_de_passe = serializers.CharField(
        write_only=True, min_length=8, trim_whitespace=False
    )

    def validate_nouveau_mot_de_passe(self, valeur: str) -> str:
        if len(valeur) < 8:
            raise serializers.ValidationError(
                "Le mot de passe doit comporter au moins 8 caractères."
            )
        return valeur


class AdresseSerializer(serializers.ModelSerializer):
    """ADRESSE — position en GeoJSON Point (conventions §3)."""

    position = ChampGeoJSONSerializer(types=("Point",))

    class Meta:
        model = Adresse
        fields = [
            "id",
            "libelle",
            "description",
            "position",
            "ville",
            "region",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = ["id", "cree_le", "maj_le"]

    def validate_libelle(self, valeur: str) -> str:
        valeur = valeur.strip()
        if not valeur:
            raise serializers.ValidationError("Le libellé de l'adresse est obligatoire.")
        return valeur

    def validate_ville(self, valeur: str) -> str:
        valeur = valeur.strip()
        if not valeur:
            raise serializers.ValidationError("La ville est obligatoire.")
        return valeur


class AdresseCreationSerializer(AdresseSerializer):
    class Meta(AdresseSerializer.Meta):
        read_only_fields = ["id", "cree_le", "maj_le"]


class AdresseModificationSerializer(AdresseSerializer):
    """PATCH : tous les champs facultatifs."""

    libelle = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    position = ChampGeoJSONSerializer(types=("Point",), required=False)
    ville = serializers.CharField(max_length=100, required=False)
    region = serializers.CharField(max_length=100, required=False)

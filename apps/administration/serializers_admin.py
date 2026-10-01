"""Serializers d'administration des utilisateurs (prompt F-03 backend point 4).

Représentations destinées aux admins : le téléphone complet n'est visible
que dans le détail, jamais dans les listes (conventions §6, minimisation).
"""

from rest_framework import serializers

from apps.utilisateurs.models import RoleUtilisateur, Utilisateur

# Constante partagée avec le registre OpenAPI (ENUM_NAME_OVERRIDES) : le champ
# `role` de ce serializer porte un sous-ensemble de choix, donc un enum distinct.
CHOIX_ROLES_ADMIN = [RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN]


class UtilisateurAdminListeSerializer(serializers.ModelSerializer):
    """Ligne de GET /admin/utilisateurs — identité, rôle, statut, drapeaux."""

    id = serializers.CharField(read_only=True)
    telephone = serializers.SerializerMethodField(
        help_text="Masqué en liste (conventions §6) ; complet dans le détail."
    )

    class Meta:
        model = Utilisateur
        fields = [
            "id",
            "nom",
            "telephone",
            "role",
            "statut_compte",
            "region",
            "doit_changer_mot_de_passe",
            "reinitialisation_demandee_le",
            "derniere_connexion_le",
            "cree_le",
        ]
        read_only_fields = fields

    def get_telephone(self, obj: Utilisateur) -> str:
        telephone = obj.telephone or ""
        if len(telephone) <= 7:
            return telephone
        return f"{telephone[:5]}{'*' * (len(telephone) - 8)}{telephone[-3:]}"


class JustificatifAdminSerializer(serializers.Serializer):
    """Justificatif vu par l'admin :métadonnées + accès au fichier (03 : adm)."""

    id = serializers.CharField()
    type = serializers.CharField()
    statut = serializers.CharField()
    fichier_id = serializers.CharField()
    nom_original = serializers.CharField(source="fichier.nom_original")
    type_mime = serializers.CharField(source="fichier.type_mime")
    cree_le = serializers.DateTimeField()


class ProfilIngenieurAdminSerializer(serializers.Serializer):
    """Dossier de candidature complet pour la décision admin (D-13)."""

    statut_validation = serializers.CharField()
    motif_refus = serializers.CharField(allow_null=True)
    valide_le = serializers.DateTimeField(allow_null=True)
    specialites = serializers.ListField(child=serializers.CharField())
    tarif_consultation_fcfa = serializers.IntegerField(allow_null=True)
    disponible = serializers.BooleanField()
    justificatifs = JustificatifAdminSerializer(many=True)


class UtilisateurAdminDetailSerializer(serializers.ModelSerializer):
    """GET /admin/utilisateurs/{id} — toutes les infos nécessaires à l'admin."""

    profil_ingenieur = serializers.SerializerMethodField()

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
            "profil_ingenieur",
        ]
        read_only_fields = fields

    def get_profil_ingenieur(self, obj: Utilisateur) -> dict | None:
        profil = getattr(obj, "profil_ingenieur", None)
        if profil is None:
            return None
        return ProfilIngenieurAdminSerializer(
            {
                "statut_validation": profil.statut_validation,
                "motif_refus": profil.motif_refus,
                "valide_le": profil.valide_le,
                "specialites": profil.specialites,
                "tarif_consultation_fcfa": profil.tarif_consultation_fcfa,
                "disponible": profil.disponible,
                "justificatifs": list(profil.justificatifs.select_related("fichier")),
            }
        ).data


class SuspendreSerializer(serializers.Serializer):
    """POST /admin/utilisateurs/{id}/suspendre — motif obligatoire (affiché au user)."""

    motif = serializers.CharField(max_length=500)

    def validate_motif(self, valeur: str) -> str:
        valeur = valeur.strip()
        if not valeur:
            raise serializers.ValidationError("Le motif de suspension est obligatoire.")
        return valeur


class MotDePasseTemporaireSerializer(serializers.Serializer):
    """Réponse du mot de passe temporaire (Q-23) — affiché une seule fois."""

    mot_de_passe_temporaire = serializers.CharField(read_only=True)
    doit_changer_mot_de_passe = serializers.BooleanField(read_only=True)
    message = serializers.CharField(read_only=True)


class CreationAdminSerializer(serializers.Serializer):
    """POST /admin/utilisateurs (super-admin) — création d'un compte admin."""

    nom = serializers.CharField(max_length=150)
    telephone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=CHOIX_ROLES_ADMIN, default=RoleUtilisateur.ADMIN)
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)


class CompteursAdminSerializer(serializers.Serializer):
    """GET /admin/utilisateurs/compteurs — bandeaux ADM-01 (F-03 frontend point 5)."""

    ingenieurs_en_attente = serializers.IntegerField()
    reinitialisations_en_attente = serializers.IntegerField()
    comptes_suspendus = serializers.IntegerField()

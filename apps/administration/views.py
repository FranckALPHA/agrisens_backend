"""Vues API pour l'administration, les paramètres et la gestion des utilisateurs."""

from typing import cast

from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration.serializers_admin import (
    CompteursAdminSerializer,
    CreationAdminSerializer,
    MotDePasseTemporaireSerializer,
    SuspendreSerializer,
    UtilisateurAdminDetailSerializer,
    UtilisateurAdminListeSerializer,
)
from apps.administration.services import parametres
from apps.administration.services_admin import ROLES_ADMIN, admin_utilisateurs
from apps.utilisateurs.models import Utilisateur


def _porteur(request: Request) -> Utilisateur:
    """Utilisateur authentifié ; les permissions appelées avant la vue
    garantissent un administrateur actif, ce cast informe mypy."""
    return cast("Utilisateur", request.user)


def _adresse_ip(request: Request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


# Enveloppes paginées documentées pour l'OpenAPI (APIView + reponse_liste).
LISTE_UTILISATEURS = schema_enveloppe_liste(
    "ListeUtilisateursAdmin", UtilisateurAdminListeSerializer
)
LISTE_DEMANDES_REINITIALISATION = schema_enveloppe_liste(
    "ListeDemandesReinitialisation", UtilisateurAdminListeSerializer
)


class VueParametresPublics(APIView):
    """Exposition publique des paramètres configurés (tarifs livraison, bornes, etc.)."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Obtenir les paramètres publics de la plateforme",
        description=(
            "Retourne la liste des paramètres nécessaires aux clients "
            "(frais de livraison, plafonds, tolérances, limites de fichiers)."
        ),
        responses={
            200: OpenApiResponse(
                description="Dictionnaire des paramètres publics avec leurs valeurs courantes"
            )
        },
    )
    def get(self, request: Request) -> Response:
        valeurs = parametres.lister_publics()
        return Response(valeurs)


class VueListeUtilisateursAdmin(APIView):
    """GET /admin/utilisateurs — liste ; POST — création d'admin (sadm seul, 403 sinon)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Lister les utilisateurs (admin)",
        operation_id="admin_utilisateurs_lister",
        parameters=[
            OpenApiParameter("role", str, description="Filtre par rôle"),
            OpenApiParameter("statut", str, description="Filtre par statut de compte"),
            OpenApiParameter("recherche", str, description="Nom ou téléphone"),
        ],
        responses={200: LISTE_UTILISATEURS},
    )
    def get(self, request: Request) -> Response:
        queryset = admin_utilisateurs.lister(
            role=request.query_params.get("role") or None,
            statut=request.query_params.get("statut") or None,
            recherche=request.query_params.get("recherche") or None,
        )
        return reponse_liste(request, queryset, UtilisateurAdminListeSerializer)

    @extend_schema(
        summary="Créer un compte d'administration",
        description=(
            "Réservé au super-admin (admin et super_admin ne s'inscrivent pas, "
            "02 §2.1). Le mot de passe temporaire n'est renvoyé qu'une fois."
        ),
        request=CreationAdminSerializer,
        responses={
            201: MotDePasseTemporaireSerializer,
            403: OpenApiResponse(description="permission_refusee"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = CreationAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        utilisateur, mdp = admin_utilisateurs.creer_admin(
            _porteur(request),
            nom=donnees["nom"],
            telephone=donnees["telephone"],
            role=donnees["role"],
            email=donnees.get("email"),
            region=donnees.get("region"),
            adresse_ip=_adresse_ip(request),
        )
        return Response(
            MotDePasseTemporaireSerializer(
                {
                    "mot_de_passe_temporaire": mdp,
                    "doit_changer_mot_de_passe": utilisateur.doit_changer_mot_de_passe,
                    "message": "Première connexion avec ce mot de passe temporaire.",
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class VueCompteursAdmin(APIView):
    """GET /admin/utilisateurs/compteurs — bandeaux ADM-01."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Compteurs d'administration",
        description=(
            "Ingénieurs en attente de validation, demandes de réinitialisation "
            "en attente (Q-23) et comptes suspendus."
        ),
        responses={200: CompteursAdminSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(CompteursAdminSerializer(admin_utilisateurs.compteurs()).data)


class VueDemandesReinitialisation(APIView):
    """GET /admin/utilisateurs/demandes-reinitialisation — file Q-23."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Lister les demandes de réinitialisation en attente",
        responses={200: LISTE_DEMANDES_REINITIALISATION},
    )
    def get(self, request: Request) -> Response:
        queryset = admin_utilisateurs.lister_demandes_reinitialisation()
        return reponse_liste(request, queryset, UtilisateurAdminListeSerializer)


class VueDetailUtilisateurAdmin(APIView):
    """GET /admin/utilisateurs/{id} — détail + dossier ingénieur (D-13)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Consulter un utilisateur (admin)",
        responses={
            200: UtilisateurAdminDetailSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def get(self, request: Request, id: str) -> Response:
        utilisateur = admin_utilisateurs.obtenir(id)
        return Response(UtilisateurAdminDetailSerializer(utilisateur).data)


class VueSuspendreUtilisateur(APIView):
    """POST /admin/utilisateurs/{id}/suspendre — motif obligatoire, sessions révoquées."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Suspendre un compte",
        description=(
            "Le compte devient inaccessible et TOUTES ses sessions sont "
            "révoquées immédiatement. Motif obligatoire, notifié et journalisé."
        ),
        request=SuspendreSerializer,
        responses={
            200: UtilisateurAdminDetailSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
            409: OpenApiResponse(description="transition_invalide"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        serializer = SuspendreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur = admin_utilisateurs.suspendre(
            _porteur(request),
            id,
            serializer.validated_data["motif"],
            adresse_ip=_adresse_ip(request),
        )
        return Response(UtilisateurAdminDetailSerializer(utilisateur).data)


class VueReactiverUtilisateur(APIView):
    """POST /admin/utilisateurs/{id}/reactiver."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Réactiver un compte suspendu",
        request=None,
        responses={
            200: UtilisateurAdminDetailSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
            409: OpenApiResponse(description="transition_invalide"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        utilisateur = admin_utilisateurs.reactiver(
            _porteur(request), id, adresse_ip=_adresse_ip(request)
        )
        return Response(UtilisateurAdminDetailSerializer(utilisateur).data)


class VueReinitialiserMotDePasse(APIView):
    """POST /admin/utilisateurs/{id}/reinitialiser-mot-de-passe — Q-23."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Émettre un mot de passe temporaire",
        description=(
            "Le mot de passe n'est renvoyé qu'une seule fois, jamais stocké ni "
            "journalisé en clair ; le compte devra le changer à la connexion. "
            "Toutes les sessions en cours sont révoquées."
        ),
        request=None,
        responses={
            200: MotDePasseTemporaireSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        utilisateur, mdp = admin_utilisateurs.reinitialiser_mot_de_passe(
            _porteur(request), id, adresse_ip=_adresse_ip(request)
        )
        return Response(
            MotDePasseTemporaireSerializer(
                {
                    "mot_de_passe_temporaire": mdp,
                    "doit_changer_mot_de_passe": utilisateur.doit_changer_mot_de_passe,
                    "message": "Communiquer ce mot de passe hors plateforme ; il ne sera "
                    "plus affiché après cette réponse.",
                }
            ).data
        )

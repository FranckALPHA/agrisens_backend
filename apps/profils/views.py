"""Vues API des profils professionnels ([[03-api-endpoints]] SPEC-01, F-03 point 3-4)."""

from typing import cast

from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole, AutoriseEnAttente, EstAuthentifieEtActif
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.profils.serializers import (
    DisponibiliteLivreurSerializer,
    IngenieurProfilSerializer,
    IngenieurPublicSerializer,
    JustificatifDepotSerializer,
    JustificatifSerializer,
    LivreurProfilSerializer,
    RefusIngenieurSerializer,
)
from apps.profils.services import ROLES_ADMIN, ServiceProfilIngenieur, ServiceProfilLivreur
from apps.utilisateurs.models import RoleUtilisateur, Utilisateur


def _porteur(request: Request) -> Utilisateur:
    """Utilisateur authentifié de la requête (garanti par les permissions)."""
    return cast("Utilisateur", request.user)


def _adresse_ip(request: Request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


# Enveloppe paginée documentée pour l'OpenAPI (APIView + reponse_liste).
LISTE_INGENIEURS = schema_enveloppe_liste("ListeIngenieurs", IngenieurPublicSerializer)


class VueProfilIngenieurMoi(APIView):
    """GET/PUT /ingenieurs/moi/profil — accessible même en attente de validation."""

    permission_classes = [AutoriseEnAttente]

    @extend_schema(
        summary="Consulter son profil professionnel",
        responses={200: IngenieurProfilSerializer},
    )
    def get(self, request: Request) -> Response:
        profil = ServiceProfilIngenieur.obtenir_profil(_porteur(request))
        return Response(IngenieurProfilSerializer(profil).data)

    @extend_schema(
        summary="Mettre à jour son profil professionnel",
        description=(
            "Spécialités (référentiel), zone (centre + rayon), tarif borné par les "
            "paramètres de consultation, disponibilité, biographie, modalités, photo. "
            "La mise en disponibilité exige un profil complet et validé."
        ),
        request=IngenieurProfilSerializer,
        responses={
            200: IngenieurProfilSerializer,
            422: OpenApiResponse(description="validation_invalide"),
        },
    )
    def put(self, request: Request) -> Response:
        serializer = IngenieurProfilSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profil = ServiceProfilIngenieur.modifier_profil(
            _porteur(request), serializer.validated_data
        )
        return Response(IngenieurProfilSerializer(profil).data)


class VueJustificatifsMoi(APIView):
    """GET/POST /ingenieurs/moi/justificatifs — dossier de candidature (D-13)."""

    permission_classes = [AutoriseEnAttente]

    @extend_schema(
        summary="Lister ses justificatifs",
        responses={200: JustificatifSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        pieces = ServiceProfilIngenieur.lister_justificatifs(_porteur(request))
        return Response(JustificatifSerializer(pieces, many=True).data)

    @extend_schema(
        summary="Déposer un justificatif",
        description="Rattache un fichier déjà téléversé en contexte `justificatif`.",
        request=JustificatifDepotSerializer,
        responses={
            201: JustificatifSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = JustificatifDepotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        piece = ServiceProfilIngenieur.ajouter_justificatif(
            _porteur(request),
            fichier_id=serializer.validated_data["fichier_id"],
            type_document=serializer.validated_data["type"],
        )
        return Response(JustificatifSerializer(piece).data, status=status.HTTP_201_CREATED)


class VueListeIngenieurs(APIView):
    """GET /ingenieurs — ingénieurs validés et disponibles (rôle : authentifié actif)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Lister les ingénieurs validés et disponibles",
        operation_id="ingenieurs_lister",
        description=(
            "Filtres `specialite` (code du référentiel) et `region`. "
            "Le filtre par exploitation (zone) sera ajouté en F-11."
        ),
        parameters=[
            OpenApiParameter("specialite", str, description="Code de spécialité"),
            OpenApiParameter("region", str, description="Code de région"),
        ],
        responses={200: LISTE_INGENIEURS},
    )
    def get(self, request: Request) -> Response:
        profils = ServiceProfilIngenieur.lister_valides(
            specialite=request.query_params.get("specialite") or None,
            region=request.query_params.get("region") or None,
        )
        return reponse_liste(request, profils, IngenieurPublicSerializer)


class VueIngenieurDetail(APIView):
    """GET /ingenieurs/{id} — profil public, sans téléphone (03 SPEC-01)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Consulter le profil public d'un ingénieur",
        responses={
            200: IngenieurPublicSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def get(self, request: Request, id: str) -> Response:
        profil = ServiceProfilIngenieur.obtenir_public(id)
        return Response(IngenieurPublicSerializer(profil).data)


class VueDisponibiliteLivreur(APIView):
    """GET/PUT /livreurs/moi/disponibilite — bouton disponible/indisponible (LIV-01)."""

    permission_classes = [ARole(RoleUtilisateur.LIVREUR)]

    @extend_schema(
        summary="Consulter sa disponibilité de livreur",
        responses={200: LivreurProfilSerializer},
    )
    def get(self, request: Request) -> Response:
        profil = ServiceProfilLivreur.obtenir(_porteur(request))
        return Response(LivreurProfilSerializer(profil).data)

    @extend_schema(
        summary="Se rendre disponible ou indisponible",
        description=(
            "Se rendre disponible exige une position (GeoJSON Point dans "
            "l'emprise du Cameroun) au moins une fois."
        ),
        request=DisponibiliteLivreurSerializer,
        responses={200: LivreurProfilSerializer},
    )
    def put(self, request: Request) -> Response:
        serializer = DisponibiliteLivreurSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        profil = ServiceProfilLivreur.definir_disponibilite(
            _porteur(request),
            disponible=bool(donnees["disponible"]),
            position=donnees.get("position"),
            zone=donnees.get("zone"),
        )
        return Response(LivreurProfilSerializer(profil).data)


class VueValiderIngenieur(APIView):
    """POST /admin/ingenieurs/{id}/valider — réservé aux administrateurs."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Valider le profil d'un ingénieur",
        request=None,
        description=(
            "Après examen des justificatifs (D-13). Rend le compte actif et "
            "notifie l'ingénieur. Journalisé dans JOURNAL_AUDIT."
        ),
        responses={
            200: IngenieurPublicSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
            409: OpenApiResponse(description="transition_invalide"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        profil = ServiceProfilIngenieur.valider(
            _porteur(request), id, adresse_ip=_adresse_ip(request)
        )
        return Response(IngenieurPublicSerializer(profil).data)


class VueRefuserIngenieur(APIView):
    """POST /admin/ingenieurs/{id}/refuser — motif obligatoire, notifié (D-13)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Refuser le profil d'un ingénieur",
        description="Le motif est obligatoire (vide → 422 `motif_obligatoire`) et notifié (D-13).",
        request=RefusIngenieurSerializer,
        responses={
            200: IngenieurPublicSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
            409: OpenApiResponse(description="transition_invalide"),
            422: OpenApiResponse(description="motif_obligatoire"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        donnees = request.data if isinstance(request.data, dict) else {}
        motif = str(donnees.get("motif", ""))
        profil = ServiceProfilIngenieur.refuser(
            _porteur(request), id, motif, adresse_ip=_adresse_ip(request)
        )
        return Response(IngenieurPublicSerializer(profil).data)

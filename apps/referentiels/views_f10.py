"""Vues API F-10 : gestion des référentiels par le super-admin (SPEC-11).

Les vues sont dans l'app référentiels mais réutilisent les serializers de
l'administration (pattern déjà suivi par paiements → administration).
"""

from commun.permissions import ARole
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration.serializers_f10 import (
    ReferentielAdminSerializer,
    ReferentielRenommageSerializer,
)
from apps.administration.views import _adresse_ip, _porteur
from apps.referentiels import services


class VueAdminReferentiels(APIView):
    """/admin/referentiels/{type} — liste de gestion (sadm) + ajout."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Lister un référentiel (gestion)",
        description="Toutes les entrées d'un type, actifs et inactifs.",
        parameters=[OpenApiParameter("type_ref", str)],
        responses={
            200: ReferentielAdminSerializer(many=True),
            422: OpenApiResponse(description="type_referentiel_inconnu"),
        },
    )
    def get(self, request: Request, type_ref: str) -> Response:
        entrees = services.lister_tous(type_ref, inclure_inactifs=True)
        return Response(ReferentielAdminSerializer(entrees, many=True).data)

    @extend_schema(
        summary="Ajouter une entrée de référentiel",
        request=ReferentielAdminSerializer,
        responses={
            201: ReferentielAdminSerializer,
            409: OpenApiResponse(description="code_existant"),
            422: OpenApiResponse(description="type_referentiel_inconnu | champ_requis"),
        },
    )
    def post(self, request: Request, type_ref: str) -> Response:
        serializer = ReferentielAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        entree = services.creer(
            type_ref,
            donnees["code"],
            donnees["libelle"],
            donnees.get("ordre", 0),
            acteur=_porteur(request),
            adresse_ip=_adresse_ip(request),
        )
        return Response(
            ReferentielAdminSerializer(entree).data, status=status.HTTP_201_CREATED
        )


class VueAdminReferentielEntree(APIView):
    """/admin/referentiels/entrees/{id} — renommage (PUT) du libellé seul."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Renommer le libellé d'une entrée",
        description="Le code stable ne change jamais ; l'ancien libellé est audité.",
        request=ReferentielRenommageSerializer,
        responses={
            200: ReferentielAdminSerializer,
            404: OpenApiResponse(description="referentiel_introuvable"),
        },
    )
    def put(self, request: Request, id: str) -> Response:
        serializer = ReferentielRenommageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entree = services.renommer(
            id,
            serializer.validated_data["libelle"],
            acteur=_porteur(request),
            adresse_ip=_adresse_ip(request),
        )
        return Response(ReferentielAdminSerializer(entree).data)


class VueAdminReferentielDesactiver(APIView):
    """POST /admin/referentiels/entrees/{id}/desactiver — jamais de suppression."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Désactiver une entrée de référentiel",
        description=(
            "L'entrée reste en base (un code utilisé ne se supprime jamais) ; "
            "elle disparaît des formulaires."
        ),
        request=None,
        responses={
            200: ReferentielAdminSerializer,
            404: OpenApiResponse(description="referentiel_introuvable"),
            409: OpenApiResponse(description="deja_desactive"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        entree = services.desactiver(
            id, acteur=_porteur(request), adresse_ip=_adresse_ip(request)
        )
        return Response(ReferentielAdminSerializer(entree).data)

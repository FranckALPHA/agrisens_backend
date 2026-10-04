"""Vues DRF des statistiques (SPEC-11, F-14).

Vues minces : garde de rôle (`ARole`), lecture des filtres de période, appel du
service, réponse. Aucune agrégation ici. Un rôle non autorisé reçoit 403 (les
statistiques ne sont pas une ressource privée d'un tiers mais un point de vue
réservé par rôle) ; un acheteur qui appelle `/statistiques/plateforme` → 403.
"""

from typing import Any, cast

from commun.permissions import ARole
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.statistiques.serializers import (
    MesAccompagnementsSerializer,
    MesLivraisonsSerializer,
    MesVentesSerializer,
    PlateformeSerializer,
)
from apps.statistiques.services import ServiceStatistiques, normaliser_granularite

PARAM_PERIODE = [
    OpenApiParameter("du", str, description="Date de début inclusive (AAAA-MM-JJ)."),
    OpenApiParameter("au", str, description="Date de fin inclusive (AAAA-MM-JJ)."),
    OpenApiParameter(
        "granularite",
        str,
        description="Granularité de la série : jour, semaine ou mois (défaut : mois).",
    ),
]


def _filtres(request: Request) -> dict[str, Any]:
    query = request.query_params
    return {
        "du": query.get("du") or None,
        "au": query.get("au") or None,
        "granularite": normaliser_granularite(query.get("granularite")),
    }


class VueMesVentes(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        operation_id="statistiques_mes_ventes",
        summary="Statistiques de ventes de l'agriculteur connecté",
        parameters=PARAM_PERIODE,
        responses={200: MesVentesSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(
            ServiceStatistiques.mes_ventes(cast(Any, request.user), **_filtres(request))
        )


class VueMesLivraisons(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        operation_id="statistiques_mes_livraisons",
        summary="Statistiques de livraisons du livreur connecté",
        parameters=PARAM_PERIODE,
        responses={200: MesLivraisonsSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(
            ServiceStatistiques.mes_livraisons(cast(Any, request.user), **_filtres(request))
        )


class VueMesAccompagnements(APIView):
    permission_classes = [ARole("ingenieur_agronome")]

    @extend_schema(
        operation_id="statistiques_mes_accompagnements",
        summary="Statistiques d'accompagnements de l'ingénieur connecté",
        parameters=PARAM_PERIODE,
        responses={200: MesAccompagnementsSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(
            ServiceStatistiques.mes_accompagnements(cast(Any, request.user), **_filtres(request))
        )


class VuePlateforme(APIView):
    permission_classes = [ARole("admin", "super_admin")]

    @extend_schema(
        operation_id="statistiques_plateforme",
        summary="Statistiques globales de la plateforme (admin / super-admin)",
        parameters=PARAM_PERIODE,
        responses={200: PlateformeSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(ServiceStatistiques.plateforme(**_filtres(request)))

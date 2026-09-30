"""Vues API pour les référentiels."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.referentiels.serializers import ReferentielSerializer
from apps.referentiels.services import lister_par_type


class VueReferentielParType(APIView):
    """Retourne la liste des éléments d'un référentiel donné."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Lister un référentiel par type",
        description=(
            "Retourne la liste des entrées actives d'un référentiel (region, unite, "
            "categorie_produit, type_sol, specialite, culture)."
        ),
        responses={
            200: ReferentielSerializer(many=True),
            404: OpenApiResponse(description="Référentiel introuvable"),
        },
    )
    def get(self, request: Request, type_ref: str) -> Response:
        elements = lister_par_type(type_ref)
        serializer = ReferentielSerializer(elements, many=True)
        return Response(serializer.data)

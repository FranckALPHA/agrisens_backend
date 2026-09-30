"""Vues API pour l'administration et les paramètres."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration.services import parametres


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

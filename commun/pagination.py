"""Pagination conforme aux spécifications de l'API AgriSens.

Conventions techniques §4 et prompt F-00 point 9 :
Paramètres : ``page`` (>= 1, défaut 1) et ``taille`` (1-100, défaut 20).
Réponse :
{
  "elements": [...],
  "page": 1,
  "taille": 20,
  "total": 134
}
"""

from typing import Any

from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer


class PaginationStandard(PageNumberPagination):
    """Pagination standard avec page et taille."""

    page_query_param = "page"
    page_size_query_param = "taille"
    page_size = 20
    max_page_size = 100

    def get_paginated_response(self, data: Any) -> Response:
        page_num = self.page.number if self.page is not None else 1
        total_count = self.page.paginator.count if self.page is not None else len(data)
        taille = self.get_page_size(self.request) if self.request else self.page_size
        return Response(
            {
                "elements": data,
                "page": page_num,
                "taille": taille or self.page_size,
                "total": total_count,
            }
        )

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "elements": schema,
                "page": {
                    "type": "integer",
                    "example": 1,
                },
                "taille": {
                    "type": "integer",
                    "example": 20,
                },
                "total": {
                    "type": "integer",
                    "example": 134,
                },
            },
            "required": ["elements", "page", "taille", "total"],
        }


def reponse_liste(
    request: Request, queryset: Any, serializer_class: type[BaseSerializer]
) -> Response:
    """Réponse de liste paginée pour les `APIView` (DRF ne fournit pas
    `paginate_queryset` hors des vues génériques) : page courante sérialisée
    dans l'enveloppe {elements, page, taille, total}, sinon liste complète."""
    paginator = PaginationStandard()
    page: Any = paginator.paginate_queryset(queryset, request)
    if page is not None:
        return paginator.get_paginated_response(serializer_class(page, many=True).data)
    return Response(serializer_class(queryset, many=True).data)


def schema_enveloppe_liste(nom: str, element: type[BaseSerializer]) -> Any:
    """Schéma OpenAPI de l'enveloppe paginée pour `@extend_schema` sur les
    `APIView` : `reponse_liste` n'est pas détectée automatiquement par
    drf-spectacular (réservé aux vues génériques), donc on le documente."""
    from drf_spectacular.utils import inline_serializer
    from rest_framework import serializers as drf_serializers

    return inline_serializer(
        name=nom,
        fields={
            "elements": element(many=True),
            "page": drf_serializers.IntegerField(),
            "taille": drf_serializers.IntegerField(),
            "total": drf_serializers.IntegerField(),
        },
    )

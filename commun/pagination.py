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
from rest_framework.response import Response


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

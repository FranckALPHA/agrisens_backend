"""Routes de santé (sante et pret) pour la supervision et le démarrage."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class VueSante(APIView):
    """Vérification de vie basique (liveness probe)."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Santé de l'API (liveness)",
        description="Indique que le processus HTTP répond.",
        responses={200: OpenApiResponse(description="API en marche")},
    )
    def get(self, request: Request) -> Response:
        return Response({"statut": "ok", "version": "0.1.0"})


class VueSantePret(APIView):
    """Vérification de disponibilité complète (readiness probe)."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Disponibilité de l'API (readiness)",
        description="Vérifie la connexion MySQL et l'absence de migrations en attente.",
        responses={
            200: OpenApiResponse(description="API prête à servir"),
            503: OpenApiResponse(description="Base inaccessible ou migrations en attente"),
        },
    )
    def get(self, request: Request) -> Response:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
        except Exception:
            return Response(
                {
                    "statut": "non_pret",
                    "base": "inaccessible",
                    "migrations_en_attente": None,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            executor = MigrationExecutor(connection)
            targets = executor.loader.graph.leaf_nodes()
            plan = executor.migration_plan(targets)
            en_attente = len(plan)
            if en_attente > 0:
                return Response(
                    {
                        "statut": "non_pret",
                        "base": "accessible",
                        "migrations_en_attente": en_attente,
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        except Exception:
            return Response(
                {
                    "statut": "non_pret",
                    "base": "accessible",
                    "erreur": "Erreur lors du contrôle des migrations",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "statut": "pret",
                "base": "accessible",
                "migrations_en_attente": 0,
            }
        )

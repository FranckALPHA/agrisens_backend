"""Routes réservées aux tests et à l'environnement de développement.

Disponibles UNIQUEMENT si ENVIRONNEMENT in ('local', 'test').
Doc : docs/12-plan-tests-playwright.md §1.3 et prompt F-00 point 17.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from commun.exceptions import NonTrouve, ValidationMetier
from commun.horloge import decaler, fixer, maintenant, reinitialiser
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

# Registre des tâches pouvant être déclenchées manuellement en dev/test
REGISTRE_TACHES_DEV: dict[str, Callable[[], Any]] = {}


def enregistrer_tache_dev(nom: str, fonction: Callable[[], Any]) -> None:
    """Permet aux applications d'enregistrer des tâches exécutables via l'API dev."""
    REGISTRE_TACHES_DEV[nom] = fonction


class VueDevHorloge(APIView):
    """Contrôle de l'horloge système pour les tests."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Décaler ou figer l'horloge (dev/test)",
        description="Décale l'heure ou la fige pour tester les expirations et délais.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "decalage_minutes": {"type": "integer"},
                    "date_iso": {"type": "string", "format": "date-time"},
                },
            }
        },
        responses={200: OpenApiResponse(description="Horloge mise à jour")},
    )
    def post(self, request: Request) -> Response:
        data: dict[str, Any] = request.data if isinstance(request.data, dict) else {}
        decalage_minutes = data.get("decalage_minutes")
        date_iso = data.get("date_iso")

        if decalage_minutes is not None:
            decaler(timedelta(minutes=int(decalage_minutes)))
        elif date_iso is not None:
            try:
                dt = datetime.fromisoformat(date_iso)
                fixer(dt)
            except ValueError:
                raise ValidationMetier("format_date_invalide", "Date ISO 8601 invalide.") from None
        else:
            raise ValidationMetier(
                "parametres_manquants",
                "Spécifiez 'decalage_minutes' ou 'date_iso'.",
            )

        return Response({"maintenant": maintenant().isoformat()})

    @extend_schema(
        summary="Réinitialiser l'horloge (dev/test)",
        description="Rétablit le temps réel.",
        responses={200: OpenApiResponse(description="Horloge réinitialisée")},
    )
    def delete(self, request: Request) -> Response:
        reinitialiser()
        return Response(
            {
                "statut": "reinitialise",
                "maintenant": maintenant().isoformat(),
            }
        )


class ReponseTacheSerializer(serializers.Serializer):
    tache = serializers.CharField()
    statut = serializers.CharField()
    resultat = serializers.CharField()


class VueDevDeclencherTache(APIView):
    """Déclenchement immédiat d'une tâche de fond pour les tests E2E."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Exécuter une tâche planifiée immédiatement (dev/test)",
        request=None,
        responses={
            200: ReponseTacheSerializer,
            404: OpenApiResponse(description="Tâche inconnue"),
        },
    )
    def post(self, request: Request, nom: str) -> Response:
        tache = REGISTRE_TACHES_DEV.get(nom)
        if not tache:
            raise NonTrouve(
                code="tache_inconnue",
                message=f"Tâche inconnue : '{nom}'.",
                details={"nom": nom, "taches_disponibles": list(REGISTRE_TACHES_DEV.keys())},
            )

        resultat = tache()
        return Response({"tache": nom, "statut": "executee", "resultat": str(resultat)})

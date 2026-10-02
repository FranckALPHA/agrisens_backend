"""Vues DRF minces des paiements + webhook NotchPay (SPEC-05)."""

from typing import Any, cast

from commun.permissions import ARole, EstAuthentifieEtActif
from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.paiements.serializers import InitierPaiementSerializer, PaiementSerializer
from apps.paiements.services import ServicePaiement


class VueInitierPaiement(APIView):
    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Initier le paiement d'une commande acceptée",
        description=(
            "En-tête `Idempotency-Key` : la même clé renvoie le même paiement "
            "sans nouvel effet (double clic sans doublon)."
        ),
        request=InitierPaiementSerializer,
        responses={201: PaiementSerializer, 200: PaiementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = InitierPaiementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        paiement, url, rejoue = ServicePaiement.initier_paiement_commande(
            cast(Any, request.user),
            identifiant,
            serializer.validated_data["operateur"],
            serializer.validated_data.get("telephone") or None,
            request.headers.get("Idempotency-Key"),
            getattr(settings, "FRONTEND_URL", ""),
        )
        corps = PaiementSerializer(paiement).data | {"url_paiement": url, "rejoue": rejoue}
        return Response(corps, status=status.HTTP_200_OK if rejoue else status.HTTP_201_CREATED)


class VuePaiementDetail(APIView):
    # Le statut ne dépend jamais du retour navigateur : lecture seule,
    # payeur et admins uniquement (404 sinon).
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Consulter un paiement",
        responses={
            200: PaiementSerializer,
            404: OpenApiResponse(description="Paiement introuvable"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        return Response(
            PaiementSerializer(ServicePaiement.obtenir_paiement(request.user, identifiant)).data
        )


class VueWebhookNotchpay(APIView):
    """POST /webhooks/notchpay — UNIQUE point d'entrée des confirmations.

    Authentification par signature HMAC (`X-Notch-Signature`), jamais par
    jeton : permission DRF vide, vérification dans le service.
    """

    permission_classes: list[type] = []
    authentication_classes: list[type] = []

    @extend_schema(
        summary="Webhook NotchPay (confirmation des paiements)",
        description=(
            "Signature invalide → 401. Événement déjà reçu → 200 sans effet. "
            "Seul ce point d'entrée fait passer un paiement à `reussi`."
        ),
        request={"application/json": {"type": "object"}},
        responses={
            200: OpenApiResponse(description="Événement traité, rejoué, ignoré ou en erreur"),
            400: OpenApiResponse(description="Webhook illisible ou incomplet"),
            401: OpenApiResponse(description="Signature invalide"),
        },
    )
    def post(self, request: Request) -> Response:
        statut, corps = ServicePaiement.traiter_webhook(request.headers, request.body)
        return Response(corps, status=statut)

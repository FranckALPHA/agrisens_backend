"""Vues API des notifications ([[03-api-endpoints]] SPEC-10, prompt F-03 point 2).

Livraison par interrogation périodique en V1 ; le compteur alimente la
cloche de notifications de l'en-tête (polling 30 s côté frontend).
"""

from typing import cast

from commun.exceptions import NonTrouve
from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import EstAuthentifieEtActif
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.notifications.services import marquer_lue, tout_marquer_lu
from apps.utilisateurs.models import Utilisateur


def _porteur(request: Request) -> Utilisateur:
    """Utilisateur authentifié de la requête (garanti par les permissions)."""
    return cast("Utilisateur", request.user)


# Enveloppe paginée documentée pour l'OpenAPI (APIView + reponse_liste).
LISTE_NOTIFICATIONS = schema_enveloppe_liste("ListeNotifications", NotificationSerializer)


class VueListeNotifications(APIView):
    """GET /notifications — la liste du destinataire courant."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Lister ses notifications",
        parameters=[
            OpenApiParameter("non_lues", OpenApiTypes.BOOL, description="Filtrer sur les non lues"),
        ],
        responses={200: LISTE_NOTIFICATIONS},
    )
    def get(self, request: Request) -> Response:
        queryset = Notification.objects.filter(destinataire=_porteur(request))
        filtre = request.query_params.get("non_lues", "").lower()
        if filtre in ("1", "true", "oui"):
            queryset = queryset.filter(lue_le__isnull=True)
        return reponse_liste(request, queryset, NotificationSerializer)


class VueCompteurNotifications(APIView):
    """GET /notifications/compteur — nombre de non lues (cloche, polling 30 s)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Compteur de notifications non lues",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request: Request) -> Response:
        non_lues = Notification.objects.filter(
            destinataire=_porteur(request), lue_le__isnull=True
        ).count()
        return Response({"non_lues": non_lues})


class VueNotificationLue(APIView):
    """POST /notifications/{id}/lue — destinataire uniquement (autrui → 404)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Marquer une notification comme lue",
        request=None,
        responses={
            200: NotificationSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        notification = Notification.objects.filter(id=id, destinataire=_porteur(request)).first()
        if notification is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Notification introuvable.",
            )
        marquer_lue(notification)
        return Response(NotificationSerializer(notification).data)


class VueToutLireNotifications(APIView):
    """POST /notifications/tout-lire — marque toutes les non lues comme lues."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Marquer toutes les notifications comme lues",
        request=None,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        modifiees = tout_marquer_lu(_porteur(request))
        return Response({"modifiees": modifiees})

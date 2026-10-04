"""Vues API du chat (F-13, docs/03 SPEC-10).

Vues minces : permission, validation de forme, appel du service, réponse.
Aucune règle métier ici. Non-participant d'une ressource privée → 404
(contrôle délégué au service).
"""

from typing import Any, cast

from commun.exceptions import AccesInterdit
from commun.pagination import PaginationStandard, schema_enveloppe_liste
from commun.permissions import EstAuthentifieEtActif
from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.consommateurs import creer_ticket
from apps.conversations.serializers import (
    BlocageCreationSerializer,
    BlocageSerializer,
    ConversationCreationSerializer,
    ConversationSerializer,
    MarquerLuSerializer,
    MessageCreationSerializer,
    MessageSerializer,
)
from apps.conversations.services import (
    ServiceBlocage,
    ServiceConversation,
    ServiceMessage,
)
from apps.utilisateurs.models import Utilisateur


def _porteur(request: Request) -> Utilisateur:
    return cast("Utilisateur", request.user)


def _contexte(request: Request) -> dict[str, Any]:
    return {"utilisateur": _porteur(request)}


LISTE_CONVERSATIONS = schema_enveloppe_liste("ListeConversations", ConversationSerializer)


class VueConversations(APIView):
    """GET/POST /conversations — liste des siennes, ouverture/obtention."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        operation_id="conversations_list",
        summary="Lister ses conversations",
        responses={200: LISTE_CONVERSATIONS},
    )
    def get(self, request: Request) -> Response:
        conversations = ServiceConversation.vue_liste(_porteur(request))
        # L'enveloppe paginée est reconstruite à la main pour injecter le
        # contexte (utilisateur vues) requis par `autre_participant`/`non_lus`.
        paginator = PaginationStandard()
        page = paginator.paginate_queryset(conversations, request)
        elements = page if page is not None else conversations
        donnees = ConversationSerializer(elements, many=True, context=_contexte(request)).data
        if page is not None:
            return paginator.get_paginated_response(donnees)
        return Response(donnees)

    @extend_schema(
        summary="Ouvrir ou obtenir une conversation",
        description=(
            "Exige exactement un rattachement : `commande_id` ou `accompagnement_id`. "
            "L'appelant doit être une partie de la relation, sinon 404. Une seule "
            "conversation par relation (Q-46a)."
        ),
        request=ConversationCreationSerializer,
        responses={201: ConversationSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = ConversationCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = ServiceConversation.creer_ou_obtenir(
            _porteur(request), serializer.validated_data
        )
        return Response(
            ConversationSerializer(conversation, context=_contexte(request)).data,
            status=201,
        )


class VueDetailConversation(APIView):
    """GET /conversations/{id} — détail d'une conversation participante."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        operation_id="conversations_retrieve",
        summary="Obtenir une conversation",
        responses={
            200: ConversationSerializer,
            404: OpenApiResponse(description="conversation_introuvable"),
        },
    )
    def get(self, request: Request, id: str) -> Response:
        conversation = ServiceConversation.obtenir_participante(_porteur(request), id)
        return Response(ConversationSerializer(conversation, context=_contexte(request)).data)


class VueMessages(APIView):
    """GET/POST /conversations/{id}/messages — historique et envoi."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Lister les messages d'une conversation",
        parameters=[
            OpenApiParameter(
                "avant", str, description="Curseur : id du message le plus ancien déjà vu"
            ),
        ],
        responses={
            200: MessageSerializer(many=True),
            404: OpenApiResponse(description="conversation_introuvable"),
        },
    )
    def get(self, request: Request, id: str) -> Response:
        conversation = ServiceConversation.obtenir_participante(_porteur(request), id)
        avant = request.query_params.get("avant")
        messages = ServiceMessage.vue_messages(conversation, _porteur(request), avant)
        return Response(MessageSerializer(messages, many=True).data)

    @extend_schema(
        summary="Envoyer un message",
        description=(
            "`id_client` (UUID) requis et idempotent : un renvoi après reconnexion "
            "ne crée jamais de doublon. `contenu` et/ou `fichier_id` obligatoire, "
            "sinon 422 `message_vide`. Si un participant a bloqué l'autre, 403 "
            "`blocage_actif` (Q-15). Le `contenu` est stocké en texte brut."
        ),
        request=MessageCreationSerializer,
        responses={
            201: MessageSerializer,
            403: OpenApiResponse(description="blocage_actif"),
            422: OpenApiResponse(description="message_vide"),
        },
    )
    def post(self, request: Request, id: str) -> Response:
        conversation = ServiceConversation.obtenir_participante(_porteur(request), id)
        serializer = MessageCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message, cree = ServiceMessage.envoyer(
            conversation,
            _porteur(request),
            serializer.validated_data.get("contenu"),
            serializer.validated_data.get("fichier_id"),
            serializer.validated_data["id_client"],
        )
        return Response(
            MessageSerializer(message).data,
            status=201 if cree else 200,
        )


class VueMarquerLu(APIView):
    """POST /conversations/{id}/lu — pointe le dernier message lu (accusé)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Marquer les messages comme lus",
        request=MarquerLuSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, id: str) -> Response:
        conversation = ServiceConversation.obtenir_participante(_porteur(request), id)
        serializer = MarquerLuSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ServiceMessage.marquer_lu(
            conversation, _porteur(request), serializer.validated_data["dernier_message_lu_id"]
        )
        return Response({"marque": True})


class VueTicket(APIView):
    """POST /ws/ticket — ticket WebSocket à usage unique (30 s, Q-46e)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Demander un ticket WebSocket",
        description=(
            "Émet un ticket jetable stocké au cache, à passer en query string "
            "(`/ws?ticket=…`) pour authentifier le canal. Le consumer le consomme "
            "à la connexion puis l'invalide."
        ),
        request=None,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        duree = int(getattr(settings, "WS_TICKET_DUREE_S", 30))
        ticket = creer_ticket(str(_porteur(request).id), duree)
        return Response({"ticket": ticket, "expire_dans": duree})


class VueBlocage(APIView):
    """POST /blocages — bloque un utilisateur (empêche l'écriture dans les deux sens)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Bloquer un utilisateur",
        description="Un blocage empêche tout échange de messages entre les deux comptes (Q-15).",
        request=BlocageCreationSerializer,
        responses={201: BlocageSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = BlocageCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blocage = ServiceBlocage.bloquer(
            _porteur(request), serializer.validated_data["utilisateur_id"]
        )
        return Response(BlocageSerializer(blocage).data, status=201)


class VueBlocageSuppr(APIView):
    """DELETE /blocages/{utilisateur_id} — débloque un utilisateur."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Débloquer un utilisateur",
        request=None,
        responses={
            204: OpenApiResponse(description="Débloqué"),
            404: OpenApiResponse(description="aucun blocage actif"),
        },
    )
    def delete(self, request: Request, utilisateur_id: str) -> Response:
        supprime = ServiceBlocage.debloquer(_porteur(request), utilisateur_id)
        if not supprime:
            raise AccesInterdit(
                code="blocage_inexistant",
                message="Aucun blocage actif envers cet utilisateur.",
            )
        return Response(status=204)

"""Vues DRF minces des litiges (SPEC-05 remboursements, SPEC-10 signalements).

Chaque vue : vérifie le rôle (permission), valide (serializer), appelle le
service, répond. Aucune règle métier ici (10 §4).
"""

from typing import Any, cast

from commun.exceptions import NonTrouve
from commun.permissions import ARole, EstAuthentifieEtActif
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration.services_admin import ROLES_ADMIN
from apps.litiges.serializers import (
    ApprouverRemboursementSerializer,
    EvaluationCreationSerializer,
    EvaluationSerializer,
    ExecuterRemboursementSerializer,
    RefuserRemboursementSerializer,
    RemboursementCreationSerializer,
    RemboursementSerializer,
    SignalementCreationSerializer,
    SignalementSerializer,
    TraiterSignalementSerializer,
)
from apps.litiges.services import ServiceEvaluation, ServiceRemboursement, ServiceSignalement


class VueRemboursements(APIView):
    """POST /remboursements — demande de remboursement (acheteur, SPEC-05)."""

    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Demander un remboursement dans le délai paramétré",
        request=RemboursementCreationSerializer,
        responses={201: RemboursementSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = RemboursementCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        remboursement = ServiceRemboursement.demander(
            cast(Any, request.user),
            donnees["commande_id"],
            donnees["motif"],
            donnees["montant_demande_fcfa"],
            list(donnees.get("justificatifs") or []),
            donnees.get("signalement_id"),
        )
        return Response(RemboursementSerializer(remboursement).data, status=status.HTTP_201_CREATED)


class VueMesRemboursements(APIView):
    """GET /remboursements/mes-demandes — suivi par le demandeur (acheteur ou agriculteur, F-11)."""

    permission_classes = [ARole("acheteur", "agriculteur")]

    @extend_schema(
        summary="Mes demandes de remboursement",
        responses={200: RemboursementSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        remboursements = ServiceRemboursement.mes_demandes(cast(Any, request.user))
        return Response(RemboursementSerializer(remboursements, many=True).data)


class VueAdminRemboursements(APIView):
    """GET /admin/remboursements — file de traitement (filtrable par statut)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="File des remboursements",
        parameters=[OpenApiParameter("statut", str, description="Filtre de statut")],
        responses={200: RemboursementSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        statut = request.query_params.get("statut")
        remboursements = ServiceRemboursement.file_admin(statut)
        return Response(RemboursementSerializer(remboursements, many=True).data)


class VueRemboursementExaminer(APIView):
    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Passer la demande en examen",
        request=None,
        responses={200: RemboursementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        remboursement = ServiceRemboursement.examiner(cast(Any, request.user), identifiant)
        return Response(RemboursementSerializer(remboursement).data)


class VueRemboursementApprouver(APIView):
    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Approuver (montant total ou partiel)",
        request=ApprouverRemboursementSerializer,
        responses={200: RemboursementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = ApprouverRemboursementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remboursement = ServiceRemboursement.approuver(
            cast(Any, request.user),
            identifiant,
            serializer.validated_data["montant_accorde_fcfa"],
        )
        return Response(RemboursementSerializer(remboursement).data)


class VueRemboursementRefuser(APIView):
    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Refuser avec motif obligatoire",
        request=RefuserRemboursementSerializer,
        responses={200: RemboursementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = RefuserRemboursementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remboursement = ServiceRemboursement.refuser(
            cast(Any, request.user), identifiant, serializer.validated_data["motif"]
        )
        return Response(RemboursementSerializer(remboursement).data)


class VueRemboursementExecuter(APIView):
    """Q-26 : l'admin a effectué le renvoi hors plateforme, il enregistre la référence."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Enregistrer la référence du renvoi (approuvé → effectue)",
        request=ExecuterRemboursementSerializer,
        responses={200: RemboursementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = ExecuterRemboursementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remboursement = ServiceRemboursement.executer(
            cast(Any, request.user),
            identifiant,
            serializer.validated_data["reference_operateur"],
        )
        return Response(RemboursementSerializer(remboursement).data)


class VueSignalements(APIView):
    """POST /signalements — tout utilisateur authentifié et actif (SPEC-10)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Signaler un problème (utilisateur, produit, commande, livraison…)",
        request=SignalementCreationSerializer,
        responses={201: SignalementSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = SignalementCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        signalement = ServiceSignalement.creer(
            cast(Any, request.user),
            donnees["cible_type"],
            donnees["cible_id"],
            donnees["motif"],
            donnees["description"],
            list(donnees.get("fichier_ids") or []),
        )
        return Response(SignalementSerializer(signalement).data, status=status.HTTP_201_CREATED)


class VueMesSignalements(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Mes signalements et leur avancement",
        responses={200: SignalementSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        signalements = ServiceSignalement.mes_signalements(cast(Any, request.user))
        return Response(SignalementSerializer(signalements, many=True).data)


class VueAdminSignalements(APIView):
    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="File des signalements (filtrable par statut)",
        parameters=[OpenApiParameter("statut", str, description="Filtre de statut")],
        responses={200: SignalementSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        statut = request.query_params.get("statut")
        signalements = ServiceSignalement.file_admin(statut)
        return Response(SignalementSerializer(signalements, many=True).data)


class VueAdminSignalementTraiter(APIView):
    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Traiter un signalement (statut + décision, audit)",
        request=TraiterSignalementSerializer,
        responses={200: SignalementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = TraiterSignalementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        signalement = ServiceSignalement.traiter(
            cast(Any, request.user),
            identifiant,
            serializer.validated_data["statut"],
            serializer.validated_data.get("decision") or "",
        )
        return Response(SignalementSerializer(signalement).data)


class VueCommandeEvaluation(APIView):
    """POST /commandes/{id}/evaluation — seulement si livrée, une fois (02 §2.3)."""

    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Évaluer une commande livrée (1..5)",
        request=EvaluationCreationSerializer,
        responses={201: EvaluationSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = EvaluationCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        evaluation = ServiceEvaluation.creer(
            cast(Any, request.user),
            identifiant,
            donnees["note_agriculteur"],
            donnees.get("note_livreur"),
            donnees.get("commentaire"),
        )
        return Response(EvaluationSerializer(evaluation).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Consulter mon évaluation de cette commande",
        responses={
            200: EvaluationSerializer,
            404: OpenApiResponse(description="Aucune évaluation"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        evaluation = ServiceEvaluation.evaluation_de(cast(Any, request.user), identifiant)
        if evaluation is None:
            raise NonTrouve()
        return Response(EvaluationSerializer(evaluation).data)

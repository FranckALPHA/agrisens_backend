"""Vues DRF minces de la génération IA (SPEC-08).

Chaque vue : validation sérialiseur → permission de rôle → service → réponse.
Aucune logique métier ici. Les ressources privées non visibles renvoient 404
(jamais 403 qui révélerait l'existence), conformément aux conventions §5.
"""

from typing import Any, cast

from commun.exceptions import NonTrouve
from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole, EstAuthentifieEtActif
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accompagnements.services import ServiceAccompagnement
from apps.exploitations.models import Exploitation
from apps.plannings.models import GenerationIA, TypeGeneration
from apps.plannings.serializers import (
    CorrectionCreationSerializer,
    CorrectionIASerializer,
    GenerationIADetailSerializer,
    GenerationIAListSerializer,
    PlanningCreationSerializer,
    RecommandationCreationSerializer,
)
from apps.plannings.services import (
    ServiceCorrection,
    ServiceGeneration,
    ServiceQuota,
    resoudre_mode_simule,
)

ROLE_AGRI = "agriculteur"
ROLE_ING = "ingenieur_agronome"
ROLE_ADM = "admin"
ROLE_SADM = "super_admin"

PARAM_TEST_SIMULE = OpenApiParameter(
    "test_simule",
    str,
    description=(
        "Modes du fournisseur factice (local/test uniquement) : "
        "`normal`, `invalide`, `echec`, `timeout`. Ignoré sinon."
    ),
)

LISTE_GENERATIONS = schema_enveloppe_liste(
    "ListeGenerationsIA", GenerationIAListSerializer
)


def _exploitation_proprio(user: Any, identifiant: str) -> Exploitation:
    exploitation = Exploitation.objects.filter(id=identifiant, agriculteur=user).first()
    if exploitation is None:
        raise NonTrouve(
            code="ressource_introuvable",
            message="Exploitation introuvable.",
        )
    return exploitation


def _exploitation_visible(user: Any, identifiant: str) -> Exploitation:
    exploitation = Exploitation.objects.filter(id=identifiant).first()
    if exploitation is None or not ServiceAccompagnement.peut_acceder_exploitation(
        user, exploitation
    ):
        raise NonTrouve(
            code="ressource_introuvable",
            message="Exploitation introuvable.",
        )
    return exploitation


class VueQuota(APIView):
    permission_classes = [ARole(ROLE_AGRI)]

    @extend_schema(
        summary="Quota mensuel de générations IA (consommé / limite par type)",
        responses={200: None},
    )
    def get(self, request: Request) -> Response:
        return Response(ServiceQuota.vue_quota(cast(Any, request.user)))


class VuePlanningsExploitation(APIView):
    """POST = générer un planning (propriétaire) ; GET = historique (visibles)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Historique des générations d'une exploitation",
        description="Réservé au propriétaire, à un ingénieur affecté et à l'admin.",
        parameters=[
            OpenApiParameter("page", int),
            OpenApiParameter("taille", int),
        ],
        responses={
            200: LISTE_GENERATIONS,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        user = cast(Any, request.user)
        if user.role not in (ROLE_AGRI, ROLE_ING, ROLE_ADM, ROLE_SADM):
            raise NonTrouve(code="ressource_introuvable", message="Exploitation introuvable.")
        exploitation = _exploitation_visible(user, identifiant)
        generations = ServiceGeneration.liste_pour_exploitation(exploitation)
        return reponse_liste(request, generations, GenerationIAListSerializer)

    @extend_schema(
        summary="Générer un planning cultural assisté par IA",
        description=(
            "En-tête `Idempotency-Key` supporté. Dépassement de quota → 429 "
            "`quota_atteint`. L'IA ne reçoit aucune donnée personnelle ; le "
            "centre est arrondi à 0,1°. Seules les générations réussies "
            "décomptent le quota."
        ),
        parameters=[PARAM_TEST_SIMULE],
        request=PlanningCreationSerializer,
        responses={
            201: GenerationIADetailSerializer,
            429: OpenApiResponse(description="quota_atteint"),
            502: OpenApiResponse(description="reponse_invalide"),
            503: OpenApiResponse(description="limite_fournisseur"),
        },
    )
    def post(self, request: Request, identifiant: str) -> Response:
        user = cast(Any, request.user)
        if user.role != ROLE_AGRI:
            return Response({"detail": "Réservé aux agriculteurs."}, status=403)
        serializer = PlanningCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exploitation = _exploitation_proprio(user, identifiant)
        generation = ServiceGeneration.generer(
            user,
            exploitation,
            type_generation=TypeGeneration.PLANNING,
            donnees=serializer.validated_data,
            cle_idempotence=request.headers.get("Idempotency-Key"),
            mode=resoudre_mode_simule(request.query_params.get("test_simule")),
        )
        return Response(
            GenerationIADetailSerializer(generation).data, status=status.HTTP_201_CREATED
        )


class VueRecommandationsExploitation(APIView):
    permission_classes = [ARole(ROLE_AGRI)]

    @extend_schema(
        summary="Demander une recommandation agronomique à l'IA",
        description=(
            "Quota séparé (`recommandation`). En-tête `Idempotency-Key` "
            "supporté ; dépassement → 429 `quota_atteint`."
        ),
        parameters=[PARAM_TEST_SIMULE],
        request=RecommandationCreationSerializer,
        responses={
            201: GenerationIADetailSerializer,
            429: OpenApiResponse(description="quota_atteint"),
        },
    )
    def post(self, request: Request, identifiant: str) -> Response:
        user = cast(Any, request.user)
        serializer = RecommandationCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exploitation = _exploitation_proprio(user, identifiant)
        generation = ServiceGeneration.generer(
            user,
            exploitation,
            type_generation=TypeGeneration.RECOMMANDATION,
            donnees=serializer.validated_data,
            cle_idempotence=request.headers.get("Idempotency-Key"),
            mode=resoudre_mode_simule(request.query_params.get("test_simule")),
        )
        return Response(
            GenerationIADetailSerializer(generation).data, status=status.HTTP_201_CREATED
        )


class VueGenerationDetail(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Détail d'une génération IA (propriétaire, ingénieur affecté, admin)",
        responses={
            200: GenerationIADetailSerializer,
            404: OpenApiResponse(description="generation_introuvable"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        user = cast(Any, request.user)
        if user.role not in (ROLE_AGRI, ROLE_ING, ROLE_ADM, ROLE_SADM):
            raise NonTrouve(code="generation_introuvable", message="Génération IA introuvable.")
        generation = (
            GenerationIA.objects.select_related("exploitation").filter(id=identifiant).first()
        )
        if generation is None or not ServiceAccompagnement.peut_acceder_exploitation(
            user, generation.exploitation
        ):
            raise NonTrouve(code="generation_introuvable", message="Génération IA introuvable.")
        return Response(GenerationIADetailSerializer(generation).data)


class VueCorrection(APIView):
    """POST /generations-ia/{id}/corrections — ingénieur affecté uniquement."""

    permission_classes = [ARole(ROLE_AGRI, ROLE_ING, ROLE_ADM, ROLE_SADM)]

    @extend_schema(
        summary="Corriger une génération IA (ne mute jamais la réponse originale)",
        description=(
            "Réservé à un ingénieur affecté à un accompagnement actif de "
            "l'exploitation. Tout autre appelant reçoit 404 ; un acheteur/un "
            "livreur est refusé plus en amont (403)."
        ),
        request=CorrectionCreationSerializer,
        responses={
            201: CorrectionIASerializer,
            404: OpenApiResponse(description="generation_introuvable"),
        },
    )
    def post(self, request: Request, identifiant: str) -> Response:
        user = cast(Any, request.user)
        serializer = CorrectionCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if user.role != ROLE_ING:
            # Ni propriétaire ni admin ne corrigent : on ne révèle pas l'existence.
            raise NonTrouve(code="generation_introuvable", message="Génération IA introuvable.")
        correction = ServiceCorrection.creer(
            user,
            identifiant,
            commentaire=serializer.validated_data["commentaire"],
            contenu_corrige=serializer.validated_data.get("contenu_corrige"),
        )
        return Response(
            CorrectionIASerializer(correction).data,
            status=status.HTTP_201_CREATED,
        )

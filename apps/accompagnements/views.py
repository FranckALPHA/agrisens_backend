"""Vues DRF minces de l'accompagnement (SPEC-07)."""

from typing import Any, cast

from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole, EstAuthentifieEtActif
from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accompagnements.serializers import (
    AccompagnementCreationSerializer,
    AccompagnementDetailSerializer,
    AccompagnementSerializer,
    ChangerIngenieurSerializer,
    ConfirmerSerializer,
    DevisCreationSerializer,
    DevisReponseSerializer,
    MarquerManqueSerializer,
    PlanifierSerializer,
    RefusSerializer,
    TerminerSerializer,
)
from apps.accompagnements.services import ServiceAccompagnement

ROLE_AGRI = "agriculteur"
ROLE_ING = "ingenieur_agronome"
ROLE_ADM = "admin"
ROLE_SADM = "super_admin"

# Enveloppe paginée (listes).
LISTE_ACCOMPAGNEMENTS = schema_enveloppe_liste(
    "ListeAccompagnements", AccompagnementSerializer
)


class VueDevisAccompagnement(APIView):
    permission_classes = [ARole(ROLE_AGRI)]

    @extend_schema(
        summary="Devis consultation + déplacement, sans rien créer",
        description=(
            "Consultation = tarif profil ingénieur ; déplacement = 2 × km aller "
            "arrondi au km supérieur × tarif km, borné min/plafond (P-06). "
            "Refuse le présentiel si la distance dépasse "
            "`deplacement.distance_max_km` (hypothèse Q-44)."
        ),
        request=DevisCreationSerializer,
        responses={200: DevisReponseSerializer, 422: OpenApiResponse(description="validation")},
    )
    def post(self, request: Request) -> Response:
        serializer = DevisCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        devis = ServiceAccompagnement.devis(
            cast(Any, request.user),
            exploitation_id=d["exploitation_id"],
            ingenieur_id=d["ingenieur_id"],
            modalite=d["modalite"],
        )
        return Response(devis)


class VueCreerAccompagnement(APIView):
    """POST /accompagnements — crée une demande ; GET — liste selon le rôle."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Lister les accompagnements visibles par l'appelant",
        operation_id="accompagnements_lister",
        parameters=[
            OpenApiParameter("role", str, description="Filtre côté client, ignoré ici"),
        ],
        responses={200: LISTE_ACCOMPAGNEMENTS},
    )
    def get(self, request: Request) -> Response:
        user = cast(Any, request.user)
        if user.role not in (ROLE_AGRI, ROLE_ING):
            return Response({"detail": "Réservé aux agriculteurs et ingénieurs."}, status=403)
        if user.role == ROLE_ING:
            elements = ServiceAccompagnement.liste_ingenieur(user)
        else:
            elements = ServiceAccompagnement.liste_agriculteur(user)
        return reponse_liste(request, elements, AccompagnementSerializer)

    @extend_schema(
        summary="Créer une demande d'accompagnement",
        request=AccompagnementCreationSerializer,
        responses={201: AccompagnementSerializer},
    )
    def post(self, request: Request) -> Response:
        if cast(Any, request.user).role != ROLE_AGRI:
            return Response({"detail": "Réservé aux agriculteurs."}, status=403)
        serializer = AccompagnementCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        accompagnement = ServiceAccompagnement.creer(
            cast(Any, request.user),
            exploitation_id=d["exploitation_id"],
            ingenieur_id=d["ingenieur_id"],
            modalite=d["modalite"],
            motif=d["motif"],
            description=d.get("description"),
        )
        return Response(
            AccompagnementSerializer(accompagnement).data, status=status.HTTP_201_CREATED
        )


class VueDemandesIngenieur(APIView):
    permission_classes = [ARole(ROLE_ING)]

    @extend_schema(
        summary="Demandes reçues par cet ingénieur",
        responses={200: AccompagnementSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        elements = ServiceAccompagnement.demandes_ingenieur(cast(Any, request.user))
        return Response(AccompagnementSerializer(elements, many=True).data)


class VueAccompagnementDetail(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Détail d'un accompagnement (parties + admin)",
        responses={
            200: AccompagnementDetailSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        accompagnement = ServiceAccompagnement.obtenir_visible(
            cast(Any, request.user), identifiant
        )
        return Response(AccompagnementDetailSerializer(accompagnement).data)


class VueAccepter(APIView):
    permission_classes = [ARole(ROLE_ING)]

    @extend_schema(summary="Accepter la demande", request=None,
                   responses={200: AccompagnementSerializer})
    def post(self, request: Request, identifiant: str) -> Response:
        a = ServiceAccompagnement.accepter(cast(Any, request.user), identifiant)
        return Response(AccompagnementSerializer(a).data)


class VueRefuser(APIView):
    permission_classes = [ARole(ROLE_ING)]

    @extend_schema(
        summary="Refuser la demande (motif obligatoire)",
        request=RefusSerializer,
        responses={
            200: AccompagnementSerializer,
            422: OpenApiResponse(description="motif_obligatoire"),
        },
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = RefusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        a = ServiceAccompagnement.refuser(
            cast(Any, request.user), identifiant, serializer.validated_data["motif"]
        )
        return Response(AccompagnementSerializer(a).data)


class VuePlanifier(APIView):
    permission_classes = [ARole(ROLE_ING)]

    @extend_schema(
        summary="Planifier le rendez-vous (conflit de créneau → 409)",
        description=(
            "Verrou SELECT FOR UPDATE sur les RDV chevauchants du même "
            "ingénieur ; fige le montant avant confirmation."
        ),
        request=PlanifierSerializer,
        responses={
            200: AccompagnementSerializer,
            409: OpenApiResponse(description="conflit_creneau"),
        },
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = PlanifierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        a = ServiceAccompagnement.planifier(
            cast(Any, request.user),
            identifiant,
            date_debut=d["date_debut"],
            date_fin=d["date_fin"],
            lieu_rdv_geojson=d.get("lieu_rdv"),
            lieu_rdv_texte=d.get("lieu_rdv_texte"),
            lien_visio=d.get("lien_visio"),
        )
        return Response(AccompagnementSerializer(a).data)


class VueConfirmer(APIView):
    permission_classes = [ARole(ROLE_AGRI)]

    @extend_schema(
        summary="Confirmer le RDV et initier le paiement",
        description=(
            "En-tête `Idempotency-Key` supporté ; retourne l'URL NotchPay. "
            "Le passage à `planifiee` se fait au webhook (Q-05)."
        ),
        request=ConfirmerSerializer,
        responses={200: None},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = ConfirmerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        paiement, url, rejoue = ServiceAccompagnement.confirmer(
            cast(Any, request.user),
            identifiant,
            operateur=d["operateur"],
            telephone=(d.get("telephone") or None),
            cle_idempotence=request.headers.get("Idempotency-Key"),
            url_base_pwa=getattr(settings, "FRONTEND_URL", ""),
        )
        corps = {
            "paiement_id": paiement.id,
            "reference_interne": paiement.reference_interne,
            "url_paiement": url,
            "montant_fcfa": paiement.montant_fcfa,
            "rejoue": rejoue,
        }
        return Response(corps, status=status.HTTP_200_OK if rejoue else status.HTTP_201_CREATED)


class VueChangerIngenieur(APIView):
    permission_classes = [ARole(ROLE_AGRI, ROLE_ADM, ROLE_SADM)]

    @extend_schema(
        summary="Changer l'ingénieur affecté (Q-29, historisé)",
        description=(
            "Avant paiement : agriculteur ou admin. Après paiement : admin "
            "uniquement, montant conservé (hypothèse Q-44)."
        ),
        request=ChangerIngenieurSerializer,
        responses={200: AccompagnementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = ChangerIngenieurSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        a = ServiceAccompagnement.changer_ingenieur(
            cast(Any, request.user),
            identifiant,
            nouveau_ingénieur_id=serializer.validated_data["ingenieur_id"],
        )
        return Response(AccompagnementSerializer(a).data)


class VueMarquerManque(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Marquer le RDV manqué (Q-30)",
        description=(
            "Ingénieur absent → remboursement automatique. "
            "Agriculteur absent → reprogrammation gratuite."
        ),
        request=MarquerManqueSerializer,
        responses={200: AccompagnementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = MarquerManqueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        a = ServiceAccompagnement.marquer_manque(
            cast(Any, request.user), identifiant, absent=serializer.validated_data["absent"]
        )
        return Response(AccompagnementSerializer(a).data)


class VueTerminer(APIView):
    permission_classes = [ARole(ROLE_ING)]

    @extend_schema(
        summary="Clôturer l'accompagnement (observation obligatoire)",
        request=TerminerSerializer,
        responses={
            200: AccompagnementSerializer,
            422: OpenApiResponse(description="observation_obligatoire"),
        },
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = TerminerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        a = ServiceAccompagnement.terminer(
            cast(Any, request.user),
            identifiant,
            observation=serializer.validated_data["observation"],
        )
        return Response(AccompagnementSerializer(a).data)


class VueAnnuler(APIView):
    permission_classes = [ARole(ROLE_AGRI)]

    @extend_schema(
        summary="Annuler sa demande",
        request=None,
        responses={200: AccompagnementSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        a = ServiceAccompagnement.annuler(cast(Any, request.user), identifiant)
        return Response(AccompagnementSerializer(a).data)

"""Vues API F-10 : soldes bénéficiaires et reversements (Q-11)."""

from typing import cast

from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration.services_admin import ROLES_ADMIN
from apps.paiements.serializers_f10 import (
    ReversementCreationSerializer,
    ReversementSerializer,
    SoldeDetailSerializer,
    SoldeLigneSerializer,
)
from apps.paiements.services import reversements, soldes
from apps.utilisateurs.models import RoleUtilisateur, Utilisateur

ROLE_BENEFICIAIRES = (
    RoleUtilisateur.AGRICULTEUR,
    RoleUtilisateur.LIVREUR,
    RoleUtilisateur.INGENIEUR_AGRONOME,
)

LISTE_REVERSEMENTS = schema_enveloppe_liste(
    "ListeReversements", ReversementSerializer
)


def _porteur(request: Request) -> Utilisateur:
    """Utilisateur authentifié ; les permissions garantissent le rôle attendu."""
    return cast("Utilisateur", request.user)


def _adresse_ip(request: Request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


class VueSoldesMoi(APIView):
    """GET /soldes/moi — gains, remboursements, reversements, solde (agr/liv/ing)."""

    permission_classes = [ARole(*ROLE_BENEFICIAIRES)]

    @extend_schema(
        summary="Consulter son solde de gains",
        description=(
            "Décomposition du solde (Q-11) : gains acquis, remboursements "
            "imputés, déjà reversé, solde disponible. Jamais stocké, recalculé."
        ),
        responses={200: SoldeDetailSerializer},
    )
    def get(self, request: Request) -> Response:
        details = soldes.details(_porteur(request))
        return Response(SoldeDetailSerializer(details).data)


class VueAdminSoldes(APIView):
    """GET /admin/soldes — bénéficiaires au solde positif (adm)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Soldes positifs à reverser",
        description="File de travail pour les reversements manuels (Q-11).",
        responses={200: SoldeLigneSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        # Liste non paginée : le volume de bénéficiaires au solde positif reste
        # faible en V1 et l'admin traite la file entière.
        return Response(SoldeLigneSerializer(soldes.soldes_positifs(), many=True).data)


class VueAdminReversements(APIView):
    """GET/POST /admin/reversements — historique et enregistrement (adm)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Historique des reversements",
        parameters=[
            OpenApiParameter(
                "beneficiaire", str, description="Filtre par identifiant bénéficiaire"
            ),
            OpenApiParameter("page", int, description="Page de résultats (défaut 1)"),
            OpenApiParameter("taille", int, description="Taille de page, 1–100 (défaut 20)"),
        ],
        responses={200: LISTE_REVERSEMENTS},
    )
    def get(self, request: Request) -> Response:
        queryset = reversements.historique(
            beneficiaire_id=request.query_params.get("beneficiaire") or None
        )
        return reponse_liste(request, queryset, ReversementSerializer)

    @extend_schema(
        summary="Enregistrer un reversement manuel",
        description=(
            "Montant ≤ solde disponible, référence opérateur obligatoire, "
            "audit systématique. Le paiement sort du circuit NotchPay (Q-11)."
        ),
        request=ReversementCreationSerializer,
        responses={
            201: ReversementSerializer,
            404: OpenApiResponse(description="ressource_introuvable"),
            422: OpenApiResponse(
                description=(
                    "montant_invalide | reference_requise | "
                    "montant_superieur_solde | beneficiaire_invalide"
                )
            ),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ReversementCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        reversement = reversements.enregistrer(
            _porteur(request),
            beneficiaire_id=donnees["beneficiaire_id"],
            montant_fcfa=donnees["montant_fcfa"],
            operateur=donnees["operateur"],
            reference_operateur=donnees["reference_operateur"],
            note=donnees.get("note"),
            adresse_ip=_adresse_ip(request),
        )
        return Response(
            ReversementSerializer(reversement).data, status=status.HTTP_201_CREATED
        )

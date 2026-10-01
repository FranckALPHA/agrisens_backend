"""Vues DRF minces des commandes (SPEC-04)."""

from typing import Any, cast

from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole, EstAuthentifieEtActif
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.commandes.serializers import (
    CommandeDetailSerializer,
    CommandeListeSerializer,
    CreationCommandeSerializer,
    DevisSerializer,
    LigneHistoriqueSerializer,
    MotifSerializer,
)
from apps.commandes.services import ServiceCommande

LISTE_COMMANDES = schema_enveloppe_liste("ListeCommandes", CommandeListeSerializer)


class VueDevis(APIView):
    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Chiffrer une commande sans la créer",
        request=DevisSerializer,
        responses={200: OpenApiResponse(description="Devis calculé")},
    )
    def post(self, request: Request) -> Response:
        serializer = DevisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(ServiceCommande.devis(cast(Any, request.user), **serializer.validated_data))


class VueCommandes(APIView):
    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Passer une commande (un seul agriculteur)",
        request=CreationCommandeSerializer,
        responses={201: CommandeDetailSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = CreationCommandeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commande = ServiceCommande.creer(cast(Any, request.user), **serializer.validated_data)
        return Response(
            CommandeDetailSerializer(commande, context={"utilisateur": request.user}).data,
            status=status.HTTP_201_CREATED,
        )


class VueMesAchats(APIView):
    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Lister mes achats",
        parameters=[OpenApiParameter("statut", str, description="Filtre de statut")],
        responses={200: LISTE_COMMANDES},
    )
    def get(self, request: Request) -> Response:
        commandes = ServiceCommande.mes_achats(request.user, request.query_params.get("statut"))
        return reponse_liste(request, commandes, CommandeListeSerializer)


class VueMesVentes(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Lister mes ventes",
        parameters=[OpenApiParameter("statut", str, description="Filtre de statut")],
        responses={200: LISTE_COMMANDES},
    )
    def get(self, request: Request) -> Response:
        commandes = ServiceCommande.mes_ventes(request.user, request.query_params.get("statut"))
        return reponse_liste(request, commandes, CommandeListeSerializer)


class VueCommandeDetail(APIView):
    # Pas de filtre par rôle : le service renvoie 404 aux non-parties (PW-F06-08).
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Consulter une commande",
        responses={
            200: CommandeDetailSerializer,
            404: OpenApiResponse(description="Commande introuvable"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        commande = ServiceCommande.obtenir_visible(request.user, identifiant)
        return Response(
            CommandeDetailSerializer(commande, context={"utilisateur": request.user}).data
        )


class VueCommandeAccepter(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Accepter une commande (réserve le stock)",
        request=None,
        responses={200: CommandeDetailSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        commande = ServiceCommande.accepter(request.user, identifiant)
        return Response(
            CommandeDetailSerializer(commande, context={"utilisateur": request.user}).data
        )


class VueCommandeRejeter(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Rejeter une commande (motif obligatoire)",
        request=MotifSerializer,
        responses={200: CommandeDetailSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = MotifSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commande = ServiceCommande.rejeter(
            request.user, identifiant, serializer.validated_data.get("motif")
        )
        return Response(
            CommandeDetailSerializer(commande, context={"utilisateur": request.user}).data
        )


class VueCommandeAnnuler(APIView):
    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Annuler une commande avant paiement",
        request=None,
        responses={200: CommandeDetailSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        commande = ServiceCommande.annuler(request.user, identifiant)
        return Response(
            CommandeDetailSerializer(commande, context={"utilisateur": request.user}).data
        )


class VueCommandePreparer(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Marquer une commande payée en préparation",
        request=None,
        responses={200: CommandeDetailSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        commande = ServiceCommande.preparer(request.user, identifiant)
        return Response(
            CommandeDetailSerializer(commande, context={"utilisateur": request.user}).data
        )


class VueCommandeHistorique(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Historique des statuts d'une commande",
        responses={200: LigneHistoriqueSerializer(many=True)},
    )
    def get(self, request: Request, identifiant: str) -> Response:
        lignes = ServiceCommande.historique_visible(request.user, identifiant)
        return Response(
            LigneHistoriqueSerializer(
                [
                    {
                        "ancien_statut": ligne.ancien_statut,
                        "nouveau_statut": ligne.nouveau_statut,
                        "motif": ligne.motif,
                        "acteur_nom": ligne.acteur.nom if ligne.acteur else None,
                        "cree_le": ligne.cree_le,
                    }
                    for ligne in lignes
                ],
                many=True,
            ).data
        )

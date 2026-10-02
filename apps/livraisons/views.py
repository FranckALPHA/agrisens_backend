"""Vues DRF minces des livraisons (SPEC-06)."""

from typing import Any, cast

from commun.permissions import ARole, EstAuthentifieEtActif
from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.livraisons.serializers import (
    CodeRemiseSerializer,
    IncidentCreationSerializer,
    IncidentSerializer,
    LivraisonDetailSerializer,
    LivraisonDisponibleSerializer,
    PositionLivreurSerializer,
    ReprogrammerSerializer,
)
from apps.livraisons.services import ServiceLivraison


def _detail(request: Request, livraison: Any) -> Response:
    sensibles = ServiceLivraison.donnees_sensibles(livraison, request.user)
    return Response(LivraisonDetailSerializer(livraison, context={"sensibles": sensibles}).data)


class VueLivraisonsDisponibles(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Livraisons proposées, triées par distance (sans adresse exacte)",
        responses={200: LivraisonDisponibleSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        livraisons = ServiceLivraison.disponibles(cast(Any, request.user))
        return Response(LivraisonDisponibleSerializer(livraisons, many=True).data)


class VueMesLivraisons(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Mes livraisons acceptées et en cours",
        responses={200: LivraisonDisponibleSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        livraisons = ServiceLivraison.mes_livraisons(cast(Any, request.user))
        return Response(LivraisonDisponibleSerializer(livraisons, many=True).data)


class VueLivraisonDetail(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Consulter une livraison (données selon rôle et étape)",
        responses={
            200: LivraisonDetailSerializer,
            404: OpenApiResponse(description="Livraison introuvable"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        return _detail(request, ServiceLivraison.obtenir_visible(request.user, identifiant))


class VueLivraisonParCommande(APIView):
    """GET /commandes/{id}/livraison — livraison liée (parties + admin)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Consulter la livraison d'une commande",
        responses={
            200: LivraisonDetailSerializer,
            404: OpenApiResponse(description="Aucune livraison"),
        },
    )
    def get(self, request: Request, commande_id: str) -> Response:
        return _detail(request, ServiceLivraison.obtenir_par_commande(request.user, commande_id))


class VueLivraisonAccepter(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Accepter une livraison (un seul gagnant, sinon 409)",
        request=None,
        responses={200: LivraisonDisponibleSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        livraison = ServiceLivraison.accepter(cast(Any, request.user), identifiant)
        return Response(LivraisonDisponibleSerializer(livraison).data)


class VueLivraisonDesister(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Se désister (retour à proposée)",
        request=None,
        responses={200: LivraisonDisponibleSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        livraison = ServiceLivraison.se_desister(cast(Any, request.user), identifiant)
        return Response(LivraisonDisponibleSerializer(livraison).data)


class VueLivraisonDemarrer(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Démarrer la course (produit récupéré, stock consommé)",
        request=None,
        responses={200: LivraisonDisponibleSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        livraison = ServiceLivraison.demarrer(cast(Any, request.user), identifiant)
        return Response(LivraisonDisponibleSerializer(livraison).data)


class VueLivraisonConfirmer(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Confirmer la remise par le code (bloqué après 5 échecs)",
        request=CodeRemiseSerializer,
        responses={200: LivraisonDisponibleSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = CodeRemiseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        livraison = ServiceLivraison.confirmer_remise(
            cast(Any, request.user), identifiant, serializer.validated_data["code"]
        )
        return Response(LivraisonDisponibleSerializer(livraison).data)


class VueLivraisonIncidents(APIView):
    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Déclarer un incident (photo possible)",
        request=IncidentCreationSerializer,
        responses={201: IncidentSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = IncidentCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        incident = ServiceLivraison.declarer_incident(
            cast(Any, request.user),
            identifiant,
            serializer.validated_data["type"],
            serializer.validated_data.get("description") or "",
            serializer.validated_data.get("fichier_id"),
        )
        return Response(IncidentSerializer(incident).data, status=status.HTTP_201_CREATED)


class VueLivraisonReprogrammer(APIView):
    # Acheteur ou admin : le service tranche (404 sinon), comme le détail.
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Reprogrammer après incident (avec paiement si client absent)",
        request=ReprogrammerSerializer,
        responses={200: OpenApiResponse(description="Reprogrammée ou paiement initié")},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = ReprogrammerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            ServiceLivraison.reprogrammer(
                request.user,
                identifiant,
                serializer.validated_data.get("adresse_id"),
                serializer.validated_data.get("operateur"),
                serializer.validated_data.get("telephone") or None,
                request.headers.get("Idempotency-Key"),
                getattr(settings, "FRONTEND_URL", ""),
            )
        )


class VueLivraisonItineraire(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Itinéraire GeoJSON de la livraison",
        responses={200: OpenApiResponse(description="Itinéraire GeoJSON")},
    )
    def get(self, request: Request, identifiant: str) -> Response:
        livraison = ServiceLivraison.obtenir_visible(request.user, identifiant)
        itineraire = getattr(livraison, "itineraire", None)
        if itineraire is None:
            from commun.exceptions import NonTrouve

            raise NonTrouve()
        return Response(
            {
                "point_depart": itineraire.point_depart.__geo_interface__
                if hasattr(itineraire.point_depart, "__geo_interface__")
                else itineraire.point_depart,
                "point_arrivee": itineraire.point_arrivee.__geo_interface__
                if hasattr(itineraire.point_arrivee, "__geo_interface__")
                else itineraire.point_arrivee,
                "distance_km": str(itineraire.distance_km),
                "duree_estimee_min": itineraire.duree_estimee_min,
                "source": itineraire.source,
            }
        )


class VueCodeRemise(APIView):
    permission_classes = [ARole("acheteur")]

    @extend_schema(
        summary="Lire son code de remise (acheteur uniquement)",
        responses={200: OpenApiResponse(description="Code à 6 chiffres")},
    )
    def get(self, request: Request, identifiant: str) -> Response:
        return Response({"code": ServiceLivraison.code_pour_acheteur(request.user, identifiant)})


class VueLivraisonHistorique(APIView):
    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Historique des statuts d'une livraison",
        responses={200: OpenApiResponse(description="Historique")},
    )
    def get(self, request: Request, identifiant: str) -> Response:
        lignes = ServiceLivraison.historique_visible(request.user, identifiant)
        return Response(
            [
                {
                    "ancien_statut": ligne.ancien_statut,
                    "nouveau_statut": ligne.nouveau_statut,
                    "motif": ligne.motif,
                    "acteur_nom": ligne.acteur.nom if ligne.acteur else None,
                    "cree_le": ligne.cree_le,
                }
                for ligne in lignes
            ]
        )


class VueLivraisonPosition(APIView):
    """POST /livraisons/{id}/position — le livreur publie sa position en course."""

    permission_classes = [ARole("livreur")]

    @extend_schema(
        summary="Mettre à jour sa position pendant la course",
        request=PositionLivreurSerializer,
        responses={200: OpenApiResponse(description="Position enregistrée")},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = PositionLivreurSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profil = ServiceLivraison.mettre_a_jour_position(
            cast(Any, request.user), identifiant, serializer.validated_data["position"]
        )
        position = getattr(profil.position, "__geo_interface__", profil.position)
        return Response({"position": position, "position_maj_le": profil.position_maj_le})

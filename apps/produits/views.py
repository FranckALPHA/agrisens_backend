"""Vues DRF minces des produits et du catalogue (SPEC-03)."""

from typing import Any, cast

from commun.cache_http import CachePublicMixin
from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.produits.models import Produit
from apps.produits.serializers import (
    PhotoAjoutSerializer,
    PhotosOrdreSerializer,
    ProduitCatalogueSerializer,
    ProduitCreationSerializer,
    ProduitModificationSerializer,
    ProduitSerializer,
    StockSerializer,
)
from apps.produits.services import ServiceProduit

LISTE_CATALOGUE = schema_enveloppe_liste("CatalogueProduits", ProduitCatalogueSerializer)
LISTE_MES_PRODUITS = schema_enveloppe_liste("MesProduits", ProduitSerializer)

TRIS_CATALOGUE = ("recent", "prix_asc", "prix_desc")


def _entier_positif_ou_nul(valeur: str | None) -> int | None:
    if valeur is None or valeur == "":
        return None
    try:
        nombre = int(valeur)
    except (TypeError, ValueError):
        return None
    return nombre if nombre >= 0 else None


class VueProduits(CachePublicMixin, APIView):
    """POST /produits (publication, agriculteur) et GET /produits (catalogue public)."""

    cache_max_age = 60  # F-15 : cache HTTP catalogue

    def get_permissions(self):
        if self.request.method == "POST":
            return [ARole("agriculteur")()]
        return [AllowAny()]

    @extend_schema(
        summary="Consulter le catalogue public",
        operation_id="produits_catalogue",
        parameters=[
            OpenApiParameter("categorie", str, description="Code de catégorie"),
            OpenApiParameter("region", str, description="Code de région de l'exploitation"),
            OpenApiParameter("q", str, description="Recherche dans nom et description"),
            OpenApiParameter("prix_min", int, description="Prix plancher en FCFA"),
            OpenApiParameter("prix_max", int, description="Prix plafond en FCFA"),
            OpenApiParameter(
                "tri",
                str,
                description="recent (défaut), prix_asc ou prix_desc",
            ),
            OpenApiParameter("page", int, description="Page de résultats (défaut 1)"),
            OpenApiParameter("taille", int, description="Taille de page, 1–100 (défaut 20)"),
        ],
        responses={200: LISTE_CATALOGUE},
    )
    def get(self, request: Request) -> Response:
        tri = request.query_params.get("tri") or "recent"
        produits = ServiceProduit.catalogue(
            categorie=request.query_params.get("categorie") or None,
            region=request.query_params.get("region") or None,
            recherche=request.query_params.get("q") or None,
            prix_min=_entier_positif_ou_nul(request.query_params.get("prix_min")),
            prix_max=_entier_positif_ou_nul(request.query_params.get("prix_max")),
            tri=tri if tri in TRIS_CATALOGUE else "recent",
        )
        return reponse_liste(request, produits, ProduitCatalogueSerializer)

    @extend_schema(
        summary="Publier un produit",
        request=ProduitCreationSerializer,
        responses={201: ProduitSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = ProduitCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        produit = ServiceProduit.creer(cast(Any, request.user), **serializer.validated_data)
        return Response(ProduitSerializer(produit).data, status=status.HTTP_201_CREATED)


class VueMesProduits(APIView):
    """GET /produits/mes-produits — produits de l'agriculteur, tous statuts."""

    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Lister mes produits (tous statuts)",
        parameters=[
            OpenApiParameter("page", int, description="Page de résultats (défaut 1)"),
            OpenApiParameter("taille", int, description="Taille de page, 1–100 (défaut 20)"),
        ],
        responses={200: LISTE_MES_PRODUITS},
    )
    def get(self, request: Request) -> Response:
        produits = ServiceProduit.mes_produits(cast(Any, request.user))
        return reponse_liste(request, produits, ProduitSerializer)


class VueProduitDetail(CachePublicMixin, APIView):
    """GET /produits/{id} et PATCH /produits/{id}.

    GET : le propriétaire reçoit la vue complète (y compris `epuise` et
    `retire` pour gérer son stock) ; tout autre visiteur reçoit la vue
    publique, 404 si le produit n'est pas au catalogue. PATCH : propriétaire.
    """

    cache_max_age = 30  # F-15 : cache HTTP court sur détail produit

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [ARole("agriculteur")()]
        return [AllowAny()]

    @extend_schema(
        summary="Consulter un produit (vue complète pour le propriétaire)",
        operation_id="produits_detail",
        description=(
            "Le propriétaire reçoit tous les champs (stock réservé, statut) ; "
            "les autres visiteurs reçoivent la vue publique, 404 si indisponible."
        ),
        responses={
            200: ProduitCatalogueSerializer,
            404: OpenApiResponse(description="Produit indisponible"),
        },
    )
    def get(self, request: Request, identifiant: str) -> Response:
        utilisateur = request.user
        if (
            getattr(utilisateur, "is_authenticated", False)
            and getattr(utilisateur, "role", None) == "agriculteur"
        ):
            produit = Produit.objects.filter(
                id=identifiant, exploitation__agriculteur=cast(Any, utilisateur)
            ).first()
            if produit is not None:
                return Response(ProduitSerializer(produit).data)
        return Response(ProduitCatalogueSerializer(ServiceProduit.obtenir_public(identifiant)).data)

    @extend_schema(
        summary="Modifier un produit",
        request=ProduitModificationSerializer,
        responses={200: ProduitSerializer},
    )
    def patch(self, request: Request, identifiant: str) -> Response:
        serializer = ProduitModificationSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        produit = ServiceProduit.modifier(request.user, identifiant, **serializer.validated_data)
        return Response(ProduitSerializer(produit).data)


class VueProduitStock(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Ajuster le stock (refusé sous la quantité réservée)",
        request=StockSerializer,
        responses={200: ProduitSerializer},
    )
    def patch(self, request: Request, identifiant: str) -> Response:
        serializer = StockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        produit = ServiceProduit.definir_stock(
            request.user, identifiant, serializer.validated_data["quantite_stock"]
        )
        return Response(ProduitSerializer(produit).data)


class VueProduitRetirer(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Retirer un produit de la vente (historique conservé)",
        request=None,
        responses={200: ProduitSerializer()},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        return Response(ProduitSerializer(ServiceProduit.retirer(request.user, identifiant)).data)


class VueProduitPhotos(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Ajouter une photo au produit",
        request=PhotoAjoutSerializer,
        responses={201: PhotoAjoutSerializer},
    )
    def post(self, request: Request, identifiant: str) -> Response:
        serializer = PhotoAjoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lien = ServiceProduit.ajouter_photo(
            request.user,
            identifiant,
            serializer.validated_data["fichier_id"],
            serializer.validated_data.get("ordre"),
        )
        return Response(
            {"fichier_id": lien.fichier_id, "ordre": lien.ordre},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Réordonner les photos du produit",
        request=PhotosOrdreSerializer,
        responses={200: PhotosOrdreSerializer},
    )
    def put(self, request: Request, identifiant: str) -> Response:
        serializer = PhotosOrdreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ServiceProduit.ordonner_photos(
            request.user, identifiant, serializer.validated_data["fichier_ids"]
        )
        return Response({"fichier_ids": serializer.validated_data["fichier_ids"]})


class VueProduitPhotoDetail(APIView):
    permission_classes = [ARole("agriculteur")]

    @extend_schema(
        summary="Dissocier une photo du produit",
        responses={204: OpenApiResponse(description="Photo dissociée")},
    )
    def delete(self, request: Request, identifiant: str, fichier_id: str) -> Response:
        ServiceProduit.retirer_photo(request.user, identifiant, fichier_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

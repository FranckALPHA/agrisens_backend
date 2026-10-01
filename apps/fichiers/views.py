"""Vues API du module fichiers (routes « Fichiers » de [[03-api-endpoints]]).

Les vues valident, vérifient l'accès, appellent `ServiceFichiers` et
répondent ; aucune règle métier ici.
"""

from commun.permissions import AnonymeOuActif, AutoriseEnAttente
from django.http import FileResponse, HttpResponseRedirect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.fichiers.serializers import FichierSerializer, TeleversementSerializer
from apps.fichiers.services import service_fichiers


class VueTeleversementFichier(APIView):
    """POST /fichiers — upload multipart avec contexte d'usage."""

    permission_classes = [AutoriseEnAttente]

    @extend_schema(
        summary="Téléverser un fichier",
        description=(
            "Upload multipart (champ binaire `fichier` + `contexte`). Le type est "
            "vérifié sur le contenu, les tailles maximales viennent des paramètres "
            "de la plateforme ; les métadonnées EXIF des images sont retirées. "
            "Retourne l'identifiant du fichier."
        ),
        request={"multipart/form-data": TeleversementSerializer},
        responses={201: FichierSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = TeleversementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fichier = service_fichiers.televerser(
            proprietaire=request.user,
            contexte=serializer.validated_data["contexte"],
            fichier=serializer.validated_data["fichier"],
        )
        return Response(FichierSerializer(fichier).data, status=status.HTTP_201_CREATED)


class VueFichierDetail(APIView):
    """GET /fichiers/{id} — redirection vers une URL signée de courte durée."""

    # Compte actif ou en attente (le propriétaire d'un justificatif peut être
    # en attente de validation), visiteur anonyme pour les photos publiques
    # du catalogue ; un compte suspendu est refusé ici (403).
    permission_classes = [AnonymeOuActif]

    @extend_schema(
        summary="Obtenir un fichier",
        description=(
            "Renvoie une redirection 302 vers une URL signée HMAC de courte durée. "
            "Règles d'accès par contexte : photos de produits publiques, "
            "justificatifs visibles par le propriétaire et les admins ; "
            "un fichier non visible pour l'appelant renvoie 404."
        ),
        responses={
            302: OpenApiResponse(description="Redirection vers l'URL signée"),
            404: OpenApiResponse(description="ressource_introuvable"),
        },
    )
    def get(self, request: Request, id: str) -> HttpResponseRedirect:
        fichier = service_fichiers.obtenir(id)
        service_fichiers.verifier_acces(fichier, request.user)
        return HttpResponseRedirect(service_fichiers.construire_url_signee(fichier))


class VueFichierContenu(APIView):
    """GET /fichiers/{id}/contenu — servi via URL signée (pas de JWT)."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Télécharger le contenu d'un fichier",
        description=(
            "Point d'arrivée de la redirection de `GET /fichiers/{id}`. "
            "Autorisé uniquement par la signature HMAC ; signature invalide → 404, "
            "lien expiré → 410."
        ),
        parameters=[
            OpenApiParameter("expire", int, description="Timestamp d'expiration (secondes)"),
            OpenApiParameter("signature", str, description="Signature HMAC-SHA256"),
        ],
        responses={
            200: OpenApiResponse(
                description="Contenu binaire du fichier",
                response=OpenApiTypes.BINARY,
            ),
            404: OpenApiResponse(description="ressource_introuvable"),
            410: OpenApiResponse(description="url_expiree"),
        },
    )
    def get(self, request: Request, id: str) -> FileResponse:
        fichier = service_fichiers.valider_url_signee(
            id,
            request.query_params.get("expire", ""),
            request.query_params.get("signature", ""),
        )
        contenu = service_fichiers.ouvrir_contenu(fichier)
        return FileResponse(
            contenu,
            content_type=fichier.type_mime,
            as_attachment=False,
            filename=fichier.nom_original,
        )

"""Vues API du module utilisateurs (SPEC-01, prompt F-02 point 5).

Vues DRF minces : valident (serializer), vérifient l'accès (permission),
appellent le service, répondent. La gestion du cookie de rafraîchissement
(httpOnly, Q-25) est ici car c'est de la forme HTTP, pas du métier.
"""

from typing import Any, cast

from commun.exceptions import ErreurMetier
from commun.permissions import AutoriseEnAttente, EstAuthentifieEtActif
from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.utilisateurs.models import Utilisateur
from apps.utilisateurs.serializers import (
    AdresseCreationSerializer,
    AdresseModificationSerializer,
    AdresseSerializer,
    ConnexionSerializer,
    DemandeReinitialisationSerializer,
    InscriptionSerializer,
    MiseAJourMoiSerializer,
    MotDePasseSerializer,
    ReponseMessageSerializer,
    ReponseSessionSerializer,
    UtilisateurSerializer,
)
from apps.utilisateurs.services import (
    CHEMIN_COOKIE_RAFRAICHISSEMENT,
    NOM_COOKIE_RAFRAICHISSEMENT,
    AdresseService,
    CompteService,
    SessionService,
    duree_cookie_secondes,
)


def _cookie_secure() -> bool:
    """Drapeau Secure du cookie d'authentification.

    Actif seulement en staging/production : en local/test (http://127.0.0.1
    ou localhost), un cookie Secure serait rejeté par le navigateur.
    Les autres drapeaux suivent la journal Q-25 (httpOnly, SameSite=Lax,
    Path=/api/v1/auth).
    """
    return bool(getattr(settings, "COOKIE_AUTH_SECURE", False))


def _poser_cookie_rafraichissement(reponse: Response, jeton_brut: str, expire_le: Any) -> Response:
    """Cookie httpOnly réservé à /api/v1/auth (journal Q-25)."""
    reponse.set_cookie(
        key=NOM_COOKIE_RAFRAICHISSEMENT,
        value=jeton_brut,
        max_age=duree_cookie_secondes(expire_le),
        path=CHEMIN_COOKIE_RAFRAICHISSEMENT,
        httponly=True,
        secure=_cookie_secure(),
        samesite="Lax",
    )
    return reponse


def _reponse_session(utilisateur: Any, jeton_acces: str, expire_le: Any) -> dict[str, Any]:
    return {
        "utilisateur": UtilisateurSerializer(utilisateur).data,
        "jeton_acces": jeton_acces,
        "expire_le": expire_le,
    }


def _porteur(request: Request) -> Utilisateur:
    """Utilisateur authentifié de la requête.

    Les permissions DRF appelées avant la vue garantissent un utilisateur
    actif ; ce cast informe uniquement le vérificateur de types.
    """
    return cast("Utilisateur", request.user)


# Limites de débit (prompt F-02 point 5, « limitation de débit ») : les vues
# publiques utilisent ScopedRateThrottle avec un `throttle_scope` ; les taux
# viennent de REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (réglages, jamais en dur).


class VueInscription(APIView):
    """POST /auth/inscription — public, limité en débit."""

    permission_classes: list[type] = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "inscription"

    @extend_schema(
        summary="Créer un compte",
        description=(
            "Rôles autorisés : agriculteur, acheteur, ingenieur_agronome, livreur. "
            "L'ingénieur est créé en attente de validation administrateur (D-13). "
            "Établit la session : jeton d'accès dans la réponse, jeton de "
            "rafraîchissement en cookie httpOnly."
        ),
        request=InscriptionSerializer,
        responses={
            201: ReponseSessionSerializer,
            409: OpenApiResponse(description="compte_existant (téléphone ou email déjà utilisé)"),
            422: OpenApiResponse(
                description="validation_invalide / telephone_invalide / role_inconnu"
            ),
            429: OpenApiResponse(description="limite_debit_atteinte"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = InscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur, jeton_acces, jeton_rafraichir, expire_le = CompteService.inscrire(
            agent=request.META.get("HTTP_USER_AGENT"),
            **serializer.validated_data,
        )
        reponse = Response(
            _reponse_session(utilisateur, jeton_acces, expire_le),
            status=status.HTTP_201_CREATED,
        )
        return _poser_cookie_rafraichissement(reponse, jeton_rafraichir, expire_le)


class VueConnexion(APIView):
    """POST /auth/connexion — téléphone ou email + mot de passe, limité en débit."""

    permission_classes: list[type] = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "connexion"

    @extend_schema(
        summary="Se connecter",
        description=(
            "Identifiant : téléphone E.164 ou email. Message identique si le "
            "compte n'existe pas (anti-énumération). Compte suspendu → 403 "
            "compte_suspendu."
        ),
        request=ConnexionSerializer,
        responses={
            200: ReponseSessionSerializer,
            401: OpenApiResponse(description="identifiants_invalides"),
            403: OpenApiResponse(description="compte_suspendu"),
            429: OpenApiResponse(description="limite_debit_atteinte"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = ConnexionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur, jeton_acces, jeton_rafraichir, expire_le = CompteService.connecter(
            agent=request.META.get("HTTP_USER_AGENT"),
            **serializer.validated_data,
        )
        reponse = Response(_reponse_session(utilisateur, jeton_acces, expire_le))
        return _poser_cookie_rafraichissement(reponse, jeton_rafraichir, expire_le)


class VueRafraichir(APIView):
    """POST /auth/rafraichir — rotation du jeton de rafraîchissement (Q-25)."""

    permission_classes: list[type] = []

    @extend_schema(
        summary="Renouveler la session",
        description=(
            "Utilise le cookie httpOnly, révoque le jeton présenté (rotation) "
            "et établit une nouvelle session. Réutilisation d'un jeton déjà "
            "tourné → révocation de toutes les sessions de l'utilisateur."
        ),
        request=None,
        responses={
            200: ReponseSessionSerializer,
            401: OpenApiResponse(
                description="session_invalide / sessions_revoquees / session_expiree"
            ),
            403: OpenApiResponse(description="compte_suspendu"),
        },
    )
    def post(self, request: Request) -> Response:
        jeton_brut = request.COOKIES.get(NOM_COOKIE_RAFRAICHISSEMENT)
        if not jeton_brut:
            raise ErreurMetier(
                code="session_invalide",
                message="Aucune session à rafraîchir.",
                statut_http=status.HTTP_401_UNAUTHORIZED,
            )
        utilisateur, session, nouveau_jeton = SessionService.rafraichir(jeton_brut)
        jeton_acces, _ = SessionService.generer_jeton_acces(utilisateur)
        reponse = Response(_reponse_session(utilisateur, jeton_acces, session.expire_le))
        return _poser_cookie_rafraichissement(reponse, nouveau_jeton, session.expire_le)


class VueDeconnexion(APIView):
    """POST /auth/deconnexion — révoque la session courante et purge le cookie."""

    # Pas de permission : même un compte suspendu doit pouvoir purger son cookie.
    permission_classes: list[type] = []

    @extend_schema(
        summary="Se déconnecter",
        request=None,
        responses={204: OpenApiResponse(description="Session révoquée, cookie supprimé")},
    )
    def post(self, request: Request) -> Response:
        SessionService.deconnecter(request.COOKIES.get(NOM_COOKIE_RAFRAICHISSEMENT))
        reponse = Response(status=status.HTTP_204_NO_CONTENT)
        # delete_cookie() ne prend pas les drapeaux Secure/SameSite : on pose
        # un cookie vide avec exactement les mêmes attributs que la pose.
        reponse.set_cookie(
            key=NOM_COOKIE_RAFRAICHISSEMENT,
            value="",
            max_age=0,
            path=CHEMIN_COOKIE_RAFRAICHISSEMENT,
            httponly=True,
            secure=_cookie_secure(),
            samesite="Lax",
        )
        return reponse


class VueDemandeReinitialisation(APIView):
    """POST /auth/demande-reinitialisation — Q-23, réponse toujours identique."""

    permission_classes: list[type] = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reinitialisation"

    @extend_schema(
        summary="Demander une réinitialisation de mot de passe",
        description=(
            "L'administrateur vérifie l'identité par téléphone puis génère un "
            "mot de passe temporaire (F-03). Réponse identique que le compte "
            "existe ou non."
        ),
        request=DemandeReinitialisationSerializer,
        responses={200: ReponseMessageSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = DemandeReinitialisationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        CompteService.demander_reinitialisation(serializer.validated_data["telephone"])
        return Response(
            {
                "message": (
                    "Si un compte correspond à ce numéro, un administrateur "
                    "d'AgriSens vous contactera pour réinitialiser votre mot de passe."
                )
            }
        )


class VueMoi(APIView):
    """GET/PATCH /utilisateurs/moi — profil personnel, y compris compte en attente."""

    permission_classes = [AutoriseEnAttente]

    @extend_schema(summary="Consulter son profil", responses={200: UtilisateurSerializer})
    def get(self, request: Request) -> Response:
        return Response(UtilisateurSerializer(request.user).data)

    @extend_schema(
        summary="Modifier son profil",
        description="Nom, email, région, téléphone ; le rôle n'est jamais modifiable ici.",
        request=MiseAJourMoiSerializer,
        responses={
            200: UtilisateurSerializer,
            409: OpenApiResponse(description="compte_existant"),
            422: OpenApiResponse(description="validation_invalide / telephone_invalide"),
        },
    )
    def patch(self, request: Request) -> Response:
        serializer = MiseAJourMoiSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        utilisateur = CompteService.modifier_profil(_porteur(request), serializer.validated_data)
        return Response(UtilisateurSerializer(utilisateur).data)


class VueMotDePasse(APIView):
    """PUT /utilisateurs/moi/mot-de-passe — remet doit_changer_mot_de_passe à false."""

    permission_classes = [AutoriseEnAttente]

    @extend_schema(
        summary="Changer son mot de passe",
        request=MotDePasseSerializer,
        responses={
            200: UtilisateurSerializer,
            422: OpenApiResponse(description="mot_de_passe_incorrect / validation_invalide"),
        },
    )
    def put(self, request: Request) -> Response:
        serializer = MotDePasseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur = CompteService.changer_mot_de_passe(
            _porteur(request),
            serializer.validated_data["mot_de_passe_actuel"],
            serializer.validated_data["nouveau_mot_de_passe"],
        )
        return Response(UtilisateurSerializer(utilisateur).data)


class VueAdresses(APIView):
    """GET/POST /utilisateurs/moi/adresses."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(summary="Lister ses adresses", responses={200: AdresseSerializer(many=True)})
    def get(self, request: Request) -> Response:
        adresses = AdresseService.lister(_porteur(request))
        return Response(AdresseSerializer(adresses, many=True).data)

    @extend_schema(
        summary="Ajouter une adresse",
        request=AdresseCreationSerializer,
        responses={
            201: AdresseSerializer,
            422: OpenApiResponse(description="geometrie_invalide / hors_emprise_cameroun"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = AdresseCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adresse = AdresseService.creer(_porteur(request), **serializer.validated_data)
        return Response(AdresseSerializer(adresse).data, status=status.HTTP_201_CREATED)


class VueAdresseDetail(APIView):
    """PATCH/DELETE /utilisateurs/moi/adresses/{id} — propriétaire uniquement (sinon 404)."""

    permission_classes = [EstAuthentifieEtActif]

    @extend_schema(
        summary="Modifier son adresse",
        request=AdresseModificationSerializer,
        responses={
            200: AdresseSerializer,
            404: OpenApiResponse(
                description="ressource_introuvable (autre propriétaire ou inexistante)"
            ),
        },
    )
    def patch(self, request: Request, id: str) -> Response:  # noqa: A002
        serializer = AdresseModificationSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        adresse = AdresseService.modifier(_porteur(request), id, serializer.validated_data)
        return Response(AdresseSerializer(adresse).data)

    @extend_schema(
        summary="Supprimer son adresse",
        responses={204: OpenApiResponse(description="Adresse supprimée")},
    )
    def delete(self, request: Request, id: str) -> Response:  # noqa: A002
        AdresseService.supprimer(_porteur(request), id)
        return Response(status=status.HTTP_204_NO_CONTENT)

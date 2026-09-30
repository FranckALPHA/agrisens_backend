"""Authentification JWT pour Django REST Framework.

Conventions techniques §4 et §5, prompt F-02 points 1 et 3 :
- Lecture du jeton d'accès dans l'en-tête Authorization: Bearer <jeton>
- Validation de signature et d'expiration via PyJWT (HS256, type « acces »)
- Utilisateur injecté dans request.user ; le statut du compte (suspendu,
  en attente) est vérifié par les permissions DRF, pas ici : un compte
  suspendu doit recevoir 403 compte_suspendu, pas 401.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

if TYPE_CHECKING:
    from apps.utilisateurs.models import Utilisateur


class AuthentificationJWT(BaseAuthentication):
    """Classe d'authentification DRF vérifiant le jeton JWT d'accès."""

    def authenticate(self, request: Request) -> tuple[Utilisateur, dict[str, Any]] | None:
        en_tete_auth = request.headers.get("Authorization")
        if not en_tete_auth:
            return None

        # Import différé : ce module est chargé par les réglages DRF pendant
        # l'populate des apps Django, avant que models.py soit entièrement chargé.
        Utilisateur = get_user_model()

        morceaux = en_tete_auth.split()
        if len(morceaux) != 2 or morceaux[0].lower() != "bearer":
            return None

        jeton_brut = morceaux[1]
        try:
            payload = jwt.decode(
                jeton_brut,
                settings.JWT_SECRET,
                algorithms=["HS256"],
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Jeton d'accès expiré.") from None
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("Jeton d'accès invalide.") from None

        if payload.get("type") not in (None, "acces"):
            raise exceptions.AuthenticationFailed("Jeton d'accès invalide.")

        utilisateur_id = payload.get("sub") or payload.get("utilisateur_id")
        if not utilisateur_id:
            raise exceptions.AuthenticationFailed(
                "Jeton invalide : identifiant utilisateur manquant."
            )

        try:
            utilisateur = Utilisateur.objects.get(id=utilisateur_id)
        except Utilisateur.DoesNotExist:
            raise exceptions.AuthenticationFailed("Utilisateur introuvable.") from None

        # Le statut (suspendu, en attente, anonymisé) n'est pas rejeté ici :
        # les permissions DRF renvoient 403 avec un code stable.
        return utilisateur, payload

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'


try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class AuthentificationJWTScheme(OpenApiAuthenticationExtension):
        target_class = "apps.utilisateurs.authentification.AuthentificationJWT"
        name = "bearerAuth"

        def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
            return {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
except ImportError:
    pass

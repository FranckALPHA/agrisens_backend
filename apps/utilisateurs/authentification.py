"""Authentification JWT pour Django REST Framework.

Conventions techniques §4 et §5 :
- Lecture du jeton d'accès dans l'en-tête Authorization: Bearer <jeton>
- Validation de signature et d'expiration via PyJWT
- Utilisateur injecté dans request.user
"""

from typing import Any

import jwt
from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from apps.utilisateurs.models import Utilisateur


class AuthentificationJWT(BaseAuthentication):
    """Classe d'authentification DRF vérifiant le jeton JWT d'accès."""

    def authenticate(self, request: Request) -> tuple[Utilisateur, dict[str, Any]] | None:
        en_tete_auth = request.headers.get("Authorization")
        if not en_tete_auth:
            return None

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

        utilisateur_id = payload.get("sub") or payload.get("utilisateur_id")
        if not utilisateur_id:
            raise exceptions.AuthenticationFailed(
                "Jeton invalide : identifiant utilisateur manquant."
            )

        try:
            utilisateur = Utilisateur.objects.get(id=utilisateur_id)
        except Utilisateur.DoesNotExist:
            raise exceptions.AuthenticationFailed("Utilisateur introuvable.") from None

        if not utilisateur.is_active:
            raise exceptions.AuthenticationFailed("Compte utilisateur inactif ou suspendu.")

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

"""Fixtures pytest pour les tests unitaires et d'intégration d'AgriSens.

Conventions techniques §5, prompt F-00 point 20 :
- client API DRF
- fabrique d'utilisateurs par rôle
- client d'API authentifié réutilisable par toutes les features
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

Utilisateur = get_user_model()


@pytest.fixture
def client_api() -> APIClient:
    """Client API REST non authentifié."""
    return APIClient()


@pytest.fixture
def generer_jeton_acces() -> Callable[[Any], str]:
    """Génère un jeton JWT d'accès valide pour un utilisateur donné."""

    def _generateur(utilisateur: Any) -> str:
        payload = {
            "sub": str(utilisateur.id),
            "telephone": utilisateur.telephone,
            "role": utilisateur.role,
            "exp": datetime.now(UTC) + timedelta(minutes=15),
            "iat": datetime.now(UTC),
        }
        return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    return _generateur


@pytest.fixture
def fabrique_utilisateur(db: Any) -> Callable[..., Any]:
    """Fabrique réutilisable pour créer des utilisateurs avec différents rôles et statuts."""

    def _creer(
        role: str = "acheteur",
        statut_compte: str = "actif",
        telephone: str | None = None,
        nom: str | None = None,
        email: str | None = None,
        password: str = "AgriSens2026!",
        **extra: Any,
    ) -> Any:
        unique_suffix = str(uuid.uuid4().int)[:8]
        tel = telephone or f"+2376{unique_suffix}"
        user_nom = nom or f"Utilisateur {role.capitalize()}"
        user_email = email or f"user_{unique_suffix}@agrisens.local"

        if role == "super_admin":
            return Utilisateur.objects.create_superuser(
                telephone=tel,
                password=password,
                nom=user_nom,
                email=user_email,
                **extra,
            )

        return Utilisateur.objects.create_user(
            telephone=tel,
            password=password,
            nom=user_nom,
            role=role,
            statut_compte=statut_compte,
            email=user_email,
            **extra,
        )

    return _creer


@pytest.fixture
def client_authentifie(
    client_api: APIClient,
    fabrique_utilisateur: Callable[..., Any],
    generer_jeton_acces: Callable[[Any], str],
) -> Callable[..., tuple[APIClient, Any]]:
    """Retourne un client API DRF pré-authentifié avec le rôle demandé."""

    def _client(role: str = "acheteur", **kwargs: Any) -> tuple[APIClient, Any]:
        user = fabrique_utilisateur(role=role, **kwargs)
        jeton = generer_jeton_acces(user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {jeton}")
        return client, user

    return _client

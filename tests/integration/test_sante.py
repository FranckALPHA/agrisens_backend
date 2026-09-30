"""Tests d'intégration des endpoints de santé (sante et pret)."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.integration
@pytest.mark.django_db
def test_sante_liveness(client_api: APIClient) -> None:
    """GET /api/v1/sante renvoie 200 avec statut ok."""
    reponse = client_api.get("/api/v1/sante")
    assert reponse.status_code == status.HTTP_200_OK
    assert reponse.data == {"statut": "ok", "version": "0.1.0"}


@pytest.mark.integration
@pytest.mark.django_db
def test_sante_pret_readiness(client_api: APIClient) -> None:
    """GET /api/v1/sante/pret renvoie 200 quand la base est accessible
    et sans migrations en attente."""
    reponse = client_api.get("/api/v1/sante/pret")
    assert reponse.status_code == status.HTTP_200_OK
    assert reponse.data["statut"] == "pret"
    assert reponse.data["base"] == "accessible"
    assert reponse.data["migrations_en_attente"] == 0

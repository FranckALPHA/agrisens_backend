"""Tests d'intégration de l'API des listes de référence."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiels_regions(client_api: APIClient) -> None:
    """GET /api/v1/referentiels/region renvoie les 10 régions du Cameroun."""
    reponse = client_api.get("/api/v1/referentiels/region")
    assert reponse.status_code == status.HTTP_200_OK
    assert len(reponse.data) == 10
    codes = [r["code"] for r in reponse.data]
    assert "centre" in codes
    assert "littoral" in codes
    assert "ouest" in codes


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiels_cultures(client_api: APIClient) -> None:
    """GET /api/v1/referentiels/culture renvoie les cultures prévues par Q-10."""
    reponse = client_api.get("/api/v1/referentiels/culture")
    assert reponse.status_code == status.HTTP_200_OK
    codes = [c["code"] for c in reponse.data]
    assert "tomate" in codes
    assert "mais" in codes
    assert "cacao" in codes
    assert "manioc" in codes
    assert "plantain" in codes


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiels_type_inconnu_renvoie_404(client_api: APIClient) -> None:
    """GET /api/v1/referentiels/type_inconnu renvoie 404 au format d'erreur unique."""
    reponse = client_api.get("/api/v1/referentiels/inexistant")
    assert reponse.status_code == status.HTTP_404_NOT_FOUND
    assert "erreur" in reponse.data
    assert reponse.data["erreur"]["code"] == "referentiel_inconnu"

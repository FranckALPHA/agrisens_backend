"""Tests d'intégration du format d'erreur unique de l'API et absence de HTML."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.integration
@pytest.mark.django_db
def test_route_inconnue_renvoie_json_jamais_html(client_api: APIClient) -> None:
    """Une route non existante renvoie 404 en JSON au format unique AgriSens."""
    reponse = client_api.get("/api/v1/cette_route_n_existe_pas")
    assert reponse.status_code == status.HTTP_404_NOT_FOUND
    assert reponse["Content-Type"].startswith("application/json")

    data = reponse.json()
    assert "erreur" in data
    assert data["erreur"]["code"] == "ressource_introuvable"
    assert "introuvable" in data["erreur"]["message"].lower()


@pytest.mark.integration
@pytest.mark.django_db
def test_methode_non_autorisee_renvoie_format_json(client_api: APIClient) -> None:
    """Une méthode non autorisée renvoie 405 en JSON."""
    # POST sur /sante qui n'accepte que GET
    reponse = client_api.post("/api/v1/sante", {})
    assert reponse.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert reponse["Content-Type"].startswith("application/json")
    data = reponse.json()
    assert "erreur" in data
    assert data["erreur"]["code"] == "methode_non_autorisee"


@pytest.mark.integration
@pytest.mark.django_db
def test_erreur_validation_donnees_renvoie_422(client_api: APIClient) -> None:
    """Données de requête invalides renvoient 422 en JSON."""
    reponse = client_api.post("/api/v1/dev/horloge", {"date_iso": "date_non_valide"}, format="json")
    assert reponse.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = reponse.json()
    assert "erreur" in data
    assert data["erreur"]["code"] == "format_date_invalide"

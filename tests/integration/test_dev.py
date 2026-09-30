"""Tests d'intégration des routes réservées aux tests (dev/horloge, dev/taches)."""

from collections.abc import Iterator
from importlib import reload

import config.urls
import pytest
from commun.horloge import maintenant, reinitialiser
from config.routes_dev import enregistrer_tache_dev
from django.test import override_settings
from django.urls import clear_url_caches
from rest_framework import status
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def nettoyer_horloge() -> Iterator[None]:
    reinitialiser()
    yield
    reinitialiser()


@pytest.mark.integration
@pytest.mark.django_db
def test_dev_horloge_decalage_et_reset(client_api: APIClient) -> None:
    """POST /api/v1/dev/horloge décale l'heure et DELETE la réinitialise."""
    t0 = maintenant()

    # Décalage de 120 minutes
    resp = client_api.post("/api/v1/dev/horloge", {"decalage_minutes": 120}, format="json")
    assert resp.status_code == status.HTTP_200_OK
    assert "maintenant" in resp.data

    t_apres = maintenant()
    assert (t_apres - t0).total_seconds() >= 7100

    # Réinitialisation
    resp_del = client_api.delete("/api/v1/dev/horloge")
    assert resp_del.status_code == status.HTTP_200_OK
    assert resp_del.data["statut"] == "reinitialise"


@pytest.mark.integration
@pytest.mark.django_db
def test_dev_tache_declenchement_immediat(client_api: APIClient) -> None:
    """POST /api/v1/dev/taches/{nom} exécute une tâche déclarée."""
    compteur = [0]

    def _tache_test() -> str:
        compteur[0] += 1
        return "succes"

    enregistrer_tache_dev("tache_demonstration", _tache_test)

    resp = client_api.post("/api/v1/dev/taches/tache_demonstration")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["tache"] == "tache_demonstration"
    assert resp.data["statut"] == "executee"
    assert compteur[0] == 1

    # Tâche inexistante -> 404
    resp_inconnue = client_api.post("/api/v1/dev/taches/tache_inexistante")
    assert resp_inconnue.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
def test_dev_routes_absentes_en_production(client_api: APIClient) -> None:
    """En environnement staging ou production, les routes /api/v1/dev/ ne sont pas montées."""
    with override_settings(ENVIRONNEMENT="production"):
        clear_url_caches()
        reload(config.urls)
        reponse = client_api.get("/api/v1/dev/horloge")
        assert reponse.status_code == status.HTTP_404_NOT_FOUND

    # Restauration de l'état test
    clear_url_caches()
    reload(config.urls)

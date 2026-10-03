"""Tests d'intégration API F-12 : plannings & recommandations IA (SPEC-08).

Parcours complet via le fournisseur factice (aucun réseau), permissions par
rôle, quota, idempotence et immuabilité de la réponse originale. `ENVIRONNEMENT`
vaut `test` et `IA_FOURNISSEUR` vaut `factice` (config/settings/test.py), donc
`?test_simule=…` est honoré.
"""

from __future__ import annotations

from typing import Any

import pytest
from apps.accompagnements.models import Accompagnement, Modalite, StatutAccompagnement
from apps.exploitations.models import Exploitation
from apps.plannings.models import GenerationIA
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"


def _exploitation_agri(client: APIClient, nom: str = "Champ F12") -> dict[str, Any]:
    rep = client.post(
        f"{API}/exploitations",
        {"nom": nom, "parcelle": polygone_yaounde(), "type_sol": "argileux", "region": "centre"},
        format="json",
    )
    assert rep.status_code == 201, rep.data
    return rep.json()


def _affecter_ingenieur(exploitation_id: str, ingenieur: Any, agriculteur: Any) -> None:
    """Un ingénieur affecté à un accompagnement actif peut lire/corriger."""
    exploitation = Exploitation.objects.get(id=exploitation_id)
    Accompagnement.objects.create(
        agriculteur=agriculteur,
        exploitation=exploitation,
        ingenieur=ingenieur,
        modalite=Modalite.EN_LIGNE,
        motif="Vérification planning",
        statut=StatutAccompagnement.ACCEPTEE,
    )


def _generer_planning(client: APIClient, exploitation_id: str, culture: str = "tomate") -> dict:
    rep = client.post(
        f"{API}/exploitations/{exploitation_id}/plannings",
        {
            "culture": culture,
            "periode_debut": "2026-01-01",
            "periode_fin": "2026-05-01",
        },
        format="json",
    )
    assert rep.status_code == 201, rep.data
    return rep.json()


@pytest.mark.integration
def test_parcours_complet_planning_et_correction(client_authentifie: Any) -> None:
    agri_client, agri = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    ing_client, ing = client_authentifie(role="ingenieur_agronome")
    _affecter_ingenieur(exploitation["id"], ing, agri)

    detail = _generer_planning(agri_client, exploitation["id"])
    assert detail["statut"] == "reussie"
    assert detail["reponse_originale"] is not None
    assert detail["avertissement"]
    generation_id = detail["id"]

    # Le détail est accessible au propriétaire comme à l'ingénieur affecté.
    v_agri = agri_client.get(f"{API}/generations-ia/{generation_id}")
    assert v_agri.status_code == 200, v_agri.data
    v_ing = ing_client.get(f"{API}/generations-ia/{generation_id}")
    assert v_ing.status_code == 200, v_ing.data

    originale = v_agri.json()["reponse_originale"]

    correction = ing_client.post(
        f"{API}/generations-ia/{generation_id}/corrections",
        {"commentaire": "À confirmer sur le terrain.", "contenu_corrige": None},
        format="json",
    )
    assert correction.status_code == 201, correction.data

    apres = agri_client.get(f"{API}/generations-ia/{generation_id}").json()
    assert apres["reponse_originale"] == originale  # jamais mutée
    assert len(apres["corrections"]) == 1
    assert apres["corrections"][0]["ingenieur_nom"] == ing.nom


@pytest.mark.integration
def test_permissions_correction(client_authentifie: Any) -> None:
    agri_client, agri = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    detail = _generer_planning(agri_client, exploitation["id"])
    generation_id = detail["id"]

    # Ingénieur non affecté → 404.
    ing_seul_client, _ = client_authentifie(role="ingenieur_agronome")
    rep = ing_seul_client.post(
        f"{API}/generations-ia/{generation_id}/corrections",
        {"commentaire": "x"},
        format="json",
    )
    assert rep.status_code == 404, rep.data

    # Agriculteur non propriétaire → 404 (resource privée invisibile).
    autre_agri_client, _ = client_authentifie(role="agriculteur")
    rep2 = autre_agri_client.post(
        f"{API}/generations-ia/{generation_id}/corrections",
        {"commentaire": "x"},
        format="json",
    )
    assert rep2.status_code == 404, rep2.data

    # Acheteur → 403 (rôle hors périmètre dès la permission de vue).
    acheteur_client, _ = client_authentifie(role="acheteur")
    rep3 = acheteur_client.post(
        f"{API}/generations-ia/{generation_id}/corrections",
        {"commentaire": "x"},
        format="json",
    )
    assert rep3.status_code == 403, rep3.data


@pytest.mark.integration
def test_detail_generation_invisible_aux_tiers(client_authentifie: Any) -> None:
    agri_client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    generation_id = _generer_planning(agri_client, exploitation["id"])["id"]

    autre_client, _ = client_authentifie(role="agriculteur")
    assert autre_client.get(f"{API}/generations-ia/{generation_id}").status_code == 404
    ing_client, _ = client_authentifie(role="ingenieur_agronome")
    assert ing_client.get(f"{API}/generations-ia/{generation_id}").status_code == 404


@pytest.mark.integration
def test_quota_depassement_429(client_authentifie: Any) -> None:
    agri_client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    _generer_planning(agri_client, exploitation["id"], "tomate")
    _generer_planning(agri_client, exploitation["id"], "maïs")

    rep = agri_client.post(
        f"{API}/exploitations/{exploitation['id']}/plannings",
        {"culture": "niéré", "periode_debut": "2026-01-01", "periode_fin": "2026-05-01"},
        format="json",
    )
    assert rep.status_code == 429, rep.data
    corps = rep.json()["erreur"]
    assert corps["code"] == "quota_atteint"
    assert corps["details"]["limite"] == 2
    assert corps["details"]["consomme"] == 2
    assert "date_reinitialisation" in corps["details"]


@pytest.mark.integration
def test_echec_simule_non_decompte(client_authentifie: Any) -> None:
    agri_client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)

    avant = agri_client.get(f"{API}/ia/quota").json()["planning"]["consomme"]
    rep = agri_client.post(
        f"{API}/exploitations/{exploitation['id']}/plannings?test_simule=echec",
        {"culture": "tomate", "periode_debut": "2026-01-01", "periode_fin": "2026-05-01"},
        format="json",
    )
    assert rep.status_code == 201, rep.data
    assert rep.json()["statut"] == "echouee"
    apres = agri_client.get(f"{API}/ia/quota").json()["planning"]["consomme"]
    assert apres == avant  # non décompté


@pytest.mark.integration
def test_idempotence_rejoue(client_authentifie: Any) -> None:
    agri_client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    corps = {"culture": "tomate", "periode_debut": "2026-01-01", "periode_fin": "2026-05-01"}
    entete = {"HTTP_IDEMPOTENCY_KEY": "CLE-API-1"}

    premier = agri_client.post(
        f"{API}/exploitations/{exploitation['id']}/plannings", corps, format="json", **entete
    )
    second = agri_client.post(
        f"{API}/exploitations/{exploitation['id']}/plannings", corps, format="json", **entete
    )
    assert premier.status_code == 201
    assert second.json()["id"] == premier.json()["id"]
    assert GenerationIA.objects.filter(cle_idempotence="CLE-API-1").count() == 1


@pytest.mark.integration
def test_historique_retourne_uniquement_les_siennes(client_authentifie: Any) -> None:
    agri_client, agri = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    _generer_planning(agri_client, exploitation["id"])

    autre_client, _ = client_authentifie(role="agriculteur")
    # L'historique d'une exploitation d'autrui est invisible (404).
    rep = autre_client.get(f"{API}/exploitations/{exploitation['id']}/plannings")
    assert rep.status_code == 404, rep.data

    ma_liste = agri_client.get(f"{API}/exploitations/{exploitation['id']}/plannings").json()
    ids = [element["id"] for element in ma_liste["elements"]]
    assert len(ids) == 1

    # Un ingénieur affecté y a accès.
    ing_client, ing = client_authentifie(role="ingenieur_agronome")
    _affecter_ingenieur(exploitation["id"], ing, agri)
    vue_ing = ing_client.get(f"{API}/exploitations/{exploitation['id']}/plannings")
    assert vue_ing.status_code == 200, vue_ing.data


@pytest.mark.integration
def test_recommandation_quota_separe(client_authentifie: Any) -> None:
    agri_client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation_agri(agri_client)
    rep = agri_client.post(
        f"{API}/exploitations/{exploitation['id']}/recommandations",
        {"sujet": "jaunissement des feuilles", "culture": "tomate", "contexte": "depuis 10 j"},
        format="json",
    )
    assert rep.status_code == 201, rep.data
    assert rep.json()["type"] == "recommandation"
    quota = agri_client.get(f"{API}/ia/quota").json()
    assert quota["recommandation"]["consomme"] == 1
    assert quota["planning"]["consomme"] == 0  # quota distinct


@pytest.mark.integration
def test_entrees_api_sans_donnee_personnelle(client_authentifie: Any) -> None:
    agri_client, agri = client_authentifie(role="agriculteur", nom="Paul Mbianda")
    exploitation = _exploitation_agri(agri_client)
    detail = _generer_planning(agri_client, exploitation["id"])
    generation = GenerationIA.objects.get(id=detail["id"])
    # Aucune clé personnelle dans le payload persisté.
    assert set(generation.entrees.keys()) == {
        "culture",
        "sol",
        "superficie_m2",
        "periode",
        "region",
        "centre",
        "infos_agronomiques",
    }
    valeurs_plattes = str(generation.entrees)
    assert agri.nom not in valeurs_plattes
    assert agri.telephone not in valeurs_plattes

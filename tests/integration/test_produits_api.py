"""Tests d'intégration API F-05 : produits, catalogue, stock, photos (SPEC-03)."""

import io
import threading
from decimal import Decimal
from typing import Any

import pytest
from apps.fichiers.services import ServiceFichiers
from apps.fichiers.stockage import StockageDisque
from apps.produits.models import StatutProduit
from apps.produits.services import consommer, liberer, reserver
from commun.exceptions import Conflit
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"
PRODUITS = f"{API}/produits"


@pytest.fixture
def service_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> ServiceFichiers:
    """Stockage temporaire (même pattern que les tests F-03)."""
    service = ServiceFichiers(stockage=StockageDisque(tmp_path))
    monkeypatch.setattr("apps.fichiers.views.service_fichiers", service)
    return service


def _jpeg() -> bytes:
    tampon = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(tampon, format="JPEG")
    return tampon.getvalue()


def _exploitation(client: APIClient, nom: str = "Champ") -> dict[str, Any]:
    reponse = client.post(
        "/api/v1/exploitations",
        {
            "nom": nom,
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()


def _produit(
    client: APIClient,
    exploitation_id: str,
    nom: str = "Tomates",
    quantite: str = "100",
    **surcharge: Any,
) -> dict[str, Any]:
    donnees = {
        "nom": nom,
        "categorie": "legumes",
        "unite": "kg",
        "prix_unitaire_fcfa": 500,
        "quantite_stock": quantite,
        "exploitation_id": exploitation_id,
    }
    donnees.update(surcharge)
    reponse = client.post(PRODUITS, donnees, format="json")
    assert reponse.status_code == 201, reponse.data
    return reponse.json()


def _upload_image(client: APIClient) -> str:
    reponse = client.post(
        f"{API}/fichiers",
        {
            "fichier": SimpleUploadedFile("tomates.jpg", _jpeg(), content_type="image/jpeg"),
            "contexte": "produit",
        },
        format="multipart",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()["id"]


@pytest.mark.integration
@pytest.mark.django_db
def test_creation_par_proprietaire_et_statut_disponible(client_authentifie) -> None:
    client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(client)
    produit = _produit(client, exploitation["id"])
    assert produit["statut"] == "disponible"
    assert produit["quantite_disponible"] == "100.000"
    assert produit["quantite_reservee"] == "0.000"


@pytest.mark.integration
@pytest.mark.django_db
def test_creation_stock_nul_donne_epuise(client_authentifie) -> None:
    client, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(client)
    produit = _produit(client, exploitation["id"], quantite="0")
    assert produit["statut"] == "epuise"


@pytest.mark.integration
@pytest.mark.django_db
def test_refus_dans_exploitation_d_autrui_et_archivee(client_authentifie) -> None:
    proprietaire, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(proprietaire)
    autre, _ = client_authentifie(role="agriculteur")
    reponse = autre.post(
        PRODUITS,
        {
            "nom": "Vol",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 100,
            "quantite_stock": "10",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    )
    assert reponse.status_code == 404
    archive = proprietaire.post(f"/api/v1/exploitations/{exploitation['id']}/archiver")
    assert archive.status_code == 200
    reponse = proprietaire.post(
        PRODUITS,
        {
            "nom": "Tardif",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 100,
            "quantite_stock": "10",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    )
    assert reponse.status_code == 422
    assert reponse.json()["erreur"]["code"] == "exploitation_inactive"


@pytest.mark.integration
@pytest.mark.django_db
def test_catalogue_public_filtres_tri_pagination_et_vie_privee(
    client_authentifie, client_api
) -> None:
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(paul, nom="Champ catalogue")
    _produit(paul, exploitation["id"], nom="Tomates rondes", quantite="100")
    _produit(
        paul,
        exploitation["id"],
        nom="Maïs doux",
        quantite="50",
        categorie="cereales",
        prix_unitaire_fcfa=300,
    )
    cache = _produit(paul, exploitation["id"], nom="Gombo caché", quantite="10")
    assert paul.post(f"{PRODUITS}/{cache['id']}/retirer").status_code == 200

    catalogue = client_api.get(f"{PRODUITS}?categorie=legumes")
    assert catalogue.status_code == 200
    corps = catalogue.json()
    assert set(corps) == {"elements", "page", "taille", "total"}
    assert [e["nom"] for e in corps["elements"]] == ["Tomates rondes"]

    region = client_api.get(f"{PRODUITS}?region=ouest")
    assert region.json()["total"] == 0
    texte = client_api.get(f"{PRODUITS}?q=maïs")
    assert texte.json()["total"] == 1
    prix = client_api.get(f"{PRODUITS}?prix_min=400&prix_max=600")
    assert prix.json()["total"] == 1
    tri = client_api.get(f"{PRODUITS}?tri=prix_asc")
    noms = [e["nom"] for e in tri.json()["elements"]]
    assert noms == ["Maïs doux", "Tomates rondes"]
    page = client_api.get(f"{PRODUITS}?taille=1&page=2")
    assert page.json()["total"] == 2 and len(page.json()["elements"]) == 1

    # Vie privée (D-21) : ni téléphone (+237), ni coordonnées, ni identifiants.
    import json as _json

    brut = _json.dumps(client_api.get(PRODUITS).json())
    assert "+237" not in brut and "quantite_reservee" not in brut
    assert "agriculteur_id" not in brut and "position" not in brut
    detail = client_api.get(f"{PRODUITS}/{corps['elements'][0]['id']}")
    assert detail.json()["producteur_nom"]
    assert "telephone" not in detail.json()


@pytest.mark.integration
@pytest.mark.django_db
def test_detail_public_masque_apres_retrait(client_authentifie, client_api) -> None:
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(paul)
    produit = _produit(paul, exploitation["id"])
    assert client_api.get(f"{PRODUITS}/{produit['id']}").status_code == 200
    assert paul.post(f"{PRODUITS}/{produit['id']}/retirer").status_code == 200
    reponse = client_api.get(f"{PRODUITS}/{produit['id']}")
    assert reponse.status_code == 404
    assert reponse.json()["erreur"]["code"] == "produit_indisponible"
    # Le propriétaire le voit encore (retiré) pour son historique.
    prive = paul.get(f"{PRODUITS}/{produit['id']}")
    assert prive.status_code == 200
    assert prive.json()["statut"] == "retire"


@pytest.mark.integration
@pytest.mark.django_db
def test_modification_et_stock_par_proprietaire_uniquement(client_authentifie) -> None:
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(paul)
    produit = _produit(paul, exploitation["id"])
    autre, _ = client_authentifie(role="agriculteur")
    assert (
        autre.patch(f"{PRODUITS}/{produit['id']}", {"nom": "X"}, format="json").status_code == 404
    )
    modifie = paul.patch(f"{PRODUITS}/{produit['id']}", {"prix_unitaire_fcfa": 600}, format="json")
    assert modifie.status_code == 200
    assert modifie.json()["prix_unitaire_fcfa"] == 600
    # Stock sous le réservé : refusé.
    reserver(produit["id"], "30")
    refuse = paul.patch(
        f"{PRODUITS}/{produit['id']}/stock", {"quantite_stock": "20"}, format="json"
    )
    assert refuse.status_code == 422
    assert refuse.json()["erreur"]["code"] == "stock_inferieur_reserve"
    # Réserve libérée, puis stock à zéro accepté (passage à épuisé).
    liberer(produit["id"], "30")
    ajuste = paul.patch(f"{PRODUITS}/{produit['id']}/stock", {"quantite_stock": "0"}, format="json")
    assert ajuste.status_code == 200
    assert ajuste.json()["statut"] == "epuise"


@pytest.mark.integration
@pytest.mark.django_db
def test_mouvements_de_stock_et_epuise_automatique(client_authentifie) -> None:
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(paul)
    produit = _produit(paul, exploitation["id"], quantite="10")
    reserver(produit["id"], "10")
    from apps.produits.models import Produit

    assert Produit.objects.get(id=produit["id"]).statut == StatutProduit.EPUISE
    consommer(produit["id"], "10")
    frais = Produit.objects.get(id=produit["id"])
    assert frais.quantite_stock == Decimal("0")
    assert frais.quantite_reservee == Decimal("0")


@pytest.mark.integration
@pytest.mark.django_db
def test_photos_produit_ordre_et_limites(client_authentifie, client_api, service_tmp) -> None:
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(paul)
    produit = _produit(paul, exploitation["id"])
    identifiants = [_upload_image(paul) for _ in range(5)]
    for rang, fichier_id in enumerate(identifiants):
        ajout = paul.post(
            f"{PRODUITS}/{produit['id']}/photos", {"fichier_id": fichier_id}, format="json"
        )
        assert ajout.status_code == 201, ajout.data
        assert ajout.json()["ordre"] == rang
    # 6e photo : limite paramétrée (5) → 422.
    trop = paul.post(
        f"{PRODUITS}/{produit['id']}/photos",
        {"fichier_id": _upload_image(paul)},
        format="json",
    )
    assert trop.status_code == 422
    assert trop.json()["erreur"]["code"] == "trop_de_photos"
    # Fichier d'un autre : 404. Non-image : 422.
    autre, _ = client_authentifie(role="agriculteur")
    assert (
        autre.post(
            f"{PRODUITS}/{produit['id']}/photos",
            {"fichier_id": identifiants[0]},
            format="json",
        ).status_code
        == 404
    )
    # Réordonner : inverser l'ordre.
    inverse = list(reversed(identifiants))
    ordre = paul.put(f"{PRODUITS}/{produit['id']}/photos", {"fichier_ids": inverse}, format="json")
    assert ordre.status_code == 200
    detail = paul.get(f"{PRODUITS}/{produit['id']}").json()
    assert [p["fichier_id"] for p in detail["photos"]] == inverse
    # Dissocier.
    assert paul.delete(f"{PRODUITS}/{produit['id']}/photos/{inverse[0]}").status_code == 204
    # Photo visible au public via le lien catalogue (anonyme).
    publique = client_api.get(f"{API}/fichiers/{inverse[1]}/", follow=False)
    assert publique.status_code == 302


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_concurrence_deux_reservations_dernier_stock(client_authentifie) -> None:
    """Deux réservations simultanées du dernier stock : une seule réussit (MySQL).

    TransactionTestCase vide la base partagée (--reuse-db) en teardown ; la
    fixture `_donnees_migration_garanties` (conftest) réinsère les données de
    migration pour les tests suivants, quel que soit l'ordre d'exécution.
    """
    from django.db import connections

    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _exploitation(paul, nom="Champ concurrence")
    produit = _produit(paul, exploitation["id"], nom="Stock unique", quantite="5")
    barriere = threading.Barrier(2)
    resultats: list[str] = []

    def tenter() -> None:
        barriere.wait()
        try:
            reserver(produit["id"], "5")
            resultats.append("ok")
        except Conflit:
            resultats.append("conflit")

    try:
        fils = [threading.Thread(target=tenter) for _ in range(2)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        assert sorted(resultats) == ["conflit", "ok"]
        from apps.produits.models import Produit

        final = Produit.objects.get(id=produit["id"])
        assert final.quantite_reservee == Decimal("5")
        liberer(produit["id"], "5")
    finally:
        connections.close_all()


@pytest.mark.integration
@pytest.mark.django_db
def test_gardes_collection_et_mes_produits(client_authentifie, client_api) -> None:
    assert client_api.post(PRODUITS, {}, format="json").status_code in (401, 403, 422)
    acheteur, _ = client_authentifie(role="acheteur")
    assert acheteur.post(PRODUITS, {}, format="json").status_code == 403
    assert acheteur.get(f"{PRODUITS}/mes-produits").status_code == 403
    paul, _ = client_authentifie(role="agriculteur")
    assert paul.get(f"{PRODUITS}/mes-produits").json()["total"] == 0

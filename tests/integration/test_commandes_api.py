"""Tests d'intégration API F-06 : devis, commandes, transitions, expiration (SPEC-04)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from apps.commandes.models import Commande, StatutCommande
from apps.commandes.taches import expirer_commandes
from apps.produits.models import Produit
from commun.horloge import decaler, reinitialiser
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"
COMMANDES = f"{API}/commandes"
POINT_YAOUNDE = {"type": "Point", "coordinates": [11.5137, 3.848]}


@pytest.fixture
def exploitation_paul(client_authentifie: Any) -> tuple[APIClient, Any, dict[str, Any]]:
    paul, paul_obj = client_authentifie(role="agriculteur")
    reponse = paul.post(
        "/api/v1/exploitations",
        {
            "nom": "Champ commandes",
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    return paul, paul_obj, reponse.json()


@pytest.fixture
def tomates(
    exploitation_paul: tuple[APIClient, Any, dict[str, Any]],
) -> dict[str, Any]:
    paul, _, exploitation = exploitation_paul
    reponse = paul.post(
        "/api/v1/produits",
        {
            "nom": "Tomates commande",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 500,
            "quantite_stock": "100",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()


def _adresse_amina(amina: APIClient) -> str:
    reponse = amina.post(
        "/api/v1/utilisateurs/moi/adresses",
        {
            "libelle": "Domicile",
            "description": "Près du marché",
            "position": POINT_YAOUNDE,
            "ville": "Yaoundé",
            "region": "centre",
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()["id"]


def _commande_10kg(amina: APIClient, adresse_id: str, produit_id: str) -> dict[str, Any]:
    reponse = amina.post(
        COMMANDES,
        {
            "lignes": [{"produit_id": produit_id, "quantite": "10"}],
            "adresse_id": adresse_id,
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()


@pytest.mark.integration
@pytest.mark.django_db
def test_devis_sans_creation_et_hors_zone(client_authentifie, exploitation_paul, tomates) -> None:
    amina, _ = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    devis = amina.post(
        f"{COMMANDES}/devis",
        {"lignes": [{"produit_id": tomates["id"], "quantite": "10"}], "adresse_id": adresse_id},
        format="json",
    )
    assert devis.status_code == 200, devis.data
    corps = devis.json()
    assert corps["sous_total_fcfa"] == 5_000
    assert corps["frais_livraison_fcfa"] == corps["total_fcfa"] - 5_000
    assert corps["distance_km"] >= 0
    assert Commande.objects.count() == 0

    loin = amina.post(
        f"{COMMANDES}/devis",
        {
            "lignes": [{"produit_id": tomates["id"], "quantite": "10"}],
            "adresse_texte": "Village lointain",
            "position": {"type": "Point", "coordinates": [11.5137, 5.0]},
        },
        format="json",
    )
    assert loin.status_code == 422
    assert loin.json()["erreur"]["code"] == "hors_zone_livraison"


@pytest.mark.integration
@pytest.mark.django_db
def test_creation_copies_figees_reference_et_expiration(
    client_authentifie, exploitation_paul, tomates
) -> None:
    amina, _ = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    commande = _commande_10kg(amina, adresse_id, tomates["id"])
    assert commande["reference"].startswith("CMD-")
    assert commande["statut"] == "en_attente"
    assert commande["total_fcfa"] == commande["sous_total_fcfa"] + commande["frais_livraison_fcfa"]
    assert commande["expire_le"] is not None
    assert commande["parametres_appliques"]["livraison.tarif_km_fcfa"] == 150
    # Stock vérifié mais pas encore réservé (D-03).
    produit = Produit.objects.get(id=tomates["id"])
    assert produit.quantite_reservee == Decimal("0")
    assert produit.quantite_disponible == Decimal("100")


@pytest.mark.integration
@pytest.mark.django_db
def test_multi_agriculteurs_vide_et_stock_refuses(
    client_authentifie, exploitation_paul, tomates
) -> None:
    amina, _ = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    autre, _ = client_authentifie(role="agriculteur")
    exploitation = autre.post(
        "/api/v1/exploitations",
        {
            "nom": "Champ voisin",
            "parcelle": polygone_yaounde(),
            "type_sol": "sableux",
            "region": "centre",
        },
        format="json",
    ).json()
    oignons = autre.post(
        "/api/v1/produits",
        {
            "nom": "Oignons",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 400,
            "quantite_stock": "50",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    ).json()
    multi = amina.post(
        COMMANDES,
        {
            "lignes": [
                {"produit_id": tomates["id"], "quantite": "10"},
                {"produit_id": oignons["id"], "quantite": "5"},
            ],
            "adresse_id": adresse_id,
        },
        format="json",
    )
    assert multi.status_code == 422
    assert multi.json()["erreur"]["code"] == "commande_multi_agriculteurs"
    vide = amina.post(COMMANDES, {"lignes": [], "adresse_id": adresse_id}, format="json")
    assert vide.status_code == 422
    trop = amina.post(
        COMMANDES,
        {"lignes": [{"produit_id": tomates["id"], "quantite": "1000"}], "adresse_id": adresse_id},
        format="json",
    )
    assert trop.status_code == 409
    assert trop.json()["erreur"]["code"] == "stock_insuffisant"


@pytest.mark.integration
@pytest.mark.django_db
def test_accepter_reserve_rejeter_motif_annuler_libere(
    client_authentifie, exploitation_paul, tomates
) -> None:
    paul, _ = exploitation_paul[0], exploitation_paul[1]
    amina, _ = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)

    commande = _commande_10kg(amina, adresse_id, tomates["id"])
    acceptee = paul.post(f"{COMMANDES}/{commande['id']}/accepter")
    assert acceptee.status_code == 200, acceptee.data
    assert acceptee.json()["statut"] == "acceptee"
    assert Produit.objects.get(id=tomates["id"]).quantite_reservee == Decimal("10")

    # Rejet par un autre agriculteur : 404.
    autre, _ = client_authentifie(role="agriculteur")
    assert (
        autre.post(
            f"{COMMANDES}/{commande['id']}/rejeter", {"motif": "x"}, format="json"
        ).status_code
        == 404
    )

    # Rejet sans motif : 422 ; avec motif : rejetée.
    commande2 = _commande_10kg(amina, adresse_id, tomates["id"])
    assert (
        paul.post(
            f"{COMMANDES}/{commande2['id']}/rejeter", {"motif": " "}, format="json"
        ).status_code
        == 422
    )
    rejet = paul.post(
        f"{COMMANDES}/{commande2['id']}/rejeter",
        {"motif": "Rupture de main d'œuvre"},
        format="json",
    )
    assert rejet.status_code == 200
    assert rejet.json()["statut"] == "rejetee"
    assert rejet.json()["motif_rejet"] == "Rupture de main d'œuvre"

    # Annulation par l'acheteur : stock libéré.
    annulee = amina.post(f"{COMMANDES}/{commande['id']}/annuler")
    assert annulee.status_code == 200
    assert annulee.json()["statut"] == "annulee"
    assert Produit.objects.get(id=tomates["id"]).quantite_reservee == Decimal("0")

    # Transitions invalides : 409.
    assert paul.post(f"{COMMANDES}/{commande['id']}/accepter").status_code == 409
    assert amina.post(f"{COMMANDES}/{commande2['id']}/annuler").status_code == 409


@pytest.mark.integration
@pytest.mark.django_db
def test_preparer_depuis_payee_et_historique(
    client_authentifie, exploitation_paul, tomates
) -> None:
    paul, _ = exploitation_paul[0], exploitation_paul[1]
    amina, amina_obj = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    commande = _commande_10kg(amina, adresse_id, tomates["id"])
    # Paiement simulé en fixture (F-07 branchera le webhook) : passage direct à payee.
    Commande.objects.filter(id=commande["id"]).update(statut=StatutCommande.PAYEE)
    preparee = paul.post(f"{COMMANDES}/{commande['id']}/preparer")
    assert preparee.status_code == 200
    assert preparee.json()["statut"] == "preparation"

    historique = amina.get(f"{COMMANDES}/{commande['id']}/historique")
    assert historique.status_code == 200
    statuts = [ligne["nouveau_statut"] for ligne in historique.json()]
    assert statuts == ["en_attente", "preparation"]


@pytest.mark.integration
@pytest.mark.django_db
def test_visibilite_et_adresse_masquee_avant_paiement(
    client_authentifie, exploitation_paul, tomates
) -> None:
    paul, _ = exploitation_paul[0], exploitation_paul[1]
    amina, _ = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    commande = _commande_10kg(amina, adresse_id, tomates["id"])

    autre_acheteur, _ = client_authentifie(role="acheteur")
    assert autre_acheteur.get(f"{COMMANDES}/{commande['id']}").status_code == 404

    # Avant paiement : Paul ne voit que la zone, pas l'adresse exacte.
    paul.post(f"{COMMANDES}/{commande['id']}/accepter")
    vue_paul = paul.get(f"{COMMANDES}/{commande['id']}").json()
    assert vue_paul["adresse_exacte_visible"] is False
    assert "marché" not in vue_paul["adresse_livraison"].lower()
    # Amina voit son adresse exacte.
    vue_amina = amina.get(f"{COMMANDES}/{commande['id']}").json()
    assert vue_amina["adresse_exacte_visible"] is True
    assert "marché" in vue_amina["adresse_livraison"].lower()


@pytest.mark.integration
@pytest.mark.django_db
def test_expiration_annule_et_libere_avec_notifications(
    client_authentifie, exploitation_paul, tomates
) -> None:
    from apps.notifications.models import Notification

    paul, _ = exploitation_paul[0], exploitation_paul[1]
    amina, amina_obj = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    commande = _commande_10kg(amina, adresse_id, tomates["id"])
    paul.post(f"{COMMANDES}/{commande['id']}/accepter")
    assert Produit.objects.get(id=tomates["id"]).quantite_reservee == Decimal("10")

    try:
        decaler(timedelta(hours=25))
        assert expirer_commandes() == 1
    finally:
        reinitialiser()
    assert Produit.objects.get(id=tomates["id"]).quantite_reservee == Decimal("0")
    terminee = Commande.objects.get(id=commande["id"])
    assert terminee.statut == StatutCommande.ANNULEE
    assert Notification.objects.filter(destinataire=amina_obj, type="commande").exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_listes_filtrees_et_gardes(
    client_authentifie, client_api, exploitation_paul, tomates
) -> None:
    amina, _ = client_authentifie(role="acheteur")
    adresse_id = _adresse_amina(amina)
    _commande_10kg(amina, adresse_id, tomates["id"])
    achats = amina.get(f"{COMMANDES}/mes-achats?statut=en_attente")
    assert achats.status_code == 200
    assert achats.json()["total"] == 1
    paul, _ = exploitation_paul[0], exploitation_paul[1]
    ventes = paul.get(f"{COMMANDES}/mes-ventes")
    assert ventes.json()["total"] == 1
    assert client_api.get(f"{COMMANDES}/mes-achats").status_code == 401
    acheteur, _ = client_authentifie(role="acheteur")
    assert acheteur.get(f"{COMMANDES}/mes-ventes").status_code == 403

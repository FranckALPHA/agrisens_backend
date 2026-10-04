"""Tests d'intégration API de la feature F-14 (statistiques, SPEC-11).

Parcours complet par HTTP : garde de rôle des quatre endpoints (401 sans jeton,
403 rôles interdits), chiffres exacts de `/statistiques/mes-ventes` (dont le
filtre `du`/`au` et la `granularite`), erreur 422 `periode_invalide`, séparation
stricte entre deux agriculteurs et agrégats plateforme côté admin.
"""

from typing import Any

import pytest
from apps.commandes.models import Commande, LigneCommande, StatutCommande
from apps.exploitations.models import Exploitation
from apps.produits.models import Produit
from rest_framework.test import APIClient

API = "/api/v1"
POINT: dict[str, Any] = {"type": "Point", "coordinates": [11.5137, 3.848]}
POLYGONE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[11.51, 3.84], [11.52, 3.84], [11.52, 3.85], [11.51, 3.85], [11.51, 3.84]]],
}


def _client(client_api: APIClient, generer_jeton_acces: Any, utilisateur: Any) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {generer_jeton_acces(utilisateur)}")
    return client


def _commande(
    acheteur: Any,
    agriculteur: Any,
    *,
    statut: str,
    sous_total: int,
    commission: int = 0,
) -> Commande:
    return Commande.objects.create(
        reference=f"F14{Commande.objects.count() + 1:06d}",
        acheteur=acheteur,
        agriculteur=agriculteur,
        statut=statut,
        adresse_livraison_texte="Yaoundé, test F-14",
        position_livraison=POINT,
        position_recuperation=POINT,
        distance_km=6,
        sous_total_fcfa=sous_total,
        frais_livraison_fcfa=0,
        total_fcfa=sous_total,
        commission_fcfa=commission,
    )


@pytest.fixture
def scenario(db: Any, fabrique_utilisateur: Any) -> dict[str, Any]:
    amina = fabrique_utilisateur(role="acheteur", nom="Amina")
    paul = fabrique_utilisateur(role="agriculteur", nom="Paul")
    pierre = fabrique_utilisateur(role="agriculteur", nom="Pierre")
    champ = Exploitation.objects.create(
        agriculteur=paul,
        nom="Champ F-14",
        parcelle=POLYGONE,
        centre=POINT,
        superficie_ha=2,
        type_sol="argileux",
        region="centre",
    )
    tomates = Produit.objects.create(
        exploitation=champ,
        nom="Tomates",
        categorie="legumes",
        unite="kg",
        prix_unitaire_fcfa=500,
        quantite_stock=100,
    )
    vendue = _commande(amina, paul, statut=StatutCommande.LIVREE, sous_total=4000, commission=200)
    LigneCommande.objects.create(
        commande=vendue,
        produit=tomates,
        nom_produit="Tomates",
        unite="kg",
        quantite=8,
        prix_unitaire_fcfa=500,
        total_ligne_fcfa=4000,
    )
    _commande(amina, paul, statut=StatutCommande.PAYEE, sous_total=1500, commission=75)
    _commande(amina, pierre, statut=StatutCommande.LIVREE, sous_total=9000, commission=450)
    return {"amina": amina, "paul": paul, "pierre": pierre}


# --- Gardes de rôle --------------------------------------------------------


@pytest.mark.integration
def test_garde_route_statistiques_par_role(db: Any, verifier_garde_route: Any) -> None:
    verifier_garde_route(f"{API}/statistiques/mes-ventes", roles_autorises=["agriculteur"])
    verifier_garde_route(f"{API}/statistiques/mes-livraisons", roles_autorises=["livreur"])
    verifier_garde_route(
        f"{API}/statistiques/mes-accompagnements",
        roles_autorises=["ingenieur_agronome"],
    )
    verifier_garde_route(
        f"{API}/statistiques/plateforme",
        roles_autorises=["admin", "super_admin"],
    )


# --- més-ventes par HTTP ---------------------------------------------------


@pytest.mark.integration
def test_mes_ventes_chiffres_exactes_via_http(
    scenario: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    paul = _client(client_api, generer_jeton_acces, scenario["paul"])
    reponse = paul.get(f"{API}/statistiques/mes-ventes")
    assert reponse.status_code == 200, reponse.content
    corps = reponse.json()
    # Seule la commande livrée compte (pas la payee du même Paul).
    assert corps["nombre_ventes"] == 1
    assert corps["chiffre_affaires_brut_fcfa"] == 4000
    assert corps["commissions_fcfa"] == 200
    assert corps["revenu_net_fcfa"] == 3800
    assert corps["quantite_totale"] == pytest.approx(8.0)
    assert corps["produits_populaires"][0]["nom_produit"] == "Tomates"
    assert corps["produits_populaires"][0]["total_fcfa"] == 4000
    assert corps["serie"], "la série temporelle doit contenir au moins un point"
    assert sum(p["montant_fcfa"] for p in corps["serie"]) == 4000


@pytest.mark.integration
def test_mes_ventes_separation_stricte(
    scenario: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    paul = _client(client_api, generer_jeton_acces, scenario["paul"])
    pierre = _client(client_api, generer_jeton_acces, scenario["pierre"])
    assert paul.get(f"{API}/statistiques/mes-ventes").json()["chiffre_affaires_brut_fcfa"] == 4000
    assert pierre.get(f"{API}/statistiques/mes-ventes").json()["chiffre_affaires_brut_fcfa"] == 9000


@pytest.mark.integration
def test_mes_ventes_periode_et_granularite(
    scenario: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    paul = _client(client_api, generer_jeton_acces, scenario["paul"])
    # Période passée : rien ne correspond.
    vide = paul.get(f"{API}/statistiques/mes-ventes", {"du": "2020-01-01", "au": "2020-01-31"})
    assert vide.status_code == 200
    assert vide.json()["nombre_ventes"] == 0
    assert vide.json()["serie"] == []
    # Granularité inconnue → repli mois sans erreur.
    jour = paul.get(f"{API}/statistiques/mes-ventes", {"granularite": "annee"})
    assert jour.status_code == 200
    serie = jour.json()["serie"]
    assert serie and "-" in serie[0]["periode"] and len(serie[0]["periode"]) == 7


@pytest.mark.integration
def test_mes_ventes_periode_invalide_422(
    scenario: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    paul = _client(client_api, generer_jeton_acces, scenario["paul"])
    reponse = paul.get(f"{API}/statistiques/mes-ventes", {"du": "2026-13-99"})
    assert reponse.status_code == 422
    assert reponse.json()["erreur"]["code"] == "periode_invalide"


# --- Plateforme ------------------------------------------------------------


@pytest.mark.integration
def test_plateforme_agregats_via_http(
    scenario: dict[str, Any],
    client_api: APIClient,
    generer_jeton_acces: Any,
    fabrique_utilisateur: Any,
) -> None:
    mireille = fabrique_utilisateur(role="admin", nom="Mireille")
    admin = _client(client_api, generer_jeton_acces, mireille)
    reponse = admin.get(f"{API}/statistiques/plateforme")
    assert reponse.status_code == 200, reponse.content
    corps = reponse.json()
    assert corps["total_commandes"] == 3
    assert corps["commandes_par_statut"][StatutCommande.LIVREE] == 2
    assert corps["chiffre_affaires_fcfa"] == 13000
    assert corps["revenus_plateforme_fcfa"] == 650
    assert corps["utilisateurs_par_role"]["agriculteur"] >= 2

    # Un acheteur n'a pas accès aux statistiques plateforme → 403.
    amina = _client(client_api, generer_jeton_acces, scenario["amina"])
    refus = amina.get(f"{API}/statistiques/plateforme")
    assert refus.status_code == 403

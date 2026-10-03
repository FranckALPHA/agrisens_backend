"""Tests d'intégration API de la feature F-10 (routes admin + permissions).

Couvre le prompt F-10 : matrice de permissions par rôle pour chaque route,
motif obligatoire, hors bornes 422, audit et export réservés super-admin,
reversements et soldes.
"""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from apps.administration.models import JournalAudit
from apps.administration.services import parametres
from apps.commandes.models import Commande, StatutCommande
from commun import horloge

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"

CLE = "commission.taux_pb"
POINT_YAOUNDE = {"type": "Point", "coordinates": [11.5137, 3.848]}


@pytest.fixture
def tomates_centre(client_authentifie: Any) -> dict[str, Any]:
    """Produit d'exemple chez un agriculteur (pour la commande E2E-12)."""
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = paul.post(
        f"{API}/exploitations",
        {
            "nom": "Champ F-10",
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    ).json()
    return paul.post(
        f"{API}/produits",
        {
            "nom": "Tomates F-10",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 500,
            "quantite_stock": "100",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    ).json()


@pytest.fixture
def commande_livree_paul(client_authentifie: Any) -> tuple[Any, Any]:
    """Commande livrée 12 500 − 625 de commission ; renvoie (client_paul, paul)."""
    paul, paul_obj = client_authentifie(role="agriculteur", nom="Paul")
    _, acheteur = client_authentifie(role="acheteur", nom="Amina")
    Commande.objects.create(
        reference="CF10TEST001",
        acheteur=acheteur,
        agriculteur=paul_obj,
        statut=StatutCommande.LIVREE,
        adresse_livraison_texte="Yaoundé, test F-10",
        position_livraison=POINT_YAOUNDE,
        position_recuperation=POINT_YAOUNDE,
        distance_km=6,
        sous_total_fcfa=12_500,
        frais_livraison_fcfa=1_400,
        total_fcfa=13_900,
        commission_taux_pb=500,
        commission_fcfa=625,
    )
    return paul, paul_obj


@pytest.fixture(autouse=True)
def _horloge_reinitialisee() -> Iterator[None]:
    horloge.reinitialiser()
    yield
    horloge.reinitialiser()


# --- Matrice de permissions -------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_admin_parametres_lecture(verifier_garde_route: Any) -> None:
    """GET /admin/parametres : adm et sadm seulement."""
    verifier_garde_route(f"{API}/admin/parametres", roles_autorises=["admin", "super_admin"])


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_admin_parametres_ecriture(verifier_garde_route: Any) -> None:
    """POST /admin/parametres/{cle} : super-admin seul."""
    verifier_garde_route(
        f"{API}/admin/parametres/{CLE}",
        roles_autorises=["super_admin"],
        methode="post",
        donnees={"valeur": 600, "motif": "Ajustement contrat"},
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_admin_audit(verifier_garde_route: Any) -> None:
    """GET /admin/audit et export : super-admin seul."""
    verifier_garde_route(f"{API}/admin/audit", roles_autorises=["super_admin"])
    verifier_garde_route(f"{API}/admin/audit/export", roles_autorises=["super_admin"])


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_admin_referentiels(verifier_garde_route: Any) -> None:
    """Gestion des référentiels : super-admin seul."""
    verifier_garde_route(
        f"{API}/admin/referentiels/region",
        roles_autorises=["super_admin"],
        methode="post",
        donnees={"code": "nouvelle", "libelle": "Nouvelle région"},
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_soldes_moi(verifier_garde_route: Any) -> None:
    """GET /soldes/moi : agriculteur, livreur et ingénieur seulement."""
    verifier_garde_route(
        f"{API}/soldes/moi", roles_autorises=["agriculteur", "livreur", "ingenieur_agronome"]
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_admin_reversements(verifier_garde_route: Any) -> None:
    """GET/POST /admin/reversements : admin et super-admin."""
    verifier_garde_route(
        f"{API}/admin/reversements", roles_autorises=["admin", "super_admin"]
    )


# --- Paramètres via l'API ---------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_changement_tarif_commande_garde_ancien_calcul(
    client_authentifie: Any, tomates_centre: dict[str, Any]
) -> None:
    """E2E-12 backend : une commande créée avant un changement de tarif
    conserve ses `parametres_appliques` et ses frais figés."""
    paul, _ = client_authentifie(role="agriculteur")
    amina, _ = client_authentifie(role="acheteur")
    adresse = amina.post(
        f"{API}/utilisateurs/moi/adresses",
        {
            "libelle": "Domicile",
            "position": {"type": "Point", "coordinates": [11.5137, 3.848]},
            "ville": "Yaoundé",
            "region": "centre",
        },
        format="json",
    ).json()
    commande = amina.post(
        f"{API}/commandes",
        {
            "lignes": [{"produit_id": tomates_centre["id"], "quantite": "10"}],
            "adresse_id": adresse["id"],
        },
        format="json",
    ).json()
    figes = commande["parametres_appliques"]["livraison.tarif_km_fcfa"]
    frais_avant = commande["frais_livraison_fcfa"]

    sadm, _ = client_authentifie(role="super_admin")
    reponse = sadm.post(
        f"{API}/admin/parametres/livraison.tarif_km_fcfa",
        {"valeur": 200, "motif": "Hausse carburant"},
        format="json",
    )
    assert reponse.status_code == 201, reponse.data

    # L'ancienne commande est inchangée ; le nouveau tarif s'applique désormais.
    relue = amina.get(f"{API}/commandes/{commande['id']}").json()
    assert relue["parametres_appliques"]["livraison.tarif_km_fcfa"] == figes
    assert relue["frais_livraison_fcfa"] == frais_avant
    assert parametres.obtenir("livraison.tarif_km_fcfa") == 200

    # L'audit montre l'ancienne et la nouvelle valeur (E2E-12).
    entree = JournalAudit.objects.filter(action="modifier_parametre").latest("cree_le")
    assert entree.ancienne_valeur["valeur"] == 150
    assert entree.nouvelle_valeur["valeur"] == 200


@pytest.mark.integration
@pytest.mark.django_db
def test_motif_obligatoire_et_hors_bornes_422(client_authentifie: Any) -> None:
    """Le motif est obligatoire et une valeur hors bornes renvoie 422."""
    sadm, _ = client_authentifie(role="super_admin")
    sans_motif = sadm.post(
        f"{API}/admin/parametres/{CLE}", {"valeur": 600}, format="json"
    )
    assert sans_motif.status_code == 422
    assert "motif" in str(sans_motif.json())
    hors_bornes = sadm.post(
        f"{API}/admin/parametres/{CLE}", {"valeur": 99_999, "motif": "Test"}, format="json"
    )
    assert hors_bornes.status_code == 422
    assert hors_bornes.json()["erreur"]["code"] == "valeur_hors_bornes"


@pytest.mark.integration
@pytest.mark.django_db
def test_cle_inconnue_404(client_authentifie: Any) -> None:
    """Une clé hors registre renvoie 404 parametre_inconnu."""
    sadm, _ = client_authentifie(role="super_admin")
    reponse = sadm.post(
        f"{API}/admin/parametres/inconnue.cle", {"valeur": 1, "motif": "Test"}, format="json"
    )
    assert reponse.status_code == 404
    assert reponse.json()["erreur"]["code"] == "parametre_inconnu"


@pytest.mark.integration
@pytest.mark.django_db
def test_date_effet_future_via_api(client_authentifie: Any) -> None:
    """Planifier via l'API : la valeur ne s'applique qu'à son échéance."""
    sadm, _ = client_authentifie(role="super_admin")
    futur = horloge.maintenant() + timedelta(days=3)
    creee = sadm.post(
        f"{API}/admin/parametres/{CLE}",
        {"valeur": 700, "date_effet": futur.isoformat(), "motif": "Entrée en vigueur différée"},
        format="json",
    )
    assert creee.status_code == 201, creee.data

    liste = sadm.get(f"{API}/admin/parametres").json()
    ligne = next(p for p in liste if p["cle"] == CLE)
    assert ligne["valeur"] == 500  # valeur de démarrage toujours en vigueur
    assert [p["valeur"] for p in ligne["planifiees"]] == [700]

    horloge.decaler(timedelta(days=4))
    liste = sadm.get(f"{API}/admin/parametres").json()
    ligne = next(p for p in liste if p["cle"] == CLE)
    assert ligne["valeur"] == 700
    assert ligne["planifiees"] == []


@pytest.mark.integration
@pytest.mark.django_db
def test_historique_et_resturation_via_api(client_authentifie: Any) -> None:
    """Historique complet puis restauration de l'avant-dernière valeur."""
    sadm, _ = client_authentifie(role="super_admin")
    sadm.post(f"{API}/admin/parametres/{CLE}", {"valeur": 600, "motif": "Un"}, format="json")
    sadm.post(f"{API}/admin/parametres/{CLE}", {"valeur": 700, "motif": "Deux"}, format="json")

    historique = sadm.get(f"{API}/admin/parametres/{CLE}/historique").json()
    assert [h["valeur"] for h in historique][:2] == [700, 600]

    restauree = sadm.post(f"{API}/admin/parametres/{CLE}/restaurer", format="json")
    assert restauree.status_code == 201, restauree.data
    assert restauree.json()["valeur"] == 600


@pytest.mark.integration
@pytest.mark.django_db
def test_admin_en_lecture_seule(client_authentifie: Any) -> None:
    """L'admin simple voit les paramètres mais ne peut pas les modifier (403)."""
    adm, _ = client_authentifie(role="admin")
    assert adm.get(f"{API}/admin/parametres").status_code == 200
    refuse = adm.post(
        f"{API}/admin/parametres/{CLE}", {"valeur": 600, "motif": "Non"}
    )
    assert refuse.status_code == 403


# --- Offres ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_offres_lecture_adm_ecriture_sadm(client_authentifie: Any) -> None:
    """GET /admin/offres pour adm ; POST réservé sadm ; versionnement et audit."""
    adm, _ = client_authentifie(role="admin")
    liste = adm.get(f"{API}/admin/offres")
    assert liste.status_code == 200
    codes = [o["code"] for o in liste.json()]
    assert "gratuit" in codes

    refus = adm.post(
        f"{API}/admin/offres",
        {"code": "gratuit", "nom": "Gratuit", "prix_mensuel_fcfa": 0,
         "quota_planning_mois": 3, "quota_recommandation_mois": 5},
        format="json",
    )
    assert refus.status_code == 403

    sadm, _ = client_authentifie(role="super_admin")
    creee = sadm.post(
        f"{API}/admin/offres",
        {"code": "gratuit", "nom": "Gratuit", "prix_mensuel_fcfa": 0,
         "quota_planning_mois": 3, "quota_recommandation_mois": 8},
        format="json",
    )
    assert creee.status_code == 201, creee.data
    assert creee.json()["quota_recommandation_mois"] == 8
    assert JournalAudit.objects.filter(action="modifier_offre").exists()


# --- Audit -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_audit_filtres_et_export(client_authentifie: Any) -> None:
    """Filtres acteur/entité/action et export CSV réservés super-admin."""
    sadm, sadm_obj = client_authentifie(role="super_admin")
    sadm.post(
        f"{API}/admin/parametres/{CLE}", {"valeur": 650, "motif": "Pour audit"}, format="json"
    )

    par_action = sadm.get(f"{API}/admin/audit?action=modifier_parametre").json()
    assert par_action["elements"], "l'action doit apparaître filtrée par action"
    ligne = par_action["elements"][0]
    assert ligne["entite_type"] == "parametre_plateforme"
    assert ligne["ancienne_valeur"]["valeur"] == 500
    assert ligne["nouvelle_valeur"]["valeur"] == 650

    par_acteur = sadm.get(f"{API}/admin/audit?acteur={sadm_obj.id}").json()
    assert par_acteur["elements"]

    vide = sadm.get(f"{API}/admin/audit?action=inexistante").json()
    assert vide["elements"] == []

    export = sadm.get(f"{API}/admin/audit/export")
    assert export.status_code == 200
    assert "text/csv" in export["Content-Type"]
    assert "modifier_parametre" in export.content.decode("utf-8")

    adm, _ = client_authentifie(role="admin")
    assert adm.get(f"{API}/admin/audit").status_code == 403
    assert adm.get(f"{API}/admin/audit/export").status_code == 403


# --- Référentiels --------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiels_gestion_complete(client_authentifie: Any) -> None:
    """Ajout via API, renommage, désactivation ; la lecture publique exclut l'inactif."""
    sadm, _ = client_authentifie(role="super_admin")
    cree = sadm.post(
        f"{API}/admin/referentiels/culture",
        {"type": "culture", "code": "cafe-f10", "libelle": "Café test", "ordre": 9},
        format="json",
    )
    assert cree.status_code == 201, cree.data
    entree_id = cree.json()["id"]

    conflit = sadm.post(
        f"{API}/admin/referentiels/culture",
        {"type": "culture", "code": "cafe-f10", "libelle": "Doublon"},
        format="json",
    )
    assert conflit.status_code == 409

    renom = sadm.put(
        f"{API}/admin/referentiels/entrees/{entree_id}",
        {"libelle": "Café renommé"},
        format="json",
    )
    assert renom.status_code == 200, renom.data
    assert renom.json()["libelle"] == "Café renommé"

    publics = sadm.get(f"{API}/referentiels/culture").json()
    assert any(e["code"] == "cafe-f10" for e in publics)

    desactive = sadm.post(f"{API}/admin/referentiels/entrees/{entree_id}/desactiver")
    assert desactive.status_code == 200, desactive.data

    publics_apres = sadm.get(f"{API}/referentiels/culture").json()
    assert all(e["code"] != "cafe-f10" for e in publics_apres)
    # Toujours présente en base (jamais supprimée).
    gestion = sadm.get(f"{API}/admin/referentiels/culture").json()
    inactive = next(e for e in gestion if e["code"] == "cafe-f10")
    assert inactive["actif"] is False

    adm, _ = client_authentifie(role="admin")
    assert adm.get(f"{API}/admin/referentiels/culture").status_code == 403


# --- Soldes et reversements via l'API -----------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_soldes_moi_et_admin_et_reversement(
    client_authentifie: Any, commande_livree_paul: tuple[Any, Any]
) -> None:
    """Paul voit son solde ; l'admin enregistre un reversement ; le solde baisse."""
    paul, paul_obj = commande_livree_paul
    mes_gains = paul.get(f"{API}/soldes/moi").json()
    assert mes_gains["gains_acquis"] == 12_500 - 625
    assert mes_gains["solde"] == 11_875

    adm, _ = client_authentifie(role="admin")
    file = adm.get(f"{API}/admin/soldes").json()
    ligne = next(s for s in file if s["beneficiaire_id"] == paul_obj.id)
    assert ligne["solde"] == 11_875

    refuse = adm.post(
        f"{API}/admin/reversements",
        {"beneficiaire_id": paul_obj.id, "montant_fcfa": 99_999,
         "operateur": "mtn_momo", "reference_operateur": "REF-X"},
        format="json",
    )
    assert refuse.status_code == 422
    assert refuse.json()["erreur"]["code"] == "montant_superieur_solde"

    ok = adm.post(
        f"{API}/admin/reversements",
        {"beneficiaire_id": paul_obj.id, "montant_fcfa": 5_000,
         "operateur": "orange_money", "reference_operateur": "REF-OM-1",
         "note": "Remise en main propre"},
        format="json",
    )
    assert ok.status_code == 201, ok.data

    apres = paul.get(f"{API}/soldes/moi").json()
    assert apres["solde"] == 11_875 - 5_000
    assert apres["deja_reverse"] == 5_000

    historique = adm.get(f"{API}/admin/reversements").json()
    assert historique["elements"][0]["montant_fcfa"] == 5_000
    assert historique["elements"][0]["reference_operateur"] == "REF-OM-1"


@pytest.mark.integration
@pytest.mark.django_db
def test_acheteur_soldes_refuse(client_authentifie: Any) -> None:
    """Un acheteur n'a pas de solde bénéficiaire."""
    amina, _ = client_authentifie(role="acheteur")
    assert amina.get(f"{API}/soldes/moi").status_code == 403

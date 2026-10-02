"""Tests d'intégration API F-08 : livraisons, code, incidents, reprogrammation."""

import json
import threading
from typing import Any

import pytest
from apps.commandes.models import Commande, StatutCommande
from apps.livraisons.models import Livraison, StatutLivraison
from apps.livraisons.services import dechiffrer_code
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"
POINT_YAOUNDE = {"type": "Point", "coordinates": [11.5137, 3.848]}


@pytest.fixture
def commande_payee(client_authentifie: Any) -> dict[str, Any]:
    """Commande payée via le vrai chemin webhook (factice signé)."""
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = paul.post(
        "/api/v1/exploitations",
        {
            "nom": "Champ livraison",
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    ).json()
    produit = paul.post(
        "/api/v1/produits",
        {
            "nom": "Tomates livraison",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 500,
            "quantite_stock": "100",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    ).json()
    amina, _ = client_authentifie(role="acheteur")
    adresse = amina.post(
        "/api/v1/utilisateurs/moi/adresses",
        {
            "libelle": "Domicile",
            "description": "Repères",
            "position": POINT_YAOUNDE,
            "ville": "Yaoundé",
            "region": "centre",
        },
        format="json",
    ).json()
    commande = amina.post(
        f"{API}/commandes",
        {
            "lignes": [{"produit_id": produit["id"], "quantite": "10"}],
            "adresse_id": adresse["id"],
        },
        format="json",
    ).json()
    paul.post(f"{API}/commandes/{commande['id']}/accepter")
    init = amina.post(
        f"{API}/commandes/{commande['id']}/paiements",
        {"operateur": "mtn_momo"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"cle-{commande['id']}",
    )
    assert init.status_code == 201, init.data
    from apps.paiements.passerelle import signer_webhook

    corps = json.dumps(
        {
            "id": f"evt-{init.json()['reference_interne']}",
            "type": "payment.complete",
            "data": {
                "reference": init.json()["reference_notchpay"],
                "merchant_reference": init.json()["reference_interne"],
                "amount": init.json()["montant_fcfa"],
            },
        }
    ).encode()
    from apps.paiements.passerelle import EN_TETE_SIGNATURE

    webhook = amina.post(
        "/api/v1/webhooks/notchpay",
        data=corps,
        content_type="application/json",
        **{f"HTTP_{EN_TETE_SIGNATURE.upper().replace('-', '_')}": signer_webhook(corps)},
    )
    assert webhook.status_code == 200, webhook.data
    jean, _ = client_authentifie(role="livreur")
    return {"paul": paul, "amina": amina, "jean": jean, "commande": commande, "produit": produit}


def _livraison(commande_id: str) -> Livraison:
    return Livraison.objects.get(commande_id=commande_id)


def _rendre_disponible(jean: APIClient) -> None:
    reponse = jean.put(
        "/api/v1/livreurs/moi/disponibilite",
        {"disponible": True, "position": POINT_YAOUNDE},
        format="json",
    )
    assert reponse.status_code == 200, reponse.data


@pytest.mark.integration
@pytest.mark.django_db
def test_livraison_creee_apres_paiement_avec_code_et_itineraire(
    client_authentifie, commande_payee
) -> None:
    livraison = _livraison(commande_payee["commande"]["id"])
    assert livraison.statut == StatutLivraison.PROPOSEE
    assert livraison.numero_tentative == 1
    assert livraison.itineraire.source == "ligne_droite"
    assert livraison.itineraire.trajet is None
    assert float(livraison.itineraire.distance_km) > 0
    assert (livraison.itineraire.duree_estimee_min or 0) > 0
    assert len(dechiffrer_code(livraison.code_remise_chiffre)) == 6


@pytest.mark.integration
@pytest.mark.django_db
def test_disponibles_sans_adresse_ni_telephone(client_authentifie, commande_payee) -> None:
    jean = commande_payee["jean"]
    # Indisponible : liste vide, pas d'erreur.
    assert jean.get(f"{API}/livraisons/disponibles").json() == []
    _rendre_disponible(jean)
    liste = jean.get(f"{API}/livraisons/disponibles").json()
    assert len(liste) == 1
    import json as _json

    brut = _json.dumps(liste)
    assert "+237" not in brut and "adresse" not in brut.lower()


@pytest.mark.integration
@pytest.mark.django_db
def test_course_complete_accepter_demarrer_code(client_authentifie, commande_payee) -> None:
    paul, amina, jean = commande_payee["paul"], commande_payee["amina"], commande_payee["jean"]
    commande_id = commande_payee["commande"]["id"]
    _rendre_disponible(jean)
    livraison = _livraison(commande_id)

    acceptee = jean.post(f"{API}/livraisons/{livraison.id}/accepter")
    assert acceptee.status_code == 200

    # Démarrage exige la préparation : 409 avant, puis OK.
    assert jean.post(f"{API}/livraisons/{livraison.id}/demarrer").status_code == 409
    assert paul.post(f"{API}/commandes/{commande_id}/preparer").status_code == 200
    demarree = jean.post(f"{API}/livraisons/{livraison.id}/demarrer")
    assert demarree.status_code == 200
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.REMISE_AU_LIVREUR
    from apps.produits.models import Produit

    produit = Produit.objects.get(id=commande_payee["produit"]["id"])
    assert str(produit.quantite_stock) == "90.000"
    assert str(produit.quantite_reservee) == "0.000"

    # Mauvais code refusé, bon code livré (lu via la route acheteur).
    assert (
        jean.post(
            f"{API}/livraisons/{livraison.id}/confirmer-remise", {"code": "000000"}, format="json"
        ).status_code
        == 422
    )
    code = amina.get(f"{API}/commandes/{commande_id}/code-remise").json()["code"]
    livree = jean.post(
        f"{API}/livraisons/{livraison.id}/confirmer-remise", {"code": code}, format="json"
    )
    assert livree.status_code == 200
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.LIVREE


@pytest.mark.integration
@pytest.mark.django_db
def test_code_reserve_acheteur_et_jamais_livreur(client_authentifie, commande_payee) -> None:
    amina = commande_payee["amina"]
    jean = commande_payee["jean"]
    commande_id = commande_payee["commande"]["id"]
    code = amina.get(f"{API}/commandes/{commande_id}/code-remise")
    assert code.status_code == 200
    assert len(code.json()["code"]) == 6
    assert jean.get(f"{API}/commandes/{commande_id}/code-remise").status_code in (403, 404)
    # Le détail livreur ne contient jamais le code.
    _rendre_disponible(jean)
    livraison = _livraison(commande_id)
    jean.post(f"{API}/livraisons/{livraison.id}/accepter")
    import json as _json

    assert "code_remise" not in _json.dumps(jean.get(f"{API}/livraisons/{livraison.id}").json())


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_double_acceptation_un_seul_gagnant(client_authentifie) -> None:
    """Deux livreurs acceptent en même temps : 200 + 409 (verrou)."""
    paul, _ = client_authentifie(role="agriculteur")
    amina, _ = client_authentifie(role="acheteur")
    jean, _ = client_authentifie(role="livreur")
    alain, _ = client_authentifie(role="livreur")
    livraison_id = _construire_proposee(paul, amina)
    _rendre_disponible(jean)
    _rendre_disponible(alain)
    statuts: list[int] = []
    barriere = threading.Barrier(2)

    def accepter(client: APIClient) -> None:
        barriere.wait()
        statuts.append(client.post(f"{API}/livraisons/{livraison_id}/accepter").status_code)

    fils = [threading.Thread(target=accepter, args=(c,)) for c in (jean, alain)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert sorted(statuts) == [200, 409]


def _construire_proposee(paul: APIClient, amina: APIClient) -> str:
    exploitation = paul.post(
        "/api/v1/exploitations",
        {
            "nom": "Champ course",
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    ).json()
    produit = paul.post(
        "/api/v1/produits",
        {
            "nom": "Tomates course",
            "categorie": "legumes",
            "unite": "kg",
            "prix_unitaire_fcfa": 500,
            "quantite_stock": "100",
            "exploitation_id": exploitation["id"],
        },
        format="json",
    ).json()
    adresse = amina.post(
        "/api/v1/utilisateurs/moi/adresses",
        {
            "libelle": "Domicile",
            "description": "Repères",
            "position": POINT_YAOUNDE,
            "ville": "Yaoundé",
            "region": "centre",
        },
        format="json",
    ).json()
    commande = amina.post(
        f"{API}/commandes",
        {
            "lignes": [{"produit_id": produit["id"], "quantite": "10"}],
            "adresse_id": adresse["id"],
        },
        format="json",
    ).json()
    paul.post(f"{API}/commandes/{commande['id']}/accepter")
    init = amina.post(
        f"{API}/commandes/{commande['id']}/paiements",
        {"operateur": "mtn_momo"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"cle-{commande['id']}",
    )
    assert init.status_code == 201, init.data
    import json as _json

    from apps.paiements.passerelle import EN_TETE_SIGNATURE as _SIG
    from apps.paiements.passerelle import signer_webhook as _signer

    corps = _json.dumps(
        {
            "id": f"evt-{init.json()['reference_interne']}",
            "type": "payment.complete",
            "data": {
                "reference": init.json()["reference_notchpay"],
                "merchant_reference": init.json()["reference_interne"],
                "amount": init.json()["montant_fcfa"],
            },
        }
    ).encode()
    webhook = amina.post(
        "/api/v1/webhooks/notchpay",
        data=corps,
        content_type="application/json",
        **{f"HTTP_{_SIG.upper().replace('-', '_')}": _signer(corps)},  # type: ignore[arg-type]
    )
    assert webhook.status_code == 200, webhook.data
    return str(Livraison.objects.get(commande_id=commande["id"]).id)


@pytest.mark.integration
@pytest.mark.django_db
def test_incident_client_absent_puis_reprogrammation_payante(
    client_authentifie, commande_payee
) -> None:
    paul, amina, jean = commande_payee["paul"], commande_payee["amina"], commande_payee["jean"]
    commande_id = commande_payee["commande"]["id"]
    _rendre_disponible(jean)
    livraison = _livraison(commande_id)
    jean.post(f"{API}/livraisons/{livraison.id}/accepter")
    paul.post(f"{API}/commandes/{commande_id}/preparer")
    jean.post(f"{API}/livraisons/{livraison.id}/demarrer")

    incident = jean.post(
        f"{API}/livraisons/{livraison.id}/incidents",
        {"type": "client_absent", "description": "Personne au portail."},
        format="json",
    )
    assert incident.status_code == 201
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.CLIENT_ABSENT

    # Reprogrammation : nouveaux frais à payer d'abord.
    reprog = amina.post(
        f"{API}/livraisons/{livraison.id}/reprogrammer",
        {"operateur": "mtn_momo"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="cle-reprog-1",
    )
    assert reprog.status_code == 200, reprog.data
    assert reprog.json()["sans_frais"] is False
    assert reprog.json()["url_paiement"]

    # Webhook des nouveaux frais → de nouveau proposée, tentative 2.
    paiement_id = reprog.json()["paiement_id"]
    from apps.paiements.models import Paiement as _Paiement

    frais = _Paiement.objects.get(id=paiement_id)
    import json as _json

    from apps.paiements.passerelle import EN_TETE_SIGNATURE as _SIG
    from apps.paiements.passerelle import signer_webhook as _signer

    corps = _json.dumps(
        {
            "id": f"evt-{frais.reference_interne}",
            "type": "payment.complete",
            "data": {
                "reference": frais.reference_notchpay,
                "merchant_reference": frais.reference_interne,
                "amount": frais.montant_fcfa,
            },
        }
    ).encode()
    webhook = amina.post(
        "/api/v1/webhooks/notchpay",
        data=corps,
        content_type="application/json",
        **{f"HTTP_{_SIG.upper().replace('-', '_')}": _signer(corps)},
    )
    assert webhook.status_code == 200
    livree = Livraison.objects.get(id=livraison.id)
    assert livree.statut == StatutLivraison.PROPOSEE
    assert livree.numero_tentative == 2
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.REMISE_AU_LIVREUR


@pytest.mark.integration
@pytest.mark.django_db
def test_position_livreur_pendant_course_visible_acheteur(
    client_authentifie, commande_payee
) -> None:
    paul, amina, jean = commande_payee["paul"], commande_payee["amina"], commande_payee["jean"]
    commande_id = commande_payee["commande"]["id"]
    livraison = _livraison(commande_id)

    # Acceptée mais pas encore démarrée : position interdue (409).
    _rendre_disponible(jean)
    assert jean.post(f"{API}/livraisons/{livraison.id}/accepter").status_code == 200
    refuse = jean.post(
        f"{API}/livraisons/{livraison.id}/position",
        {"position": POINT_YAOUNDE},
        format="json",
    )
    assert refuse.status_code == 409

    paul.post(f"{API}/commandes/{commande_id}/preparer")
    assert jean.post(f"{API}/livraisons/{livraison.id}/demarrer").status_code == 200

    # Position hors Cameroun refusée (422).
    hors = jean.post(
        f"{API}/livraisons/{livraison.id}/position",
        {"position": {"type": "Point", "coordinates": [0.0, 0.0]}},
        format="json",
    )
    assert hors.status_code == 422

    deplace = {"type": "Point", "coordinates": [11.52, 3.87]}
    publiee = jean.post(
        f"{API}/livraisons/{livraison.id}/position", {"position": deplace}, format="json"
    )
    assert publiee.status_code == 200, publiee.data

    # L'acheteur voit la dernière position et le téléphone pendant en_cours.
    suivi = amina.get(f"{API}/livraisons/{livraison.id}").json()
    assert suivi["position_livreur"]["coordinates"] == deplace["coordinates"]
    assert suivi["telephone_livreur"]


@pytest.mark.integration
@pytest.mark.django_db
def test_code_bloque_apres_cinq_echecs(client_authentifie, commande_payee) -> None:
    paul, _, jean = commande_payee["paul"], commande_payee["amina"], commande_payee["jean"]
    commande_id = commande_payee["commande"]["id"]
    _rendre_disponible(jean)
    livraison = _livraison(commande_id)
    jean.post(f"{API}/livraisons/{livraison.id}/accepter")
    paul.post(f"{API}/commandes/{commande_id}/preparer")
    jean.post(f"{API}/livraisons/{livraison.id}/demarrer")
    for _ in range(5):
        assert (
            jean.post(
                f"{API}/livraisons/{livraison.id}/confirmer-remise",
                {"code": "000000"},
                format="json",
            ).status_code
            == 422
        )
    bloque = jean.post(
        f"{API}/livraisons/{livraison.id}/confirmer-remise",
        {"code": dechiffrer_code(Livraison.objects.get(id=livraison.id).code_remise_chiffre)},
        format="json",
    )
    assert bloque.status_code == 422
    assert bloque.json()["erreur"]["code"] == "code_bloque"

"""Tests d'intégration API F-07 : paiements, webhooks, idempotence (SPEC-05)."""

import json
import threading
from datetime import timedelta
from typing import Any

import pytest
from apps.commandes.models import Commande, StatutCommande
from apps.notifications.models import Notification
from apps.paiements.evenements import PaiementReussi
from apps.paiements.models import EvenementWebhook, Paiement, StatutPaiement
from apps.paiements.passerelle import EN_TETE_SIGNATURE, PasserelleFactice, signer_webhook
from apps.paiements.taches import verifier_paiements
from commun.evenements import abonner, desabonner, vider
from commun.horloge import maintenant
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"
ENTETE_HTTP_SIGNATURE = f"HTTP_{EN_TETE_SIGNATURE.upper().replace('-', '_')}"


@pytest.fixture
def commande_acceptee(client_authentifie: Any) -> dict[str, Any]:
    """Commande acceptée de 10 kg de tomates (factice, sans réseau)."""
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = paul.post(
        "/api/v1/exploitations",
        {
            "nom": "Champ paiement",
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    ).json()
    produit = paul.post(
        "/api/v1/produits",
        {
            "nom": "Tomates paiement",
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
            "position": {"type": "Point", "coordinates": [11.5137, 3.848]},
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
    acceptee = paul.post(f"{API}/commandes/{commande['id']}/accepter")
    assert acceptee.status_code == 200, acceptee.data
    return {"paul": paul, "amina": amina, "commande": commande, "produit": produit}


def _corps_webhook(reference_interne: str, type_evt: str, montant: int | None) -> bytes:
    return json.dumps(
        {
            "id": f"evt-{reference_interne}-{type_evt}",
            "type": type_evt,
            "data": {
                "reference": f"FCT-{reference_interne}",
                "merchant_reference": reference_interne,
                **({"amount": montant} if montant is not None else {}),
                "currency": "XAF",
            },
        }
    ).encode("utf-8")


def _poster_webhook(client: APIClient, corps: bytes, signature: str | None = None) -> Any:
    if signature is None:
        signature = signer_webhook(corps)
    extra = {ENTETE_HTTP_SIGNATURE: signature}
    return client.post(
        "/api/v1/webhooks/notchpay",
        data=corps,
        content_type="application/json",
        **extra,  # type: ignore[arg-type]
    )


def _initier(client: APIClient, commande_id: str, cle: str, operateur: str = "mtn_momo") -> Any:
    return client.post(
        f"{API}/commandes/{commande_id}/paiements",
        {"operateur": operateur},
        format="json",
        HTTP_IDEMPOTENCY_KEY=cle,
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_initier_paiement_idempotent_et_url(client_authentifie, commande_acceptee) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-achat-1")
    assert init.status_code == 201, init.data
    assert init.json()["url_paiement"]
    assert init.json()["statut"] == "en_attente"
    assert init.json()["rejoue"] is False
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.PAIEMENT_EN_ATTENTE

    # Même clé → même réponse (200), sans nouveau paiement.
    rejoue = _initier(amina, commande["id"], "cle-achat-1")
    assert rejoue.status_code == 200
    assert rejoue.json()["id"] == init.json()["id"]
    assert rejoue.json()["url_paiement"] == init.json()["url_paiement"]
    assert Paiement.objects.filter(commande_id=commande["id"]).count() == 1

    # Même clé pour une autre commande → 409.
    amina2, _ = client_authentifie(role="acheteur")
    adresse2 = amina2.post(
        "/api/v1/utilisateurs/moi/adresses",
        {
            "libelle": "Domicile",
            "description": "Repères",
            "position": {"type": "Point", "coordinates": [11.5137, 3.848]},
            "ville": "Yaoundé",
            "region": "centre",
        },
        format="json",
    ).json()
    paul = commande_acceptee["paul"]
    produit_id = commande_acceptee["produit"]["id"]
    commande2 = amina2.post(
        f"{API}/commandes",
        {"lignes": [{"produit_id": produit_id, "quantite": "2"}], "adresse_id": adresse2["id"]},
        format="json",
    ).json()
    paul.post(f"{API}/commandes/{commande2['id']}/accepter")
    conflit = amina2.post(
        f"{API}/commandes/{commande2['id']}/paiements",
        {"operateur": "orange_money"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="cle-achat-1",
    )
    assert conflit.status_code == 409
    assert conflit.json()["erreur"]["code"] == "cle_idempotence_differente"


@pytest.mark.integration
@pytest.mark.django_db
def test_webhook_reussi_paie_fige_commission_notifie_publie(
    client_authentifie, commande_acceptee
) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-ok-1")
    reference = init.json()["reference_interne"]
    montant = init.json()["montant_fcfa"]

    recus: list = []
    abonner(PaiementReussi, recus.append)
    try:
        corps = _corps_webhook(reference, "payment.complete", montant)
        reponse = _poster_webhook(amina, corps)
        assert reponse.status_code == 200, reponse.data
    finally:
        desabonner(PaiementReussi, recus.append)
        vider()

    paiement = Paiement.objects.get(reference_interne=reference)
    assert paiement.statut == StatutPaiement.REUSSI
    assert paiement.confirme_le is not None
    terminee = Commande.objects.get(id=commande["id"])
    assert terminee.statut == StatutCommande.PAYEE
    assert terminee.commission_taux_pb == 500
    assert terminee.commission_fcfa == 250  # 5 % de 5 000, hors livraison.
    assert terminee.expire_le is None
    assert len(recus) == 1 and recus[0].commande_id == commande["id"]
    assert Notification.objects.filter(destinataire_id=terminee.agriculteur_id).exists()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_webhook_avant_retour_navigateur_et_double_envoi(
    client_authentifie, commande_acceptee
) -> None:
    """Le webhook seul fait foi ; renvoyé (même en parallèle), un seul effet.

    `transaction=True` : les lignes créées en préparation doivent être
    commitées pour être visibles (et verrouillables) depuis les threads ;
    la fixture `_donnees_migration_garanties` réinsère les données de
    migration après le flush de teardown.
    """
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-double-1")
    reference = init.json()["reference_interne"]
    montant = init.json()["montant_fcfa"]

    statuts: list[int] = []

    def envoyer() -> None:
        statuts.append(
            _poster_webhook(
                APIClient(), _corps_webhook(reference, "payment.complete", montant)
            ).status_code
        )

    fils = [threading.Thread(target=envoyer) for _ in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert sorted(statuts) == [200, 200]
    assert Paiement.objects.get(reference_interne=reference).statut == StatutPaiement.REUSSI
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.PAYEE
    assert (
        EvenementWebhook.objects.filter(
            identifiant_externe=f"evt-{reference}-payment.complete"
        ).count()
        == 1
    )
    terminee = Commande.objects.get(id=commande["id"])
    # Notifications : création (F-06) + paiement, sans doublon.
    assert Notification.objects.filter(destinataire_id=terminee.agriculteur_id).count() == 2


@pytest.mark.integration
@pytest.mark.django_db
def test_webhook_signature_invalide_montant_incoherent_et_incomplet(
    client_authentifie, commande_acceptee
) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-ko-1")
    reference = init.json()["reference_interne"]
    montant = init.json()["montant_fcfa"]

    corps = _corps_webhook(reference, "payment.complete", montant)
    refuse = _poster_webhook(amina, corps, signature="signature-fausse")
    assert refuse.status_code == 401
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.PAIEMENT_EN_ATTENTE

    corps = _corps_webhook("PAY-INCONNUE-000001", "payment.complete", montant)
    inconnu = _poster_webhook(amina, corps)
    assert inconnu.status_code == 200
    assert EvenementWebhook.objects.get(
        identifiant_externe="evt-PAY-INCONNUE-000001-payment.complete"
    ).erreur

    corps = json.dumps(
        {
            "id": f"evt-{reference}-montant",
            "type": "payment.complete",
            "data": {"merchant_reference": reference, "amount": 1},
        }
    ).encode()
    incoherent = _poster_webhook(amina, corps)
    assert incoherent.status_code == 200
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.PAIEMENT_EN_ATTENTE

    corps = json.dumps({"type": "payment.complete"}).encode()
    incomplet = _poster_webhook(amina, corps)
    assert incomplet.status_code == 400


@pytest.mark.integration
@pytest.mark.django_db
def test_webhook_echoue_revient_acceptee_et_reessai(client_authentifie, commande_acceptee) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-fail-1")
    reference = init.json()["reference_interne"]
    montant = init.json()["montant_fcfa"]

    echec = _poster_webhook(amina, _corps_webhook(reference, "payment.failed", montant))
    assert echec.status_code == 200
    assert Paiement.objects.get(reference_interne=reference).statut == StatutPaiement.ECHOUE
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.ACCEPTEE

    # Nouvel essai (nouvelle clé) puis succès.
    init2 = _initier(amina, commande["id"], "cle-fail-2")
    assert init2.status_code == 201
    ok = _poster_webhook(
        amina,
        _corps_webhook(
            init2.json()["reference_interne"], "payment.complete", init2.json()["montant_fcfa"]
        ),
    )
    assert ok.status_code == 200
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.PAYEE


@pytest.mark.integration
@pytest.mark.django_db
def test_initier_sur_commande_non_acceptee_409(client_authentifie, commande_acceptee) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-etat-1")
    assert init.status_code == 201
    # Commande déjà en paiement : second paiement (autre clé) → 409.
    bis = _initier(amina, commande["id"], "cle-etat-2")
    assert bis.status_code == 409


@pytest.mark.integration
@pytest.mark.django_db
def test_get_paiement_payeur_admin_et_404(client_authentifie, commande_acceptee) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-get-1")
    paiement_id = init.json()["id"]
    assert amina.get(f"{API}/paiements/{paiement_id}").status_code == 200
    admin, _ = client_authentifie(role="admin")
    assert admin.get(f"{API}/paiements/{paiement_id}").status_code == 200
    autre, _ = client_authentifie(role="acheteur")
    assert autre.get(f"{API}/paiements/{paiement_id}").status_code == 404


@pytest.mark.integration
@pytest.mark.django_db
def test_tache_verification_secours_webhook_perdu(client_authentifie, commande_acceptee) -> None:
    amina = commande_acceptee["amina"]
    commande = commande_acceptee["commande"]
    init = _initier(amina, commande["id"], "cle-task-1")
    reference_externe = init.json()["reference_notchpay"]
    assert reference_externe
    # Le fournisseur confirme entre-temps : la tâche régularise.
    PasserelleFactice.resultats[reference_externe] = "reussi"
    try:
        Paiement.objects.filter(id=init.json()["id"]).update(
            maj_le=maintenant() - timedelta(minutes=20)
        )
        assert verifier_paiements() == 1
    finally:
        PasserelleFactice.resultats.pop(reference_externe, None)
    assert Commande.objects.get(id=commande["id"]).statut == StatutCommande.PAYEE

"""Tests d'intégration API F-09 : remboursements, signalements, évaluations.

Parcours HTTP complets (prompt F-09 backend point 6) :
- délai de demande dépassé, montant > paiement, double demande, justificatif
  obligatoire, exécution manuelle Q-26, annulation après paiement ;
- permissions par rôle (ressource d'autrui → 404) ;
- signalements + traitement admin + audit ; évaluation seulement si livrée.
"""

import io
import json
from datetime import timedelta
from typing import Any

import pytest
from apps.administration.models import JournalAudit
from apps.commandes.models import Commande, StatutCommande
from apps.litiges.models import Remboursement, Signalement, StatutRemboursement
from apps.livraisons.models import Livraison
from apps.notifications.models import Notification
from apps.paiements.models import Paiement, StatutPaiement
from apps.produits.models import Produit
from commun.horloge import decaler, reinitialiser
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"
POINT_YAOUNDE = {"type": "Point", "coordinates": [11.5137, 3.848]}


def _jpeg_valide() -> bytes:
    tampon = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(tampon, format="JPEG")
    return tampon.getvalue()


@pytest.fixture
def service_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Stockage temporaire pour les uploads (pas de pollution du disque projet)."""
    from apps.fichiers.services import ServiceFichiers
    from apps.fichiers.stockage import StockageDisque

    service = ServiceFichiers(stockage=StockageDisque(tmp_path))
    monkeypatch.setattr("apps.fichiers.views.service_fichiers", service)


def _upload_photo(client: APIClient) -> dict[str, Any]:
    reponse = client.post(
        f"{API}/fichiers",
        {
            "fichier": SimpleUploadedFile("photo.jpg", _jpeg_valide(), content_type="image/jpeg"),
            "contexte": "justificatif",
        },
        format="multipart",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()


def _webhook_succes(amina: APIClient, init: dict[str, Any]) -> None:
    from apps.paiements.passerelle import EN_TETE_SIGNATURE, signer_webhook

    corps = json.dumps(
        {
            "id": f"evt-{init['reference_interne']}",
            "type": "payment.complete",
            "data": {
                "reference": init["reference_notchpay"],
                "merchant_reference": init["reference_interne"],
                "amount": init["montant_fcfa"],
            },
        }
    ).encode()
    en_tete: dict[str, str] = {
        f"HTTP_{EN_TETE_SIGNATURE.upper().replace('-', '_')}": signer_webhook(corps)
    }
    webhook = amina.post(
        "/api/v1/webhooks/notchpay",
        data=corps,
        content_type="application/json",
        **en_tete,  # type: ignore[arg-type]
    )
    assert webhook.status_code == 200, webhook.data


def _commande_payee(client_authentifie: Any) -> dict[str, Any]:
    """Commande payée par le vrai chemin webhook (comme en F-07/F-08)."""
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = paul.post(
        f"{API}/exploitations",
        {
            "nom": "Champ litige",
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    ).json()
    produit = paul.post(
        f"{API}/produits",
        {
            "nom": "Tomates litige",
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
        f"{API}/utilisateurs/moi/adresses",
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
        {"lignes": [{"produit_id": produit["id"], "quantite": "10"}], "adresse_id": adresse["id"]},
        format="json",
    ).json()
    assert paul.post(f"{API}/commandes/{commande['id']}/accepter").status_code == 200
    init = amina.post(
        f"{API}/commandes/{commande['id']}/paiements",
        {"operateur": "mtn_momo"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"cle-{commande['id']}",
    )
    assert init.status_code == 201, init.data
    _webhook_succes(amina, init.json())
    jean, _ = client_authentifie(role="livreur")
    mireille, _ = client_authentifie(role="admin")
    paiement = Paiement.objects.get(commande_id=commande["id"], statut=StatutPaiement.REUSSI)
    return {
        "paul": paul,
        "amina": amina,
        "jean": jean,
        "mireille": mireille,
        "commande": commande,
        "produit": produit,
        "montant_paye": paiement.montant_fcfa,
    }


def _livrer(parte: dict[str, Any]) -> None:
    """Course complète jusqu'à `livree` (F-08)."""
    paul, amina, jean = parte["paul"], parte["amina"], parte["jean"]
    commande_id = parte["commande"]["id"]
    jean.put(
        f"{API}/livreurs/moi/disponibilite",
        {"disponible": True, "position": POINT_YAOUNDE},
        format="json",
    )
    livraison = Livraison.objects.get(commande_id=commande_id)
    assert jean.post(f"{API}/livraisons/{livraison.id}/accepter").status_code == 200
    assert paul.post(f"{API}/commandes/{commande_id}/preparer").status_code == 200
    assert jean.post(f"{API}/livraisons/{livraison.id}/demarrer").status_code == 200
    code = amina.get(f"{API}/commandes/{commande_id}/code-remise").json()["code"]
    reponse = jean.post(
        f"{API}/livraisons/{livraison.id}/confirmer-remise", {"code": code}, format="json"
    )
    assert reponse.status_code == 200, reponse.data
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.LIVREE


@pytest.fixture
def commande_livree(client_authentifie: Any) -> dict[str, Any]:
    parte = _commande_payee(client_authentifie)
    _livrer(parte)
    return parte


# --- Remboursements -----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_remboursement_partiel_cycle_complet(
    commande_livree: dict[str, Any], service_tmp: None
) -> None:
    """E2E-10 côté API : demande → examen → approbation partielle → exécution."""
    amina, mireille = commande_livree["amina"], commande_livree["mireille"]
    commande_id = commande_livree["commande"]["id"]
    photo = _upload_photo(amina)
    demande = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_id,
            "motif": "produit_endommage",
            "montant_demande_fcfa": 2000,
            "justificatifs": [photo["id"]],
        },
        format="json",
    )
    assert demande.status_code == 201, demande.data
    remboursement_id = demande.json()["id"]
    assert demande.json()["statut"] == "demande"
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.REMBOURSEMENT_EN_ATTENTE

    file = mireille.get(f"{API}/admin/remboursements?statut=demande")
    assert file.status_code == 200
    assert [r["id"] for r in file.json()] == [remboursement_id]

    assert (
        mireille.post(f"{API}/admin/remboursements/{remboursement_id}/examiner").status_code == 200
    )
    approuve = mireille.post(
        f"{API}/admin/remboursements/{remboursement_id}/approuver",
        {"montant_accorde_fcfa": 2000},
        format="json",
    )
    assert approuve.status_code == 200, approuve.data
    assert approuve.json()["statut"] == "approuve"

    execute = mireille.post(
        f"{API}/admin/remboursements/{remboursement_id}/executer",
        {"reference_operateur": "MTN-9987"},
        format="json",
    )
    assert execute.status_code == 200, execute.data
    assert execute.json()["statut"] == "effectue"
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.REMBOURSEE
    suivi = amina.get(f"{API}/remboursements/mes-demandes").json()
    assert suivi[0]["reference_operateur"] == "MTN-9987"
    assert Notification.objects.filter(
        destinataire__role="acheteur", titre__contains="remboursée"
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_delai_de_demande_depasse_refuse(commande_livree: dict[str, Any]) -> None:
    amina = commande_livree["amina"]
    try:
        decaler(timedelta(hours=49))
        refus = amina.post(
            f"{API}/remboursements",
            {
                "commande_id": commande_livree["commande"]["id"],
                "motif": "autre",
                "montant_demande_fcfa": 500,
            },
            format="json",
        )
        assert refus.status_code == 422
        assert refus.json()["erreur"]["code"] == "delai_depasse"
        assert refus.json()["erreur"]["details"]["delai_h"] == 48
    finally:
        reinitialiser()


@pytest.mark.integration
@pytest.mark.django_db
def test_montant_demande_superieur_au_paiement_refuse(commande_livree: dict[str, Any]) -> None:
    amina = commande_livree["amina"]
    refus = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": commande_livree["montant_paye"] + 1,
        },
        format="json",
    )
    assert refus.status_code == 422
    assert refus.json()["erreur"]["code"] == "montant_invalide"


@pytest.mark.integration
@pytest.mark.django_db
def test_double_demande_refusee(commande_livree: dict[str, Any]) -> None:
    amina = commande_livree["amina"]
    premier = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 1000,
        },
        format="json",
    )
    assert premier.status_code == 201
    second = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 500,
        },
        format="json",
    )
    assert second.status_code == 409
    assert second.json()["erreur"]["code"] == "remboursement_deja_demande"


@pytest.mark.integration
@pytest.mark.django_db
def test_justificatif_obligatoire_pour_produit_endommage(commande_livree: dict[str, Any]) -> None:
    amina = commande_livree["amina"]
    refus = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "produit_endommage",
            "montant_demande_fcfa": 1000,
        },
        format="json",
    )
    assert refus.status_code == 422
    assert refus.json()["erreur"]["code"] == "justificatif_requis"


@pytest.mark.integration
@pytest.mark.django_db
def test_approbation_montant_superieur_au_paiement_refusee(commande_livree: dict[str, Any]) -> None:
    """Le montant accordé ne peut jamais dépasser le montant réellement payé."""
    amina, mireille = commande_livree["amina"], commande_livree["mireille"]
    demande = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 1000,
        },
        format="json",
    ).json()
    trop = mireille.post(
        f"{API}/admin/remboursements/{demande['id']}/approuver",
        {"montant_accorde_fcfa": commande_livree["montant_paye"] + 100},
        format="json",
    )
    assert trop.status_code == 422
    assert trop.json()["erreur"]["code"] == "montant_accorde_invalide"


@pytest.mark.integration
@pytest.mark.django_db
def test_executer_exige_approbation_et_reference(commande_livree: dict[str, Any]) -> None:
    amina, mireille = commande_livree["amina"], commande_livree["mireille"]
    demande = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 1000,
        },
        format="json",
    ).json()
    trop_tot = mireille.post(
        f"{API}/admin/remboursements/{demande['id']}/executer",
        {"reference_operateur": "REF-1"},
        format="json",
    )
    assert trop_tot.status_code == 409
    mireille.post(
        f"{API}/admin/remboursements/{demande['id']}/approuver",
        {"montant_accorde_fcfa": 1000},
        format="json",
    )
    sans_ref = mireille.post(
        f"{API}/admin/remboursements/{demande['id']}/executer",
        {"reference_operateur": "   "},
        format="json",
    )
    assert sans_ref.status_code == 422
    assert sans_ref.json()["erreur"]["code"] == "reference_obligatoire"


@pytest.mark.integration
@pytest.mark.django_db
def test_refus_avec_motif_remise_en_etat_de_la_commande(commande_livree: dict[str, Any]) -> None:
    """PW-F09-07 : refus motivé, la commande retrouve `livree`, Amina voit le motif."""
    amina, mireille = commande_livree["amina"], commande_livree["mireille"]
    commande_id = commande_livree["commande"]["id"]
    sans_motif = amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_id,
            "motif": "autre",
            "montant_demande_fcfa": 1000,
        },
        format="json",
    )
    assert sans_motif.status_code == 201
    demande_id = sans_motif.json()["id"]
    refuse_vide = mireille.post(
        f"{API}/admin/remboursements/{demande_id}/refuser", {"motif": ""}, format="json"
    )
    assert refuse_vide.status_code == 422
    assert refuse_vide.json()["erreur"]["code"] == "motif_obligatoire"
    refuse = mireille.post(
        f"{API}/admin/remboursements/{demande_id}/refuser",
        {"motif": "Dégâts imputables au transport, plainte écartée."},
        format="json",
    )
    assert refuse.status_code == 200, refuse.data
    assert refuse.json()["statut"] == "refuse"
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.LIVREE
    suivi = amina.get(f"{API}/remboursements/mes-demandes").json()
    assert suivi[0]["motif_decision"] == "Dégâts imputables au transport, plainte écartée."


@pytest.mark.integration
@pytest.mark.django_db
def test_annulation_apres_paiement_cree_remboursement_integral(
    client_authentifie: Any,
) -> None:
    """PW-F09-06 : annulation sur commande payée → remboursement intégral à examiner."""
    parte = _commande_payee(client_authentifie)
    amina, mireille = parte["amina"], parte["mireille"]
    commande_id = parte["commande"]["id"]
    annulee = amina.post(f"{API}/commandes/{commande_id}/annuler")
    assert annulee.status_code == 200, annulee.data
    assert Commande.objects.get(id=commande_id).statut == StatutCommande.REMBOURSEMENT_EN_ATTENTE
    remboursement = Remboursement.objects.get(commande_id=commande_id)
    assert remboursement.motif == "annulation"
    assert remboursement.montant_demande_fcfa == parte["montant_paye"]
    assert remboursement.statut == StatutRemboursement.DEMANDE
    assert remboursement.statut_commande_origine == StatutCommande.PAYEE
    file = mireille.get(f"{API}/admin/remboursements")
    assert file.status_code == 200
    assert [r["id"] for r in file.json()] == [remboursement.id]
    # Hypothèse Q-42 : le stock réservé est libéré à l'annulation.
    produit = Produit.objects.get(id=parte["produit"]["id"])
    assert str(produit.quantite_reservee) == "0.000"


@pytest.mark.integration
@pytest.mark.django_db
def test_remboursement_invisible_aux_tiers(
    commande_livree: dict[str, Any], client_authentifie: Any
) -> None:
    # Un autre acheteur ne peut pas demander le remboursement d'une commande
    # qui n'est pas la sienne : 404 (conventions §5).
    autre_client, _ = client_authentifie(role="acheteur")
    refus = autre_client.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 100,
        },
        format="json",
    )
    assert refus.status_code == 404
    assert autre_client.get(f"{API}/remboursements/mes-demandes").json() == []


@pytest.mark.integration
@pytest.mark.django_db
def test_gardes_roles_remboursements(
    commande_livree: dict[str, Any],
    client_api: APIClient,
    client_authentifie: Any,
    verifier_garde_route: Any,
) -> None:
    assert client_api.post(f"{API}/remboursements", {}, format="json").status_code == 401
    paul, _ = client_authentifie(role="agriculteur")
    refus = paul.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 100,
        },
        format="json",
    )
    assert refus.status_code == 403
    verifier_garde_route(f"{API}/remboursements/mes-demandes", roles_autorises=["acheteur"])
    verifier_garde_route(f"{API}/admin/remboursements", roles_autorises=["admin", "super_admin"])
    verifier_garde_route(f"{API}/admin/signalements", roles_autorises=["admin", "super_admin"])
    assert client_api.get(f"{API}/signalements/mes-signalements").status_code == 401


# --- Évaluations ---------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_evaluation_impossible_avant_livree_puis_une_fois(
    client_authentifie: Any, commande_livree: dict[str, Any]
) -> None:
    """PW-F09-05 : 422 avant livraison, 201 après, 409 à la seconde."""
    parte = _commande_payee(client_authentifie)
    avant = parte["amina"].post(
        f"{API}/commandes/{parte['commande']['id']}/evaluation",
        {"note_agriculteur": 4},
        format="json",
    )
    assert avant.status_code == 422
    assert avant.json()["erreur"]["code"] == "commande_non_livree"

    amina = commande_livree["amina"]
    commande_id = commande_livree["commande"]["id"]
    good = amina.post(
        f"{API}/commandes/{commande_id}/evaluation",
        {"note_agriculteur": 5, "note_livreur": 4, "commentaire": "RAS"},
        format="json",
    )
    assert good.status_code == 201, good.data
    again = amina.post(
        f"{API}/commandes/{commande_id}/evaluation", {"note_agriculteur": 1}, format="json"
    )
    assert again.status_code == 409
    assert again.json()["erreur"]["code"] == "evaluation_deja_faite"
    note_invalide = amina.post(
        f"{API}/commandes/{commande_id}/evaluation", {"note_agriculteur": 6}, format="json"
    )
    assert note_invalide.status_code == 422
    lue = amina.get(f"{API}/commandes/{commande_id}/evaluation")
    assert lue.status_code == 200
    assert lue.json()["note_agriculteur"] == 5
    assert Notification.objects.filter(destinataire__role="agriculteur", type="commande").exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_evaluation_commande_d_autrui_404(
    commande_livree: dict[str, Any], client_authentifie: Any
) -> None:
    inconnu, _ = client_authentifie(role="acheteur")
    refus = inconnu.post(
        f"{API}/commandes/{commande_livree['commande']['id']}/evaluation",
        {"note_agriculteur": 3},
        format="json",
    )
    assert refus.status_code == 404
    assert (
        inconnu.get(f"{API}/commandes/{commande_livree['commande']['id']}/evaluation").status_code
        == 404
    )


# --- Signalements --------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_signalement_parcours_complet_avec_justificatif_et_audit(
    commande_livree: dict[str, Any], service_tmp: None
) -> None:
    amina, mireille = commande_livree["amina"], commande_livree["mireille"]
    photo = _upload_photo(amina)
    cree = amina.post(
        f"{API}/signalements",
        {
            "cible_type": "livraison",
            "cible_id": commande_livree["commande"]["id"],
            "motif": "conduit_tenant",
            "description": "Le livreur a conduit sans prudence.",
            "fichier_ids": [photo["id"]],
        },
        format="json",
    )
    assert cree.status_code == 201, cree.data
    signalement_id = cree.json()["id"]
    assert cree.json()["statut"] == "ouvert"
    assert amina.get(f"{API}/signalements/mes-signalements").json()[0]["id"] == signalement_id
    file = mireille.get(f"{API}/admin/signalements?statut=ouvert")
    assert [s["id"] for s in file.json()] == [signalement_id]

    cloture_vide = mireille.post(
        f"{API}/admin/signalements/{signalement_id}/traiter",
        {"statut": "clos_fonde", "decision": ""},
        format="json",
    )
    assert cloture_vide.status_code == 422
    assert cloture_vide.json()["erreur"]["code"] == "decision_requise"

    traite = mireille.post(
        f"{API}/admin/signalements/{signalement_id}/traiter",
        {"statut": "clos_fonde", "decision": "Livreur averti."},
        format="json",
    )
    assert traite.status_code == 200, traite.data
    assert Signalement.objects.get(id=signalement_id).statut == "clos_fonde"
    assert JournalAudit.objects.filter(
        action="traiter_signalement", entite_id=str(signalement_id)
    ).exists()
    assert Notification.objects.filter(destinataire__role="acheteur", type="signalement").exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_signalement_sans_description_refuse_et_reserve_aux_admins(
    commande_livree: dict[str, Any], client_authentifie: Any
) -> None:
    amina = commande_livree["amina"]
    vide = amina.post(
        f"{API}/signalements",
        {
            "cible_type": "produit",
            "cible_id": commande_livree["produit"]["id"],
            "motif": "x",
            "description": " ",
        },
        format="json",
    )
    assert vide.status_code == 422
    assert vide.json()["erreur"]["code"] == "description_requise"
    autre, _ = client_authentifie(role="acheteur")
    assert autre.get(f"{API}/signalements/mes-signalements").json() == []
    assert autre.get(f"{API}/admin/signalements").status_code == 403


# --- Compteurs ADM-01 -----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_compteurs_admin_incluent_litiges_et_signalements(commande_livree: dict[str, Any]) -> None:
    amina, mireille = commande_livree["amina"], commande_livree["mireille"]
    amina.post(
        f"{API}/remboursements",
        {
            "commande_id": commande_livree["commande"]["id"],
            "motif": "autre",
            "montant_demande_fcfa": 500,
        },
        format="json",
    )
    amina.post(
        f"{API}/signalements",
        {
            "cible_type": "utilisateur",
            "cible_id": "00000000-0000-0000-0000-000000000001",
            "motif": "spam",
            "description": "Messages insistants.",
        },
        format="json",
    )
    compteurs = mireille.get(f"{API}/admin/utilisateurs/compteurs")
    assert compteurs.status_code == 200
    assert compteurs.json()["litiges_en_attente"] == 1
    assert compteurs.json()["signalements_ouverts"] == 1

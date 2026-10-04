"""Tests d'intégration API de la feature F-13 (chat, SPEC-10).

Parcours complet par HTTP : ouverture d'une conversation entre les deux
parties d'une commande, refus d'un tiers (404), envoi idempotent, refus pour
message vide (422), blocage (403) puis déblocage, compteur de non-lus, ticket
WebSocket, notification `chat` émise et signalement d'un message réutilisant
la file d'administration (F-09).
"""

from typing import Any

import pytest
from apps.commandes.models import Commande, StatutCommande
from apps.conversations.models import Conversation, Message
from apps.litiges.models import Signalement
from apps.notifications.models import Notification
from rest_framework.test import APIClient

POINT = {"type": "Point", "coordinates": [11.5137, 3.848]}


def _client(client_api: APIClient, generer_jeton_acces: Any, utilisateur: Any) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {generer_jeton_acces(utilisateur)}")
    return client


def _commande(acheteur: Any, agriculteur: Any) -> Commande:
    return Commande.objects.create(
        reference=f"C{Commande.objects.count() + 1:05d}",
        acheteur=acheteur,
        agriculteur=agriculteur,
        statut=StatutCommande.EN_ATTENTE,
        adresse_livraison_texte="Yaoundé, test F-13",
        position_livraison=POINT,
        position_recuperation=POINT,
        distance_km=6,
        sous_total_fcfa=2500,
        frais_livraison_fcfa=1400,
        total_fcfa=3900,
    )


@pytest.fixture
def couple(db: Any, fabrique_utilisateur: Any) -> dict[str, Any]:
    amina = fabrique_utilisateur(role="acheteur", nom="Amina")
    paul = fabrique_utilisateur(role="agriculteur", nom="Paul")
    return {"amina": amina, "paul": paul, "commande": _commande(amina, paul)}


@pytest.mark.integration
def test_creer_conversation_partie_puis_tiers_404(
    db: Any, couple: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    amina_client = _client(client_api, generer_jeton_acces, couple["amina"])
    reponse = amina_client.post(
        "/api/v1/conversations",
        {"commande_id": str(couple["commande"].id)},
        format="json",
    )
    assert reponse.status_code == 201, reponse.content
    id_conv = reponse.json()["id"]
    assert Conversation.objects.filter(id=id_conv).exists()

    # Un tiers sans lien avec la commande reçoit 404 (ressource privée).
    tiers = _client(client_api, generer_jeton_acces, _commande_tiers(couple))
    refus = tiers.post(
        "/api/v1/conversations",
        {"commande_id": str(couple["commande"].id)},
        format="json",
    )
    assert refus.status_code == 404


def _commande_tiers(couple: dict[str, Any]) -> Any:
    from django.contrib.auth import get_user_model

    tiers = get_user_model().objects.create_user(
        telephone="+237699999999", password="x", nom="Tiers", role="acheteur"
    )
    return tiers


@pytest.mark.integration
def test_envoi_message_idempotent_et_liste(
    db: Any, couple: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    amina = couple["amina"]
    paul = couple["paul"]
    amina_client = _client(client_api, generer_jeton_acces, amina)
    conv = amina_client.post(
        "/api/v1/conversations", {"commande_id": str(couple["commande"].id)}, format="json"
    ).json()

    url = f"/api/v1/conversations/{conv['id']}/messages"
    premier = amina_client.post(
        url, {"contenu": "Bonjour Paul", "id_client": "api-1"}, format="json"
    )
    assert premier.status_code == 201, premier.content
    # Renvoi du même id_client : aucune création (200), aucun doublon.
    second = amina_client.post(
        url, {"contenu": "Bonjour Paul", "id_client": "api-1"}, format="json"
    )
    assert second.status_code == 200, second.content
    assert Message.objects.filter(conversation=conv["id"]).count() == 1

    # Paul consulte ses conversations : une non lue en provenance d'Amina.
    paul_client = _client(client_api, generer_jeton_acces, paul)
    liste = paul_client.get("/api/v1/conversations")
    assert liste.status_code == 200
    element = next(c for c in liste.json()["elements"] if c["id"] == conv["id"])
    assert element["non_lus"] == 1
    assert element["autre_participant"]["id"] == amina.id


@pytest.mark.integration
def test_message_vide_refuse_422(
    db: Any, couple: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    amina_client = _client(client_api, generer_jeton_acces, couple["amina"])
    conv = amina_client.post(
        "/api/v1/conversations", {"commande_id": str(couple["commande"].id)}, format="json"
    ).json()
    reponse = amina_client.post(
        f"/api/v1/conversations/{conv['id']}/messages", {"id_client": "vide-1"}, format="json"
    )
    assert reponse.status_code == 422
    assert reponse.json()["erreur"]["code"] == "message_vide"


@pytest.mark.integration
def test_blocage_puis_deblocage(
    db: Any, couple: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    amina = couple["amina"]
    paul = couple["paul"]
    amina_client = _client(client_api, generer_jeton_acces, amina)
    paul_client = _client(client_api, generer_jeton_acces, paul)
    conv = amina_client.post(
        "/api/v1/conversations", {"commande_id": str(couple["commande"].id)}, format="json"
    ).json()

    # Paul bloque Amina.
    bloc = paul_client.post("/api/v1/blocages", {"utilisateur_id": amina.id}, format="json")
    assert bloc.status_code == 201, bloc.content

    # Amina ne peut plus écrire (403 explicatif).
    refus = amina_client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        {"contenu": "allo", "id_client": "bl-1"},
        format="json",
    )
    assert refus.status_code == 403
    assert refus.json()["erreur"]["code"] == "blocage_actif"

    # Paul débloque → l'envoi passe de nouveau.
    debl = paul_client.delete(f"/api/v1/blocages/{amina.id}")
    assert debl.status_code == 204
    ok = amina_client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        {"contenu": "allo", "id_client": "bl-2"},
        format="json",
    )
    assert ok.status_code == 201, ok.content


@pytest.mark.integration
def test_ticket_ws_et_notification_chat(
    db: Any, couple: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    paul = couple["paul"]
    amina_client = _client(client_api, generer_jeton_acces, couple["amina"])

    ticket = amina_client.post("/api/v1/ws/ticket")
    assert ticket.status_code == 200
    corps = ticket.json()
    assert corps["expire_dans"] == 30
    assert corps["ticket"]

    conv = amina_client.post(
        "/api/v1/conversations", {"commande_id": str(couple["commande"].id)}, format="json"
    ).json()
    amina_client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        {"contenu": "message à Paul", "id_client": "notif-1"},
        format="json",
    )
    # La notification `chat` est bien destinée à Paul.
    assert Notification.objects.filter(destinataire=paul, type="chat").exists()


@pytest.mark.integration
def test_signalement_message_reutilise_la_file_admin(
    db: Any, couple: dict[str, Any], client_api: APIClient, generer_jeton_acces: Any
) -> None:
    amina_client = _client(client_api, generer_jeton_acces, couple["amina"])
    conv = amina_client.post(
        "/api/v1/conversations", {"commande_id": str(couple["commande"].id)}, format="json"
    ).json()
    message = amina_client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        {"contenu": "à signaler", "id_client": "sig-1"},
        format="json",
    ).json()

    # Paul signale le message d'Amina via la route existante /signalements.
    paul_client = _client(client_api, generer_jeton_acces, couple["paul"])
    reponse = paul_client.post(
        "/api/v1/signalements",
        {
            "cible_type": "message",
            "cible_id": message["id"],
            "motif": "contenu_inapproprie",
            "description": "Message déplacé, à examiner par la plateforme.",
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.content
    assert Signalement.objects.filter(cible_type="message", cible_id=message["id"]).exists()

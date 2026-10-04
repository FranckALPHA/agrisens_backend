"""Tests du consumer WebSocket du chat (F-13, plan §7).

`WebsocketCommunicator` exerce le cycle réel : ticket à usage unique, refus
sans ticket, réception d'un event poussé sur le groupe, battement périodique
et reprise après reconnexion (`dernier_id`).
"""

from typing import Any

import pytest
from apps.conversations.consommateurs import (
    CODE_AUTORISATION_REFUSEE,
    creer_ticket,
    publish_event,
    websocket_urlpatterns,
)
from apps.conversations.models import Conversation, Message, Participant, TypeConversation
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

APP = URLRouter(websocket_urlpatterns)


def _communicator(ticket: str = "") -> WebsocketCommunicator:
    path = "/ws" if not ticket else f"/ws?ticket={ticket}"
    return WebsocketCommunicator(APP, path)


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_ticket_valide_autorise_la_connexion() -> None:
    ticket = creer_ticket("utilisateur-1", 30)
    communicator = _communicator(ticket)
    accepte, _ = await communicator.connect()
    assert accepte is True
    await communicator.disconnect()


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_refus_sans_ticket() -> None:
    communicator = _communicator()
    accepte, code = await communicator.connect()
    assert accepte is False
    assert code == CODE_AUTORISATION_REFUSEE


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_ticket_a_usage_unique() -> None:
    ticket = creer_ticket("utilisateur-2", 30)
    premier = _communicator(ticket)
    accepte, _ = await premier.connect()
    assert accepte is True
    await premier.disconnect()
    # Le même ticket, déjà consommé, est refusé.
    second = _communicator(ticket)
    accepte2, _ = await second.connect()
    assert accepte2 is False


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_event_pousse_sur_le_groupe_est_recu() -> None:
    identifiant = "utilisateur-3"
    ticket = creer_ticket(identifiant, 30)
    communicator = _communicator(ticket)
    await communicator.connect()
    await publish_event(
        identifiant, {"type": "message.nouveau", "donnees": {"id": "m1", "contenu": "salut"}}
    )
    recu = await communicator.receive_json_from(timeout=2)
    assert recu == {"type": "message.nouveau", "donnees": {"id": "m1", "contenu": "salut"}}
    await communicator.disconnect()


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_battement_periodique(settings: Any) -> None:
    settings.WS_BATTEMENT_S = 1
    identifiant = "utilisateur-4"
    ticket = creer_ticket(identifiant, 30)
    communicator = _communicator(ticket)
    await communicator.connect()
    recu = await communicator.receive_json_from(timeout=3)
    assert recu == {"type": "battement"}
    await communicator.disconnect()


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_reprendre_rejoue_les_messages_apres_dernier_id(
    fabrique_utilisateur: Any,
) -> None:
    def _preparer() -> tuple[str, str]:
        amina = fabrique_utilisateur(role="acheteur")
        paul = fabrique_utilisateur(role="agriculteur")
        conversation = Conversation.objects.create(type=TypeConversation.COMMANDE)
        Participant.objects.create(conversation=conversation, utilisateur=amina)
        Participant.objects.create(conversation=conversation, utilisateur=paul)
        Message.objects.create(
            conversation=conversation, auteur=amina, id_client="r1", contenu="un"
        )
        m2 = Message.objects.create(
            conversation=conversation, auteur=paul, id_client="r2", contenu="deux"
        )
        return amina.id, m2.id

    identifiant, id_m2 = await sync_to_async(_preparer)()

    # Le ticket doit porter l'identifiant du participant qui se reconnecte.
    ticket = creer_ticket(identifiant, 30)
    communicator = _communicator(ticket)
    await communicator.connect()

    await communicator.send_json_to({"type": "reprendre", "dernier_id": id_m2})
    # Aucun message antérieur à renvoyer après le tout dernier.
    await communicator.receive_nothing(timeout=1)

    # Reprise depuis le premier message : on rejoue le second.
    m1_id = await sync_to_async(lambda: Message.objects.get(id_client="r1").id)()
    await communicator.send_json_to({"type": "reprendre", "dernier_id": m1_id})
    recu = await communicator.receive_json_from(timeout=3)
    assert recu["type"] == "message.nouveau"
    assert recu["donnees"]["id"] == id_m2
    await communicator.disconnect()


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
async def test_publier_synchrone_via_wrappers_sans_socket() -> None:
    """`publish_event` sur un groupe vide ne lève pas (aucun abonné)."""
    couche = get_channel_layer()
    assert couche is not None
    await publish_event("utilisateur-inexistant", {"type": "message.lu", "donnees": {}})
    # Le wrapper synchrone avaloute toute erreur (best effort Q-46c).
    from apps.conversations.consommateurs import publier

    async_to_sync(publish_event)  # garantit que le helper est importable en sync
    publier("utilisateur-inexistant", {"type": "message.lu", "donnees": {}})

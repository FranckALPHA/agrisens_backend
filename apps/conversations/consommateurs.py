"""Consumer WebSocket du chat temps réel (F-13, docs/08 et docs/10 §6).

Un unique canal par utilisateur, route `/ws?ticket=…` :

- l'authentification n'est pas faite par un middleware Channels : le consumer
  valide lui-même le **ticket à usage unique** stocké au cache par
  `POST /ws/ticket` (docs/03 SPEC-10). Ticket absent/expiré/consommé →
  fermeture code `4001` ;
- le socket joint le groupe `user_<id>` ; les events destinés à l'utilisateur
  (`message.nouveau`, `message.lu`, `notification.nouvelle`) sont adressés à ce
  groupe par `publier()` ;
- une **tâche de battement** envoie `{"type": "battement"}` toutes les
  `WS_BATTEMENT_S` secondes ; le client coupe et se reconnecte si un trou
  dépasse environ deux battements ;
- la reprise après reconnexion (`{"type": "reprendre", "dernier_id": …}`)
  renvoie, sous forme de `message.nouveau`, les messages des conversations de
  l'utilisateur créés **après** ce message (hypothèse Q-46b). Sans `dernier_id`
  le client recharge simplement l'historique par HTTP.

La couche de canaux est en mémoire (`InMemoryChannelLayer`) : **une seule
instance** du serveur en V1 (hypothèse Q-46e).
"""

import asyncio
import contextlib
import json
from typing import Any

from asgiref.sync import async_to_sync, sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.core.cache import cache
from django.urls import re_path

CLE_TICKET = "wsticket:"
CODE_AUTORISATION_REFUSEE = 4001


def creer_ticket(utilisateur_id: str, duree_s: int) -> str:
    """Émet un ticket WebSocket à usage unique et le stocke au cache (Q-46e)."""
    import secrets

    ticket = secrets.token_urlsafe(24)
    cache.set(f"{CLE_TICKET}{ticket}", utilisateur_id, duree_s)
    return ticket


@sync_to_async
def _consommer_ticket(ticket: str) -> str | None:
    """Renvoie l'`utilisateur_id` puis supprime le ticket (usage unique, 30 s)."""
    if not ticket:
        return None
    utilisateur_id: str | None = cache.get(f"{CLE_TICKET}{ticket}")
    if utilisateur_id is not None:
        cache.delete(f"{CLE_TICKET}{ticket}")
    return utilisateur_id


@sync_to_async
def _messages_apres(utilisateur_id: str, dernier_id: str) -> list[dict[str, Any]]:
    """Messages des conversations de l'utilisateur créés après `dernier_id` (Q-46b)."""
    from apps.conversations.models import Conversation, Message

    reference = Message.objects.filter(id=dernier_id).first()
    requete = Message.objects.filter(
        conversation__participants__utilisateur_id=utilisateur_id,
        supprime_le__isnull=True,
    )
    if reference is not None:
        requete = requete.filter(cree_le__gte=reference.cree_le).exclude(id=reference.id)
    messages = requete.select_related("conversation", "auteur").order_by("cree_le", "id")
    resultats: list[dict[str, Any]] = []
    conversations_vues: dict[str, Conversation] = {}
    for message in messages:
        conversation = conversations_vues.setdefault(message.conversation_id, message.conversation)
        resultats.append(_enveloppe_message(message, conversation))
    return resultats


def _enveloppe_message(message: Any, conversation: Any) -> dict[str, Any]:
    """Sérialise un message pour l'event WS `message.nouveau` (léger, sans ORM)."""
    return {
        "id": message.id,
        "conversation": conversation.id,
        "type_conversation": conversation.type,
        "auteur": message.auteur_id,
        "contenu": message.contenu,
        "fichier_id": message.fichier_id,
        "id_client": message.id_client,
        "cree_le": message.cree_le.isoformat(),
    }


async def publish_event(user_id: str, event: dict[str, Any]) -> None:
    """Publie un event JSON sur le groupe `user_<id>` (enveloppe `{"type", "donnees"}`)."""
    from channels.layers import get_channel_layer

    couche = get_channel_layer()
    if couche is None:  # pragma: no cover - la couche est toujours configurée
        return
    await couche.group_send(f"user_{user_id}", event)


def publier(user_id: str, event: dict[str, Any]) -> None:
    """Wrapper synchrone de `publish_event` pour les services appelés en HTTP.

    Best effort (hypothèse Q-46c) : si la couche de canaux est indisponible,
    l'exception est avalée — la notification reste en base et le polling HTTP
    du client sert de secours. Ne doit jamais faire échouer la requête émettrice.
    """
    with contextlib.suppress(Exception):  # le temps réel ne bloque jamais le métier
        async_to_sync(publish_event)(user_id, event)


class CanalUtilisateur(AsyncWebsocketConsumer):
    """Canal temps réel d'un utilisateur connecté (chat + notifications)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.utilisateur_id: str = ""
        self._tache_battement: asyncio.Task[None] | None = None
        self._joint_groupe = False

    @property
    def groupe(self) -> str:
        return f"user_{self.utilisateur_id}"

    async def connect(self) -> None:
        ticket = self._extra_param("ticket")
        utilisateur_id = await _consommer_ticket(ticket)
        if utilisateur_id is None:
            # Ticket manquant, expiré ou déjà consommé → refus sans upgrade.
            await self.close(code=CODE_AUTORISATION_REFUSEE)
            return
        self.utilisateur_id = utilisateur_id
        await self.channel_layer.group_add(self.groupe, self.channel_name)
        self._joint_groupe = True
        await self.accept()
        self._tache_battement = asyncio.create_task(self._battement())

    def _extra_param(self, nom: str) -> str:
        from urllib.parse import parse_qs

        requete = self.scope.get("query_string", b"")
        params = parse_qs(requete.decode("utf-8", "replace"))
        valeurs = params.get(nom, [])
        return valeurs[0] if valeurs else ""

    async def disconnect(self, code: int | None) -> None:
        if self._tache_battement is not None:
            self._tache_battement.cancel()
            self._tache_battement = None
        if self._joint_groupe:
            await self.channel_layer.group_discard(self.groupe, self.channel_name)

    async def _battement(self) -> None:
        """Envoie un battement périodique tant que le socket est ouvert."""
        duree = int(getattr(settings, "WS_BATTEMENT_S", 25))
        try:
            while True:
                await asyncio.sleep(duree)
                await self.send(text_data=json.dumps({"type": "battement"}))
        except asyncio.CancelledError:
            return

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        """Messages du client : uniquement la commande de contrôle `reprendre`."""
        if not text_data:
            return
        try:
            charge: dict[str, Any] = json.loads(text_data)
        except (ValueError, TypeError):
            return
        if charge.get("type") == "reprendre":
            await self._reprendre(str(charge.get("dernier_id") or ""))

    async def _reprendre(self, dernier_id: str) -> None:
        if not dernier_id:
            return
        for donnees in await _messages_apres(self.utilisateur_id, dernier_id):
            await self.send(text_data=json.dumps({"type": "message.nouveau", "donnees": donnees}))

    # --- Handlers des events adressés au groupe -----------------------------

    async def message_nouveau(self, event: dict[str, Any]) -> None:
        await self._transmettre("message.nouveau", event)

    async def message_lu(self, event: dict[str, Any]) -> None:
        await self._transmettre("message.lu", event)

    async def notification_nouvelle(self, event: dict[str, Any]) -> None:
        await self._transmettre("notification.nouvelle", event)

    async def _transmettre(self, type_event: str, event: dict[str, Any]) -> None:
        await self.send(
            text_data=json.dumps({"type": type_event, "donnees": event.get("donnees", {})})
        )


websocket_urlpatterns = [
    re_path(r"^ws$", CanalUtilisateur.as_asgi(), name="ws_chat"),
]

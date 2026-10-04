"""Tests unitaires du service de conversation (F-13, plan §7).

On exerce la logique métier sans HTTP : accès partie, idempotence `id_client`,
blocage dans les deux sens, stockage brut du contenu (sécurité script),
marquage « lu » et compteur de non-lus.
"""

from typing import Any

import pytest
from apps.conversations.models import (
    Blocage,
    Conversation,
    Message,
    Participant,
    TypeConversation,
)
from apps.conversations.services import ServiceBlocage, ServiceConversation, ServiceMessage
from apps.fichiers.models import ContexteFichier, Fichier
from commun.exceptions import AccesInterdit, NonTrouve, ValidationMetier


def _conversation(*participants: Any) -> Conversation:
    conversation = Conversation.objects.create(type=TypeConversation.COMMANDE)
    for partie in participants:
        Participant.objects.create(conversation=conversation, utilisateur=partie)
    return conversation


def _fichier(proprietaire: Any) -> Fichier:
    return Fichier.objects.create(
        proprietaire=proprietaire,
        contexte=ContexteFichier.CHAT,
        cle_stockage=f"chat/{proprietaire.id}-piece.jpg",
        nom_original="piece.jpg",
        type_mime="image/jpeg",
        taille_octets=1234,
    )


@pytest.mark.unit
def test_envoyer_message_stocke_le_contenu_brut(db: Any, fabrique_utilisateur: Any) -> None:
    """Un `<script>` reste du texte brut : aucune exécution côté client (PW-F13-04)."""
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    conversation = _conversation(amina, paul)
    message, cree = ServiceMessage.envoyer(
        conversation, amina, "<script>alert(1)</script>", None, "id-1"
    )
    assert cree is True
    assert message.contenu == "<script>alert(1)</script>"
    assert message.fichier is None


@pytest.mark.unit
def test_message_vide_refuse(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    conversation = _conversation(amina, paul)
    with pytest.raises(ValidationMetier) as exc:
        ServiceMessage.envoyer(conversation, amina, "   ", None, "id-vide")
    assert exc.value.code == "message_vide"
    assert exc.value.statut_http == 422


@pytest.mark.unit
def test_idempotence_id_client_aucun_doublon(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    conversation = _conversation(amina, paul)
    premier, cree1 = ServiceMessage.envoyer(conversation, amina, "Bonjour", None, "dup-1")
    second, cree2 = ServiceMessage.envoyer(conversation, amina, "Bonjour", None, "dup-1")
    assert cree1 is True
    assert cree2 is False
    assert premier.id == second.id
    assert Message.objects.filter(conversation=conversation).count() == 1


@pytest.mark.unit
def test_blocage_bloque_l_envoi_dans_les_deux_sens(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    conversation = _conversation(amina, paul)
    ServiceBlocage.bloquer(paul, amina.id)
    # Amina (bloquée) ne peut plus écrire.
    with pytest.raises(AccesInterdit) as exc:
        ServiceMessage.envoyer(conversation, amina, "coucou", None, "b1")
    assert exc.value.code == "blocage_actif"
    assert exc.value.statut_http == 403
    # Paul, qui a bloqué, ne peut pas écrire non plus (réciprocité Q-15).
    with pytest.raises(AccesInterdit):
        ServiceMessage.envoyer(conversation, paul, "coucou", None, "b2")
    # Débloquer rétablit l'envoi.
    assert ServiceBlocage.debloquer(paul, amina.id) is True
    _, cree = ServiceMessage.envoyer(conversation, amina, "libre", None, "b3")
    assert cree is True


@pytest.mark.unit
def test_marquer_lu_et_compteur_non_lus(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    conversation = _conversation(amina, paul)
    message, _ = ServiceMessage.envoyer(conversation, amina, "salut", None, "lu-1")

    # Paul n'a rien lu : un message des autres est non lu.
    assert conversation.non_lus(paul) == 1
    ServiceMessage.marquer_lu(conversation, paul, message.id)
    assert conversation.non_lus(paul) == 0
    # Les messages de Paul ne comptent pas dans ses non-lus.
    ServiceMessage.envoyer(conversation, paul, "reponds", None, "lu-2")
    assert conversation.non_lus(paul) == 0
    # Amina a un non-lu (le message de Paul).
    assert conversation.non_lus(amina) == 1


@pytest.mark.unit
def test_piece_jointe_doit_appartenir_a_l_envoyeur(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    conversation = _conversation(amina, paul)
    fichier = _fichier(amina)
    message, cree = ServiceMessage.envoyer(conversation, amina, None, fichier.id, "pj-1")
    assert cree is True
    assert message.fichier_id == fichier.id
    # Un fichier d'un tiers (autre propriétaire) est refusé (404 silencieux).
    fichier_alain = _fichier(paul)
    with pytest.raises(NonTrouve):
        ServiceMessage.envoyer(conversation, amina, None, fichier_alain.id, "pj-2")


@pytest.mark.unit
def test_vue_liste_et_detail_participant(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    stranger = fabrique_utilisateur(role="acheteur")
    conversation = _conversation(amina, paul)
    assert conversation in ServiceConversation.vue_liste(amina)
    assert ServiceConversation.obtenir_participante(amina, conversation.id).id == conversation.id
    # Un non-participant voit 404 (ressource privée, jamais 403).
    with pytest.raises(NonTrouve):
        ServiceConversation.obtenir_participante(stranger, conversation.id)


@pytest.mark.unit
def test_blocage_soimeme_refuse(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    with pytest.raises(ValidationMetier):
        ServiceBlocage.bloquer(amina, amina.id)
    assert Blocage.objects.count() == 0

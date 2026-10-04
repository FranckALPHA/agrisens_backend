"""Sérialiseurs DRF de l'app conversations (F-13, docs/03 SPEC-10).

Les sérialiseurs de **création** ne font que valider la forme ; toute la
logique métier (partie de la relation, blocage, idempotence, notifications)
vit dans `services.py` (règle AGENTS : logique métier uniquement dans les
services).
"""

from typing import Any

from rest_framework import serializers

from apps.conversations.models import (
    Blocage,
    Conversation,
    Message,
    Participant,
    TypeConversation,
)
from apps.fichiers.serializers import FichierSerializer


class ParticipantMinimalSerializer(serializers.Serializer):
    """Représentation légère d'un utilisateur (autre participant)."""

    id = serializers.CharField(read_only=True)
    nom = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)


class MessageSerializer(serializers.ModelSerializer):
    """Message rendu au client. `contenu` est du **texte brut** ; le client le
    rend échappé (React) — aucune exécution de script (PW-F13-04)."""

    fichier = FichierSerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "auteur",
            "id_client",
            "contenu",
            "fichier",
            "supprime_le",
            "cree_le",
        ]
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    """Conversation enrichie de son autre participant et de ses non-lus.

    `autre_participant` et `non_lus` dépendent de l'utilisateur qui consulte :
    ils sont injectés via le `context["utilisateur"]` par la vue/le service.
    """

    autre_participant = serializers.SerializerMethodField()
    non_lus = serializers.SerializerMethodField()
    dernier_message = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "commande",
            "accompagnement",
            "autre_participant",
            "non_lus",
            "dernier_message",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = fields

    def get_autre_participant(self, objet: Conversation) -> Any:
        utilisateur = self.context.get("utilisateur")
        autre = objet.participants.exclude(utilisateur_id=getattr(utilisateur, "id", None)).first()
        if autre is None:
            return None
        return ParticipantMinimalSerializer(autre.utilisateur).data

    def get_non_lus(self, objet: Conversation) -> int:
        return objet.non_lus(self.context.get("utilisateur"))

    def get_dernier_message(self, objet: Conversation) -> Any:
        message = objet.dernier_message()
        return MessageSerializer(message).data if message is not None else None


class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Participant
        fields = ["id", "conversation", "utilisateur", "dernier_message_lu", "cree_le"]
        read_only_fields = fields


class BlocageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Blocage
        fields = ["id", "bloqueur", "bloque", "cree_le"]
        read_only_fields = fields


# --- Sérialiseurs d'entrée (validation de forme uniquement) -----------------


class ConversationCreationSerializer(serializers.Serializer):
    """POST /conversations — exactement un de `commande_id` / `accompagnement_id`."""

    commande_id = serializers.CharField(required=False, allow_blank=False)
    accompagnement_id = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        a_commande = bool(attrs.get("commande_id"))
        a_accompagnement = bool(attrs.get("accompagnement_id"))
        if a_commande == a_accompagnement:
            raise serializers.ValidationError(
                {
                    "type_conversation": (
                        "Fournissez exactement un rattachement : commande_id ou accompagnement_id."
                    )
                }
            )
        attrs["type"] = TypeConversation.COMMANDE if a_commande else TypeConversation.ACCOMPAGNEMENT
        return attrs


class MessageCreationSerializer(serializers.Serializer):
    """POST /conversations/{id}/messages — `id_client` requis (idempotence)."""

    contenu = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    fichier_id = serializers.CharField(required=False, allow_blank=False)
    id_client = serializers.CharField(max_length=64)

    def validate_contenu(self, valeur: str | None) -> str | None:
        if valeur is not None and not valeur.strip():
            raise serializers.ValidationError("Le contenu ne peut pas être vide.")
        return valeur


class MarquerLuSerializer(serializers.Serializer):
    """POST /conversations/{id}/lu — pointe le dernier message lu du participant."""

    dernier_message_lu_id = serializers.CharField()


class BlocageCreationSerializer(serializers.Serializer):
    """POST /blocages — l'utilisateur à bloquer."""

    utilisateur_id = serializers.CharField()

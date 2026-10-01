"""Serializers du module notifications."""

from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Notification ; l'état lu est exposé par le champ dérivé `non_lue`."""

    non_lue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "type", "titre", "contenu", "lien", "non_lue", "lue_le", "cree_le"]
        read_only_fields = fields

"""Serializers pour l'exposition des référentiels."""

from rest_framework import serializers

from apps.referentiels.models import Referentiel


class ReferentielSerializer(serializers.ModelSerializer):
    """Représentation d'une entrée de référentiel."""

    class Meta:
        model = Referentiel
        fields = ["code", "libelle", "ordre"]

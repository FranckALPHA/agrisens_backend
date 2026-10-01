"""Serializers du module fichiers.

`cle_stockage` n'est jamais exposé à l'API ([[02-modele-donnees]] §2.8).
"""

from rest_framework import serializers

from apps.fichiers.models import ContexteFichier, Fichier


class FichierSerializer(serializers.ModelSerializer):
    """Représentation publique d'un fichier (sans chemin de stockage)."""

    class Meta:
        model = Fichier
        fields = [
            "id",
            "contexte",
            "nom_original",
            "type_mime",
            "taille_octets",
            "public",
            "cree_le",
        ]
        read_only_fields = fields


class TeleversementSerializer(serializers.Serializer):
    """POST /fichiers — multipart : `fichier` (binaire) + `contexte`."""

    fichier = serializers.FileField()
    contexte = serializers.ChoiceField(choices=ContexteFichier.choices)

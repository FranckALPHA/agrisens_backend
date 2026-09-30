"""Modèle abstrait de base avec identifiant UUID v4 et horodatage UTC.

Conventions techniques §2 et prompt F-00 point 5 :
- Identifiants : UUID v4 stockés sous forme de chaîne CHAR(36)
- Horodatages : cree_le et maj_le en UTC (via commun.horloge.maintenant)
"""

import uuid

from django.db import models

from commun.horloge import maintenant


def generer_uuid() -> str:
    """Génère un identifiant UUID v4 au format standard de 36 caractères."""
    return str(uuid.uuid4())


class ModeleBase(models.Model):
    """Classe abstraite fournissant id UUID, cree_le et maj_le."""

    id = models.CharField(
        max_length=36,
        primary_key=True,
        default=generer_uuid,
        editable=False,
    )
    cree_le = models.DateTimeField(
        default=maintenant,
        editable=False,
    )
    maj_le = models.DateTimeField(
        default=maintenant,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs) -> None:
        self.maj_le = maintenant()
        super().save(*args, **kwargs)

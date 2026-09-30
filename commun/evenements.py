"""Bus d'événements synchrone en mémoire pour la communication entre apps.

Conventions techniques §5 et prompt F-00 point 14.
Permet d'émettre des événements métier découplés (ex: PaiementConfirme)
sans couplage direct entre les apps.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from commun.horloge import maintenant

logger = logging.getLogger("agrisens.evenements")


@dataclass
class Evenement:
    """Classe de base pour tous les événements métier."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    horodatage: datetime = field(default_factory=maintenant)


E = TypeVar("E", bound=Evenement)
GestionnaireEvenement = Callable[[Any], None]

_abonnements: dict[type[Evenement], list[GestionnaireEvenement]] = defaultdict(list)


def abonner[E: Evenement](type_evenement: type[E], gestionnaire: Callable[[E], None]) -> None:
    """Abonne un gestionnaire synchrone à un type d'événement."""
    if gestionnaire not in _abonnements[type_evenement]:
        _abonnements[type_evenement].append(gestionnaire)


def desabonner[E: Evenement](type_evenement: type[E], gestionnaire: Callable[[E], None]) -> None:
    """Désabonne un gestionnaire."""
    if gestionnaire in _abonnements[type_evenement]:
        _abonnements[type_evenement].remove(gestionnaire)


def publier(evenement: Evenement) -> None:
    """Publie un événement de façon synchrone auprès de tous ses abonnés."""
    type_evt = type(evenement)
    gestionnaires = list(_abonnements.get(type_evt, []))
    for gestionnaire in gestionnaires:
        try:
            gestionnaire(evenement)
        except Exception:
            logger.exception(
                "Erreur lors de l'exécution du gestionnaire %s pour l'événement %s",
                getattr(gestionnaire, "__name__", str(gestionnaire)),
                type_evt.__name__,
            )
            raise


def vider() -> None:
    """Vide tous les abonnements (utile pour isoler les tests)."""
    _abonnements.clear()

"""Horloge applicative centralisée (UTC).

Toute l'application utilise ``maintenant()`` au lieu de ``django.utils.timezone.now()``
ou ``datetime.datetime.now()``. Permet d'avancer ou figer le temps en test
(conventions techniques §5, prompt F-00 point 13).
"""

from datetime import UTC, datetime, timedelta

_decalage: timedelta = timedelta()
_temps_fige: datetime | None = None


def maintenant() -> datetime:
    """Renvoie l'heure courante UTC, éventuellement décalée ou figée."""
    if _temps_fige is not None:
        return _temps_fige
    return datetime.now(UTC) + _decalage


def fixer(moment: datetime) -> None:
    """Fige l'horloge à une date/heure précise (forcée en UTC)."""
    global _temps_fige
    _temps_fige = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def decaler(duree: timedelta) -> None:
    """Décale l'horloge d'une durée positive ou négative."""
    global _decalage, _temps_fige
    if _temps_fige is not None:
        _temps_fige += duree
    else:
        _decalage += duree


def reinitialiser() -> None:
    """Réinitialise l'horloge sur le temps réel."""
    global _decalage, _temps_fige
    _decalage = timedelta()
    _temps_fige = None

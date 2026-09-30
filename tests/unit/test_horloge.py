"""Tests unitaires de l'horloge centralisée."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from commun.horloge import decaler, fixer, maintenant, reinitialiser


@pytest.fixture(autouse=True)
def reinitialiser_apres_test() -> Iterator[None]:
    reinitialiser()
    yield
    reinitialiser()


@pytest.mark.unit
def test_horloge_utc_consciente() -> None:
    """L'horloge renvoie un datetime avec fuseau UTC."""
    t = maintenant()
    assert t.tzinfo is not None
    assert t.tzinfo == UTC


@pytest.mark.unit
def test_horloge_decalage() -> None:
    """Le décalage de l'horloge avance ou recule l'heure perçue."""
    t0 = maintenant()
    decaler(timedelta(hours=2))
    t1 = maintenant()
    delta = t1 - t0
    assert 7100 <= delta.total_seconds() <= 7300


@pytest.mark.unit
def test_horloge_fixer_et_reinitialiser() -> None:
    """Fixer l'horloge fige le temps, et reinitialiser rétablit le temps réel."""
    moment = datetime(2026, 12, 25, 10, 0, 0, tzinfo=UTC)
    fixer(moment)
    assert maintenant() == moment

    reinitialiser()
    assert maintenant().year == datetime.now(UTC).year

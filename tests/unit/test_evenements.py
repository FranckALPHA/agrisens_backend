from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from commun.evenements import Evenement, abonner, desabonner, publier, vider


@dataclass
class EvenementTest(Evenement):
    message: str = ""


@dataclass
class AutreEvenement(Evenement):
    valeur: int = 0


@pytest.fixture(autouse=True)
def nettoyer_bus() -> Iterator[None]:
    vider()
    yield
    vider()


@pytest.mark.unit
def test_publication_et_reception_evenement() -> None:
    """Un gestionnaire abonné reçoit l'événement lors de sa publication."""
    receptions: list[str] = []

    def handler(evt: EvenementTest) -> None:
        receptions.append(evt.message)

    abonner(EvenementTest, handler)
    publier(EvenementTest(message="Bonjour AgriSens"))

    assert receptions == ["Bonjour AgriSens"]


@pytest.mark.unit
def test_isolation_par_type_evenement() -> None:
    """Un gestionnaire ne reçoit que les événements du type auquel il est abonné."""
    receptions: list[int] = []

    def handler_autre(evt: AutreEvenement) -> None:
        receptions.append(evt.valeur)

    abonner(AutreEvenement, handler_autre)
    publier(EvenementTest(message="Ignoré"))

    assert receptions == []

    publier(AutreEvenement(valeur=42))
    assert receptions == [42]


@pytest.mark.unit
def test_desabonnement_evenement() -> None:
    """Un gestionnaire désabonné ne reçoit plus d'événement."""
    compteur = [0]

    def handler(evt: EvenementTest) -> None:
        compteur[0] += 1

    abonner(EvenementTest, handler)
    publier(EvenementTest(message="1"))
    assert compteur[0] == 1

    desabonner(EvenementTest, handler)
    publier(EvenementTest(message="2"))
    assert compteur[0] == 1

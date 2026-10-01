from decimal import Decimal

import pytest
from apps.commandes.tarification import arrondir_fcfa, frais_livraison, total_ligne

# Journal §4, P-02 : base 500, 150 FCFA/km, min 500, plafond 5 000.
TARIFS = {"base_fcfa": 500, "tarif_km_fcfa": 150, "minimum_fcfa": 500, "plafond_fcfa": 5000}


def frais(distance: float) -> tuple[int, int]:
    return frais_livraison(distance, **TARIFS)


@pytest.mark.unit
def test_exemples_chiffres_du_journal() -> None:
    assert frais(6) == (6, 1_400)
    assert frais(5.2) == (6, 1_400)
    assert frais(40) == (40, 5_000)
    assert frais(0.3) == (1, 650)


@pytest.mark.unit
def test_minimum_et_plafond() -> None:
    assert frais(0) == (0, 500)
    assert frais(100) == (100, 5_000)


@pytest.mark.unit
def test_total_ligne_arrondi_demi_vers_le_haut() -> None:
    assert total_ligne(Decimal("10"), 500) == 5_000
    assert total_ligne(Decimal("2.5"), 500) == 1_250
    # 1,111 kg × 300 = 333,3 → 333 ; 1,115 × 300 = 334,5 → 335 (Q-40).
    assert total_ligne(Decimal("1.111"), 300) == 333
    assert total_ligne(Decimal("1.115"), 300) == 335


@pytest.mark.unit
def test_arrondi_fcfa() -> None:
    assert arrondir_fcfa(Decimal("12.5")) == 13
    assert arrondir_fcfa(Decimal("12.4")) == 12

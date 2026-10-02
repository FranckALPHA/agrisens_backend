import re

import pytest
from apps.livraisons.services import (
    chiffrer_code,
    comparer_code,
    dechiffrer_code,
    generer_code_remise,
)


@pytest.mark.unit
def test_code_six_chiffres_non_devinable() -> None:
    codes = {generer_code_remise() for _ in range(200)}
    assert len(codes) > 190
    for code in codes:
        assert re.fullmatch(r"[0-9]{6}", code)


@pytest.mark.unit
def test_code_chiffre_illisible_en_base() -> None:
    chiffre = chiffrer_code("123456")
    assert "123456" not in chiffre
    assert dechiffrer_code(chiffre) == "123456"


@pytest.mark.unit
def test_comparaison_temps_constant() -> None:
    chiffre = chiffrer_code("654321")
    assert comparer_code("654321", chiffre) is True
    assert comparer_code("654320", chiffre) is False
    assert comparer_code(" 654321 ", chiffre) is True
    assert comparer_code("654321", "chiffre-invalide") is False

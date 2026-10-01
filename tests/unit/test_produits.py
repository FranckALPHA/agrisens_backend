from decimal import Decimal

import pytest
from apps.produits.models import Produit, StatutProduit
from apps.produits.services import (
    _en_decimal,
    _synchroniser_statut,
    _valider_reference,
)
from commun.exceptions import ValidationMetier


def base(**surcharge) -> dict:
    donnees = {
        "nom": "Tomates",
        "categorie": "legumes",
        "unite": "kg",
        "prix_unitaire_fcfa": 500,
    }
    donnees.update(surcharge)
    return donnees


@pytest.mark.unit
def test_reference_valide() -> None:
    _valider_reference(base())


@pytest.mark.unit
def test_prix_negatif_refuse() -> None:
    with pytest.raises(ValidationMetier) as exc:
        _valider_reference(base(prix_unitaire_fcfa=-100))
    assert exc.value.code == "prix_invalide"


@pytest.mark.unit
def test_prix_non_entier_refuse() -> None:
    with pytest.raises(ValidationMetier) as exc:
        _valider_reference(base(prix_unitaire_fcfa=12.5))
    assert exc.value.code == "prix_invalide"


@pytest.mark.unit
def test_categorie_et_unite_inconnues_refusees() -> None:
    with pytest.raises(ValidationMetier) as exc:
        _valider_reference(base(categorie="extraterrestre"))
    assert exc.value.code == "categorie_inconnue"
    with pytest.raises(ValidationMetier) as exc:
        _valider_reference(base(unite="stere"))
    assert exc.value.code == "unite_inconnue"


@pytest.mark.unit
def test_nom_vide_refuse() -> None:
    with pytest.raises(ValidationMetier) as exc:
        _valider_reference(base(nom="  "))
    assert exc.value.code == "nom_obligatoire"


@pytest.mark.unit
def test_quantite_negative_ou_immense_refusee() -> None:
    with pytest.raises(ValidationMetier) as exc:
        _en_decimal("-3", "quantite_stock")
    assert exc.value.code == "quantite_invalide"
    assert _en_decimal("10.500", "quantite_stock") == Decimal("10.500")


@pytest.mark.unit
def test_disponible_est_stock_moins_reserve() -> None:
    produit = Produit(quantite_stock=Decimal("100"), quantite_reservee=Decimal("10"))
    assert produit.quantite_disponible == Decimal("90")


@pytest.mark.unit
def test_statut_suit_le_stock() -> None:
    produit = Produit(
        quantite_stock=Decimal("0"),
        quantite_reservee=Decimal("0"),
        statut=StatutProduit.DISPONIBLE,
    )
    _synchroniser_statut(produit)
    assert produit.statut == StatutProduit.EPUISE
    produit.quantite_stock = Decimal("5")
    _synchroniser_statut(produit)
    assert produit.statut == StatutProduit.DISPONIBLE


@pytest.mark.unit
def test_retire_ne_bouge_plus() -> None:
    produit = Produit(
        quantite_stock=Decimal("50"),
        quantite_reservee=Decimal("0"),
        statut=StatutProduit.RETIRE,
    )
    _synchroniser_statut(produit)
    assert produit.statut == StatutProduit.RETIRE

"""Tests unitaires du masquage des secrets et téléphones dans les logs."""

import pytest
from commun.journaux import masquer


@pytest.mark.unit
def test_masquage_mots_de_passe() -> None:
    """Les mots de passe sont remplacés par des étoiles."""
    texte1 = 'Tentative de connexion avec password: "MonSuperSecret123!"'
    assert "MonSuperSecret123!" not in masquer(texte1)
    assert "***" in masquer(texte1)

    texte2 = '{"mot_de_passe": "Secret456"}'
    assert "Secret456" not in masquer(texte2)
    assert masquer(texte2) == '{"mot_de_passe": "***"}'


@pytest.mark.unit
def test_masquage_jetons_et_secrets() -> None:
    """Les jetons d'accès, secrets et signatures sont masqués."""
    texte = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    masque = masquer(texte)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in masque
    assert "bearer ***" in masque.lower()

    texte_remise = "Code remise généré : code_remise=123456 pour le livreur"
    assert "123456" not in masquer(texte_remise)


@pytest.mark.unit
def test_masquage_telephone_camerounais() -> None:
    """Les numéros camerounais complets (+237XXXXXXXXX) sont masqués au format +2376XXXXX12."""
    texte = "Utilisateur inscrit avec le numéro +237671234589 avec succès."
    masque = masquer(texte)
    assert "+237671234589" not in masque
    assert "+2376XXXXX89" in masque

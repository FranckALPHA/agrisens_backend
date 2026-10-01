"""Tests unitaires de la configuration et des paramètres de base."""

import pytest
from apps.administration.registre import REGISTRE, cle_connue, cles_publiques, valeurs_de_demarrage
from django.conf import settings
from django.contrib.auth.hashers import Argon2PasswordHasher


@pytest.mark.unit
def test_configuration_django_langue_et_fuseau() -> None:
    """La langue par défaut est le français et le fuseau est UTC."""
    assert settings.LANGUAGE_CODE == "fr"
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True


@pytest.mark.unit
def test_configuration_argon2_en_premier() -> None:
    """Argon2id est le premier hasher de mots de passe configuré dans base.py."""
    from config.settings.base import PASSWORD_HASHERS as BASE_HASHERS

    assert BASE_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    # Vérification que la classe est bien disponible
    assert issubclass(Argon2PasswordHasher, object)


@pytest.mark.unit
def test_modele_utilisateur_personnalise_declare() -> None:
    """Le modèle utilisateur configuré pointe sur l'app utilisateurs."""
    assert settings.AUTH_USER_MODEL == "utilisateurs.Utilisateur"


@pytest.mark.unit
def test_registre_parametres_plateforme_complet() -> None:
    """Le registre contient les 24 paramètres du Journal §4 plus les
    hypothèses F-03 Q-34 (rayon de zone ingénieur) et Q-35 (durée URL signée)."""
    assert len(REGISTRE) == 26

    # Paramètres de livraison de base
    p_base = cle_connue("livraison.base_fcfa")
    assert p_base is not None
    assert p_base.demarrage == 500
    assert p_base.minimum == 0
    assert p_base.maximum == 5000
    assert p_base.public is True

    # Commission plateforme
    p_comm = cle_connue("commission.taux_pb")
    assert p_comm is not None
    assert p_comm.demarrage == 500
    assert p_comm.minimum == 0
    assert p_comm.maximum == 2000
    assert p_comm.public is False

    # Paramètres publics
    publiques = cles_publiques()
    assert "livraison.base_fcfa" in publiques
    assert "commission.taux_pb" not in publiques

    # Démarrages : 24 clés de base + consultation.zone_rayon_km_max (Q-34)
    # et fichiers.duree_url_signee_s (Q-35) ajoutées en F-03.
    demarrages = valeurs_de_demarrage()
    assert len(demarrages) == 26
    assert demarrages["ia.delai_max_s"] == 60

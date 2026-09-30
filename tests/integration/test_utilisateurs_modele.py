"""Tests d'intégration du modèle Utilisateur personnalisé."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

Utilisateur = get_user_model()


@pytest.mark.integration
@pytest.mark.django_db
def test_creation_utilisateur_standard() -> None:
    """Création d'un utilisateur régulier avec mot de passe haché."""
    user = Utilisateur.objects.create_user(
        telephone="+237671112233",
        password="MonMotDePasseFort123!",
        nom="Paul Biya",
        role="agriculteur",
    )
    assert user.id is not None
    assert len(user.id) == 36
    assert user.telephone == "+237671112233"
    assert user.role == "agriculteur"
    assert user.statut_compte == "actif"
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.check_password("MonMotDePasseFort123!") is True
    assert user.check_password("MauvaisPasse") is False


@pytest.mark.integration
@pytest.mark.django_db
def test_creation_super_admin() -> None:
    """Création d'un super-administrateur."""
    admin_user = Utilisateur.objects.create_superuser(
        telephone="+237679998877",
        password="SuperAdminPassword2026!",
        nom="Mireille Admin",
    )
    assert admin_user.role == "super_admin"
    assert admin_user.is_staff is True
    assert admin_user.is_superuser is True
    assert admin_user.has_perm("any_perm") is True
    assert admin_user.has_module_perms("any_app") is True


@pytest.mark.integration
@pytest.mark.django_db
def test_unicite_numero_telephone() -> None:
    """Le numéro de téléphone est strictement unique."""
    Utilisateur.objects.create_user(
        telephone="+237670001122",
        password="Passe123456!",
        nom="Utilisateur 1",
    )
    with pytest.raises(IntegrityError):
        Utilisateur.objects.create_user(
            telephone="+237670001122",
            password="AutrePasse123456!",
            nom="Utilisateur 2",
        )

"""Tests d'intégration des commandes de gestion personnalisées (F-00 points 18 et 19)."""

import pytest
from apps.utilisateurs.models import RoleUtilisateur, StatutCompte, Utilisateur
from django.core.management import CommandError, call_command


@pytest.mark.integration
@pytest.mark.django_db
def test_commande_seed_demo_idempotente() -> None:
    """seed_demo crée les comptes des personnages (docs/12 §3) et est idempotente."""
    # Premier passage
    call_command("seed_demo")
    assert Utilisateur.objects.filter(telephone="+237690000001").exists()
    assert Utilisateur.objects.filter(telephone="+237690000002").exists()
    assert Utilisateur.objects.filter(telephone="+237690000004").exists()
    assert Utilisateur.objects.filter(telephone="+237690000005").exists()
    assert Utilisateur.objects.filter(telephone="+237690000000").exists()
    assert Utilisateur.objects.filter(telephone="+237690000099").exists()

    nadia = Utilisateur.objects.get(telephone="+237690000003")
    assert nadia.role == RoleUtilisateur.INGENIEUR_AGRONOME
    # F-11 : Nadia est l'ingénieure de démo validée et disponible pour
    # l'accompagnement de Paul (docs/12 PW-F11-01). Le parcours
    # d'auto-validation (PW-F03) repart d'une inscription fraîche.
    assert nadia.statut_compte == StatutCompte.ACTIF
    suspendu = Utilisateur.objects.get(telephone="+237690000099")
    assert suspendu.statut_compte == StatutCompte.SUSPENDU

    # Deuxième passage sans erreur, toujours 9 comptes
    call_command("seed_demo")
    assert Utilisateur.objects.count() == 9


@pytest.mark.integration
@pytest.mark.django_db
def test_commande_creer_super_admin() -> None:
    """La commande creer_super_admin crée un super-admin avec rôle et statut valides."""
    call_command(
        "creer_super_admin",
        telephone="+237699999999",
        mot_de_passe="SuperSecretPassword123!",
        nom="Root Admin",
    )
    admin_user = Utilisateur.objects.get(telephone="+237699999999")
    assert admin_user.role == RoleUtilisateur.SUPER_ADMIN
    assert admin_user.statut_compte == StatutCompte.ACTIF
    assert admin_user.is_superuser
    assert admin_user.check_password("SuperSecretPassword123!")


@pytest.mark.integration
def test_commande_worker_dry_run() -> None:
    """Le worker se lance en mode --dry-run et s'arrête proprement."""
    call_command("worker", dry_run=True)


@pytest.mark.integration
@pytest.mark.django_db
def test_commande_reinitialiser_e2e_refuse_autres_bases() -> None:
    """La commande reinitialiser_e2e refuse formellement de s'exécuter
    sur une base autre que agrisens_e2e."""
    # En environnement de test, la base active est agrisens_test
    with pytest.raises(CommandError, match="SÉCURITÉ"):
        call_command("reinitialiser_e2e")

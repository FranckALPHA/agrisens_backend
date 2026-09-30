"""Test de vérification des migrations Django."""

import pytest
from django.core.management import call_command


@pytest.mark.integration
@pytest.mark.django_db
def test_aucune_migration_manquante() -> None:
    """Vérifie que tous les modèles ont des migrations à jour (makemigrations --check)."""
    # Si des changements de modèles n'ont pas été migrés, call_command lèvera SystemExit
    call_command("makemigrations", check=True, dry_run=True)

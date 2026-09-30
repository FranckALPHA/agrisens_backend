"""Commande manage.py reinitialiser_e2e pour préparer la base de test Playwright.

Vide la base agrisens_e2e, réapplique les migrations et charge les données de démo.
REFUSE formellement de s'exécuter sur toute autre base (sécurité).
"""

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Réinitialise la base agrisens_e2e pour les tests E2E Playwright."

    def handle(self, *args: Any, **options: Any) -> None:
        nom_base = connection.settings_dict.get("NAME")
        if nom_base != "agrisens_e2e":
            raise CommandError(
                f"SÉCURITÉ : La commande reinitialiser_e2e refuse de s'exécuter sur '{nom_base}'. "
                "Elle est strictement réservée à 'agrisens_e2e'."
            )

        self.stdout.write(f"Réinitialisation de la base '{nom_base}'...")

        # 1. Vider les tables existantes
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            cursor.execute("SHOW TABLES;")
            tables = [ligne[0] for ligne in cursor.fetchall()]
            for table in tables:
                cursor.execute(f"DROP TABLE IF EXISTS `{table}`;")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

        self.stdout.write(self.style.SUCCESS(f"{len(tables)} tables supprimées."))

        # 2. Appliquer les migrations
        self.stdout.write("Application des migrations...")
        call_command("migrate", interactive=False, verbosity=1)

        # 3. Charger les données de démonstration
        self.stdout.write("Chargement des données de démonstration...")
        call_command("seed_demo", verbosity=1)

        self.stdout.write(
            self.style.SUCCESS(f"Base '{nom_base}' réinitialisée et prête pour Playwright.")
        )

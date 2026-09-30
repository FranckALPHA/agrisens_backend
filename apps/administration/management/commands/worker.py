"""Commande de gestion pour lancer le worker de tâches asynchrones APScheduler.

Conventions techniques §5 et prompt F-00 point 18.
Processus séparé exécuté en arrière-plan (systemd en production).
"""

import contextlib
import logging
import signal
import sys
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from django.core.management.base import BaseCommand

logger = logging.getLogger("agrisens.worker")


class Command(BaseCommand):
    help = "Démarre le worker APScheduler pour l'exécution des tâches planifiées d'AgriSens."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Initialise le planificateur et s'arrête sans bloquer (pour les tests).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(self.style.SUCCESS("Démarrage du worker APScheduler AgriSens..."))

        scheduler = BlockingScheduler(timezone="UTC")

        # Registre des tâches périodiques déclarées par les applications.
        # Ex: scheduler.add_job(expirer_commandes, "interval", minutes=15)

        def arret_propre(signum: int, frame: Any) -> None:
            self.stdout.write(self.style.WARNING("\nArrêt du worker reçu..."))
            with contextlib.suppress(Exception):
                scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, arret_propre)
        signal.signal(signal.SIGTERM, arret_propre)

        if options.get("dry_run"):
            self.stdout.write(self.style.SUCCESS("Dry-run : worker initialisé avec succès."))
            return

        try:
            self.stdout.write(self.style.SUCCESS("Worker en écoute (Ctrl+C pour quitter)..."))
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass

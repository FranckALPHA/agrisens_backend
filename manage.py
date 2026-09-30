#!/usr/bin/env python
"""Utilitaire d'administration en ligne de commande pour AgriSens (Django)."""

import os
import sys


def main() -> None:
    """Point d'entrée principal des commandes de gestion."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Impossible d'importer Django. Assurez-vous qu'il est installé et disponible "
            "dans votre environnement virtuel."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

"""Configuration de l'application fichiers."""

from django.apps import AppConfig


class FichiersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fichiers"
    label = "fichiers"
    verbose_name = "Fichiers"

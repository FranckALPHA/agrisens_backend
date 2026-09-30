"""Configuration de l'application référentiels."""

from django.apps import AppConfig


class ReferentielsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.referentiels"
    label = "referentiels"
    verbose_name = "Référentiels"

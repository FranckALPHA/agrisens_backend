"""Configuration de l'application profils."""

from django.apps import AppConfig


class ProfilsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.profils"
    label = "profils"
    verbose_name = "Profils professionnels"

"""Configuration de l'application litiges."""

from django.apps import AppConfig


class LitigesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.litiges"
    label = "litiges"
    verbose_name = "Litiges"

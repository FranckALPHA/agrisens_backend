"""Configuration de l'application statistiques (SPEC-11 — F-14)."""

from django.apps import AppConfig


class StatistiquesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.statistiques"
    label = "statistiques"
    verbose_name = "Statistiques et tableaux de bord"

"""Configuration de l'application plannings (SPEC-08 — IA Groq)."""

from django.apps import AppConfig


class PlanningsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.plannings"
    label = "plannings"
    verbose_name = "Plannings IA"

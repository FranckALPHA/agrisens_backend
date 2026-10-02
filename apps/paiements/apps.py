"""Configuration de l'application paiements."""

from django.apps import AppConfig


class PaiementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.paiements"
    label = "paiements"
    verbose_name = "Paiements"

    def ready(self) -> None:
        # Vérification des paiements non confirmés, exécutable via
        # /dev/taches/verifier_paiements (même pattern que commandes).
        from config.routes_dev import enregistrer_tache_dev

        from apps.paiements.taches import verifier_paiements

        enregistrer_tache_dev("verifier_paiements", verifier_paiements)

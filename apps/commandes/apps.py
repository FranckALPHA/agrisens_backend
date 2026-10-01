"""Configuration de l'application commandes."""

from django.apps import AppConfig


class CommandesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.commandes"
    label = "commandes"
    verbose_name = "Commandes"

    def ready(self) -> None:
        # Tâche d'expiration exécutable via /dev/taches/expirer_commandes
        # (conventions §5 : abonnements faits dans ready()).
        from config.routes_dev import enregistrer_tache_dev

        from apps.commandes.taches import expirer_commandes

        enregistrer_tache_dev("expirer_commandes", expirer_commandes)

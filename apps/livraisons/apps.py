"""Configuration de l'application livraisons."""

from django.apps import AppConfig


class LivraisonsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.livraisons"
    label = "livraisons"
    verbose_name = "Livraisons"

    def ready(self) -> None:
        # Création de la livraison à la confirmation du paiement
        # (conventions §5 : abonnements faits dans ready()).
        from commun.evenements import abonner

        from apps.livraisons.services import creer_apres_paiement
        from apps.paiements.evenements import PaiementReussi

        abonner(PaiementReussi, creer_apres_paiement)

"""Configuration de l'application accompagnements."""

from django.apps import AppConfig


class AccompagnementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accompagnements"
    label = "accompagnements"
    verbose_name = "Accompagnements"

    def ready(self) -> None:
        # Passage à `planifiee` au webhook de paiement réussi (journal Q-05 :
        # conventions §5 — abonnements faits dans ready()).
        from commun.evenements import abonner

        from apps.accompagnements.services import sur_paiement_reussi
        from apps.paiements.evenements import PaiementReussi

        abonner(PaiementReussi, sur_paiement_reussi)

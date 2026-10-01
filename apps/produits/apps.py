"""Configuration de l'application produits."""

from django.apps import AppConfig


class ProduitsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.produits"
    label = "produits"
    verbose_name = "Produits et catalogue"

    def ready(self) -> None:
        # Photos de produits visibles via le catalogue (SPEC-03) : le module
        # fichiers ne connaît pas les produits, il applique ce vérificateur
        # (registre `apps/fichiers/acces.py`, pas d'import croisé).
        from apps.fichiers.acces import inscrire_verificateur
        from apps.fichiers.models import ContexteFichier
        from apps.produits.services import verifier_photo_catalogue

        inscrire_verificateur(ContexteFichier.PRODUIT, verifier_photo_catalogue)

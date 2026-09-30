"""Gestionnaire personnalisé pour le modèle Utilisateur."""

from typing import Any

from django.contrib.auth.models import BaseUserManager


class GestionnaireUtilisateur(BaseUserManager[Any]):
    """Gestionnaire pour la création d'utilisateurs avec identifiant téléphone."""

    def create_user(self, telephone: str, password: str | None = None, **extra_fields: Any) -> Any:
        """Crée et sauvegarde un utilisateur avec son téléphone."""
        if not telephone:
            raise ValueError("Le numéro de téléphone est obligatoire.")

        extra_fields.setdefault("statut_compte", "actif")
        extra_fields.setdefault("role", "acheteur")

        user = self.model(telephone=telephone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(
        self, telephone: str, password: str | None = None, **extra_fields: Any
    ) -> Any:
        """Crée et sauvegarde un super-administrateur."""
        extra_fields["role"] = "super_admin"
        extra_fields["statut_compte"] = "actif"
        extra_fields.setdefault("nom", "Super Administrateur")

        if not password:
            raise ValueError("Le mot de passe est obligatoire pour un super-administrateur.")

        return self.create_user(telephone=telephone, password=password, **extra_fields)

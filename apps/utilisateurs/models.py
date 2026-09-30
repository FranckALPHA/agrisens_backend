"""Modèle personnalisé Utilisateur pour AgriSens.

Conventions techniques §2.1 et prompt F-00 point 6 :
- Identifiant de connexion principal : numéro de téléphone (E.164).
- Rôles : agriculteur, acheteur, ingénieur agronome, livreur, admin, super_admin.
- Statuts de compte : actif, en attente de validation, suspendu, anonymisé.
"""

from typing import Any

from commun.horloge import maintenant
from commun.modeles import generer_uuid
from django.contrib.auth.models import AbstractBaseUser
from django.db import models

from apps.utilisateurs.managers import GestionnaireUtilisateur


class RoleUtilisateur(models.TextChoices):
    AGRICULTEUR = "agriculteur", "Agriculteur"
    ACHETEUR = "acheteur", "Acheteur"
    INGENIEUR_AGRONOME = "ingenieur_agronome", "Ingénieur agronome"
    LIVREUR = "livreur", "Livreur"
    ADMIN = "admin", "Administrateur"
    SUPER_ADMIN = "super_admin", "Super administrateur"


class StatutCompte(models.TextChoices):
    ACTIF = "actif", "Actif"
    EN_ATTENTE_VALIDATION = "en_attente_validation", "En attente de validation"
    SUSPENDU = "suspendu", "Suspendu"
    ANONYMISE = "anonymise", "Anonymisé"


class Utilisateur(AbstractBaseUser):
    """Modèle utilisateur personnalisé central d'AgriSens."""

    id = models.CharField(
        max_length=36,
        primary_key=True,
        default=generer_uuid,
        editable=False,
    )
    cree_le = models.DateTimeField(
        default=maintenant,
        editable=False,
    )
    maj_le = models.DateTimeField(
        default=maintenant,
    )

    telephone = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Numéro de téléphone au format E.164 (+237XXXXXXXXX)",
    )
    email = models.EmailField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Adresse email facultative",
    )
    nom = models.CharField(
        max_length=150,
        help_text="Nom complet ou raison sociale",
    )
    role = models.CharField(
        max_length=30,
        choices=RoleUtilisateur.choices,
        default=RoleUtilisateur.ACHETEUR,
    )
    statut_compte = models.CharField(
        max_length=30,
        choices=StatutCompte.choices,
        default=StatutCompte.ACTIF,
    )
    region = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )
    derniere_connexion_le = models.DateTimeField(
        null=True,
        blank=True,
    )
    doit_changer_mot_de_passe = models.BooleanField(
        default=False,
    )
    reinitialisation_demandee_le = models.DateTimeField(
        null=True,
        blank=True,
    )

    objects = GestionnaireUtilisateur()

    USERNAME_FIELD = "telephone"
    REQUIRED_FIELDS = ["nom"]

    class Meta:
        db_table = "utilisateur"
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self) -> str:
        return f"{self.nom} ({self.telephone}) [{self.role}]"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.maj_le = maintenant()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """Indique si l'utilisateur peut se connecter."""
        return self.statut_compte == StatutCompte.ACTIF

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self.statut_compte = StatutCompte.ACTIF if value else StatutCompte.SUSPENDU

    @property
    def is_staff(self) -> bool:
        """Autorise l'accès à l'administration Django."""
        return self.role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN)

    @property
    def is_superuser(self) -> bool:
        """Indique si l'utilisateur est super-administrateur."""
        return self.role == RoleUtilisateur.SUPER_ADMIN

    def has_perm(self, perm: str, obj: Any = None) -> bool:
        return self.is_superuser

    def has_module_perms(self, app_label: str) -> bool:
        return self.is_superuser or self.is_staff

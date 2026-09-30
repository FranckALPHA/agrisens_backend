"""Modèles du module utilisateurs : compte, session de rafraîchissement et adresse.

Conventions techniques §2.1 et prompt F-00 point 6 / F-02 point 1 :
- Identifiant de connexion principal : numéro de téléphone (E.164).
- Rôles : agriculteur, acheteur, ingénieur agronome, livreur, admin, super_admin.
- Statuts de compte : actif, en attente de validation, suspendu, anonymisé.
- SESSION : jetons de rafraîchissement opaques stockés hachés (journal Q-25).
- ADRESSE : position en POINT SRID 4326, exposée en GeoJSON par l'API.
"""

from typing import Any

from commun.champs_geo import ChampPoint
from commun.horloge import maintenant
from commun.modeles import ModeleBase, generer_uuid
from django.conf import settings
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


class Session(ModeleBase):
    """Session = jeton de rafraîchissement opaque stocké haché (journal Q-25).

    Rotation à chaque usage : le jeton utilisé est marqué revoque_le ;
    sa réutilisation déclenche la révocation de toutes les sessions
    de l'utilisateur.
    """

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sessions",
        help_text="Utilisateur propriétaire de la session",
    )
    jeton_hash = models.CharField(
        max_length=255,
        unique=True,
        help_text="Hachage SHA-256 du jeton de rafraîchissement (jamais le jeton en clair)",
    )
    expire_le = models.DateTimeField(
        help_text="Date d'expiration de la session",
    )
    revoque_le = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date de révocation ou de consommation (rotation)",
    )
    agent_utilisateur = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="User-Agent au moment de la connexion",
    )

    class Meta:
        db_table = "session"
        verbose_name = "Session"
        verbose_name_plural = "Sessions"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"Session {self.id[:8]}… de {self.utilisateur_id}"

    @property
    def est_valide(self) -> bool:
        """Une session est valide si non révoquée et non expirée (horloge applicative)."""
        return self.revoque_le is None and self.expire_le > maintenant()


class Adresse(ModeleBase):
    """Adresse d'un utilisateur (repères + position GPS), privée par nature."""

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="adresses",
        help_text="Utilisateur propriétaire de l'adresse",
    )
    libelle = models.CharField(
        max_length=100,
        help_text="Libellé court : « Domicile », « Boutique »…",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Repères (les adresses postales sont rares au Cameroun)",
    )
    position = ChampPoint(
        help_text="Position en POINT SRID 4326",
    )
    ville = models.CharField(max_length=100)
    region = models.CharField(max_length=100)

    class Meta:
        db_table = "adresse"
        verbose_name = "Adresse"
        verbose_name_plural = "Adresses"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"{self.libelle} ({self.ville})"

"""Classes de permissions DRF réutilisables (prompt F-02 point 4).

- ``EstAuthentifieEtActif`` : compte actif ; suspendu → 403 ``compte_suspendu``.
- ``ARole(*roles)`` : réservé aux rôles donnés (utilisé comme fabrique de classe).
- ``AutoriseEnAttente`` : ingénieur en attente de validation autorisé, accès
  limité à ce que la vue permet (profil et justificatifs).

Conventions techniques §5 : propriété d'une ressource vérifiée dans le service,
ressource d'autrui → 404 (jamais 403 qui révélerait l'existence).
"""

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from commun.exceptions import ErreurMetier


def _erreur_statut(user: object) -> ErreurMetier:
    """Construit l'erreur 403 correspondant au statut non actif du compte."""
    statut = getattr(user, "statut_compte", None)
    if statut == "suspendu":
        return ErreurMetier(
            code="compte_suspendu",
            message="Votre compte est suspendu. Contactez l'administrateur d'AgriSens.",
            statut_http=status.HTTP_403_FORBIDDEN,
        )
    if statut == "en_attente_validation":
        return ErreurMetier(
            code="compte_en_attente",
            message="Votre compte est en attente de validation par un administrateur.",
            statut_http=status.HTTP_403_FORBIDDEN,
        )
    return ErreurMetier(
        code="compte_inactif",
        message="Votre compte n'est pas actif.",
        statut_http=status.HTTP_403_FORBIDDEN,
    )


def _utilisateur_connecte(request: Request) -> object | None:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


class EstAuthentifieEtActif(BasePermission):
    """L'utilisateur doit être authentifié avec un compte actif.

    Un compte suspendu ou en attente de validation renvoie 403 avec un code
    stable (``compte_suspendu`` / ``compte_en_attente``) plutôt que 401.
    """

    message = "Authentification requise."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = _utilisateur_connecte(request)
        if user is None:
            return False
        if getattr(user, "statut_compte", None) == "actif":
            return True
        raise _erreur_statut(user)


class AutoriseEnAttente(BasePermission):
    """Compte actif OU en attente de validation (ingénieur fraîchement inscrit).

    Utilisé sur les routes du profil personnel : l'ingénieur en attente garde
    l'accès à son profil et à ses justificatifs, rien d'autre.
    Un compte suspendu renvoie 403 ``compte_suspendu``.
    """

    message = "Authentification requise."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = _utilisateur_connecte(request)
        if user is None:
            return False
        if getattr(user, "statut_compte", None) in ("actif", "en_attente_validation"):
            return True
        raise _erreur_statut(user)


class AnonymeOuActif(BasePermission):
    """Ressource partiellement publique (photos du catalogue, F-05).

    Laisse passer les requêtes anonymes : le vérificateur métier par contexte
    tranche ensuite (404 silencieux si non visible). Un compte authentifié
    doit être actif ou en attente ; un compte suspendu reçoit 403.
    """

    message = "Authentification requise."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = _utilisateur_connecte(request)
        if user is None:
            return True
        if getattr(user, "statut_compte", None) in ("actif", "en_attente_validation"):
            return True
        raise _erreur_statut(user)


def ARole(*roles: str) -> type[BasePermission]:
    """Fabrique une classe de permission réservée aux rôles donnés.

    Usage : ``permission_classes = [ARole("agriculteur", "admin")]``
    Vérifie d'abord que le compte est actif, puis que le rôle est autorisé.
    """

    class _PermissionRole(EstAuthentifieEtActif):
        message = "Accès réservé aux rôles : " + ", ".join(roles)

        def has_permission(self, request: Request, view: APIView) -> bool:
            if not super().has_permission(request, view):
                return False
            user = _utilisateur_connecte(request)
            return getattr(user, "role", None) in roles

    _PermissionRole.__name__ = f"ARole_{'_'.join(roles)}"
    return _PermissionRole


# Alias conservé pour les appels internes déjà écrits.
a_role = ARole

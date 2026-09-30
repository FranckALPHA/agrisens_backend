"""Classes de permissions DRF réutilisables.

Conventions techniques §5 et prompt F-00 point 10.
"""

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


class EstAuthentifieEtActif(BasePermission):
    """Vérifie que l'utilisateur est authentifié et que son compte est actif."""

    message = "Compte non actif ou authentification requise."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user and user.is_authenticated and getattr(user, "statut_compte", None) == "actif"
        )


class ARole(BasePermission):
    """Vérifie que l'utilisateur a l'un des rôles spécifiés."""

    def __init__(self, *roles: str) -> None:
        self.roles = set(roles)

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated and getattr(user, "statut_compte", None) == "actif"):
            return False
        return getattr(user, "role", None) in self.roles


def a_role(*roles: str) -> type[BasePermission]:
    """Fabrique de classe de permission DRF paramétrable par rôle.

    Usage : permission_classes = [a_role("admin", "super_admin")]
    """

    class _PermissionRole(BasePermission):
        message = "Accès réservé aux rôles : " + ", ".join(roles)

        def has_permission(self, request: Request, view: APIView) -> bool:
            user = getattr(request, "user", None)
            if not (
                user and user.is_authenticated and getattr(user, "statut_compte", None) == "actif"
            ):
                return False
            return getattr(user, "role", None) in roles

    _PermissionRole.__name__ = f"ARole_{'_'.join(roles)}"
    return _PermissionRole

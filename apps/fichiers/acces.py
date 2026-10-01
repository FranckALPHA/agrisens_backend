"""Registre des règles d'accès aux fichiers par contexte (prompt F-03 point 1).

Chaque module métier déclare son vérificateur ici via
`inscrire_verificateur(contexte, verifie)` ; le module `fichiers` n'importe
jamais un autre module (pas d'import croisé). Sans vérificateur inscrit, la
règle par défaut est stricte : propriétaire et admins uniquement.

Signatures : `verifie(fichier, utilisateur) -> bool` — utilisateur peut être
None (requête anonyme). Retourner False équivaut à un 404 côté API (une
ressource privée d'un autre utilisateur ne doit pas révéler son existence).
"""

from collections.abc import Callable

from apps.fichiers.models import ContexteFichier, Fichier

VerificateurAcces = Callable[[Fichier, object | None], bool]

_ROLES_ADMIN = ("admin", "super_admin")


def _proprietaire_ou_admin(fichier: Fichier, utilisateur: object | None) -> bool:
    """Règle par défaut : propriétaire et admins uniquement."""
    if utilisateur is None:
        return False
    return (
        fichier.proprietaire_id == getattr(utilisateur, "id", None)
        or getattr(utilisateur, "role", None) in _ROLES_ADMIN
    )


def _photos_produit(fichier: Fichier, utilisateur: object | None) -> bool:
    """Photos de produits : publiques une fois publiées au catalogue (02 §2.8) ;
    avant publication, le propriétaire (qui prépare son produit) et les admins y
    accèdent — un upload récent doit être prévisualisable par son émetteur."""
    return bool(fichier.public) or _proprietaire_ou_admin(fichier, utilisateur)


def _justificatifs(fichier: Fichier, utilisateur: object | None) -> bool:
    """Justificatifs : propriétaire et admins (prompt F-03 point 1)."""
    return _proprietaire_ou_admin(fichier, utilisateur)


REGISTRE_ACCESS: dict[str, VerificateurAcces] = {
    ContexteFichier.PRODUIT: _photos_produit,
    ContexteFichier.JUSTIFICATIF: _justificatifs,
}


def inscrire_verificateur(contexte: str, verifie: VerificateurAcces) -> None:
    """Permet à un module de déclarer sa règle d'accès pour un contexte."""
    REGISTRE_ACCESS[contexte] = verifie


def acces_autorise(fichier: Fichier, utilisateur: object | None) -> bool:
    """Applique la règle du contexte du fichier (défaut : privé strict)."""
    verifie = REGISTRE_ACCESS.get(fichier.contexte, _proprietaire_ou_admin)
    return verifie(fichier, utilisateur)

"""Aide de test des permissions, réutilisable par toutes les features.

Prompt F-02 point 7 : pour une route donnée, vérifier automatiquement
- 401 sans jeton ;
- 403 pour chaque rôle non autorisé (compte actif) ;
- le succès pour chaque rôle autorisé.

Utiliser la fixture pytest `verifier_garde_route` (tests/conftest.py).
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from rest_framework.test import APIClient

# Tous les rôles du modèle Utilisateur (apps/utilisateurs/models.py).
TOUS_ROLES: tuple[str, ...] = (
    "agriculteur",
    "acheteur",
    "ingenieur_agronome",
    "livreur",
    "admin",
    "super_admin",
)


def _requete(
    client: APIClient,
    methode: str,
    url: str,
    donnees: dict[str, Any] | None,
) -> Any:
    if methode == "get":
        return client.get(url)
    if methode == "post":
        return client.post(url, donnees or {}, format="json")
    if methode == "patch":
        return client.patch(url, donnees or {}, format="json")
    if methode == "put":
        return client.put(url, donnees or {}, format="json")
    if methode == "delete":
        return client.delete(url)
    raise ValueError(f"Méthode HTTP non prise en charge : {methode}")


def verifier_garde_route(
    client_authentifie: Callable[..., tuple[APIClient, Any]],
    url: str,
    roles_autorises: Iterable[str],
    *,
    methode: str = "get",
    donnees: dict[str, Any] | None = None,
    statuts_succes: Sequence[int] = (200, 201, 202, 204),
    roles_testes: Sequence[str] = TOUS_ROLES,
) -> None:
    """Vérifie 401 sans jeton, 403 pour chaque rôle interdit, succès sinon.

    ``client_authentifie`` est la fixture qui crée un utilisateur actif du
    rôle demandé et retourne (client, utilisateur).
    """
    autorises = set(roles_autorises)

    # 1. Aucun jeton → 401 avec l'erreur normalisée.
    client_anonyme = APIClient()
    reponse = _requete(client_anonyme, methode, url, donnees)
    assert reponse.status_code == 401, (
        f"{methode} {url} sans jeton : attendu 401, obtenu {reponse.status_code}"
        f" ({reponse.content!r})"
    )

    # 2. Chaque rôle, actif, avec un jeton valide.
    for role in roles_testes:
        client, utilisateur = client_authentifie(role=role)
        reponse = _requete(client, methode, url, donnees)
        if role in autorises:
            assert reponse.status_code in statuts_succes, (
                f"{methode} {url} en tant que {role} ({utilisateur.id}) : "
                f"attendu un succès, obtenu {reponse.status_code} "
                f"({reponse.content!r})"
            )
        else:
            assert reponse.status_code == 403, (
                f"{methode} {url} en tant que {role} : attendu 403, obtenu "
                f"{reponse.status_code} ({reponse.content!r})"
            )

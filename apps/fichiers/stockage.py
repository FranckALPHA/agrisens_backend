"""Adaptateur de stockage des fichiers (prompt F-03 backend point 1).

Le module `fichiers` ne connaît que l'interface `StockageFichiers` ;
l'implémentation actuelle écrit sur le disque local sous
`settings.STOCKAGE_FICHIERS_CHEMIN`. Un adaptateur S3/objet pourra être
branché en production sans toucher au métier (journal, à confirmer en F-16).
"""

import secrets
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from django.conf import settings


class StockageFichiers(ABC):
    """Interface de stockage binaire ; les clés sont des chemins internes."""

    @abstractmethod
    def enregistrer(self, cle: str, contenu: bytes) -> None:
        """Écrit `contenu` sous la clé `cle` (écrase si existe)."""

    @abstractmethod
    def ouvrir(self, cle: str) -> BinaryIO:
        """Ouvre la clé en lecture binaire. Lève FileNotFoundError si absente."""

    @abstractmethod
    def supprimer(self, cle: str) -> None:
        """Supprime la clé (idempotent)."""

    @abstractmethod
    def existe(self, cle: str) -> bool:
        """Dit si la clé est présente dans le stockage."""


def generer_cle_stockage(extension: str, instant: datetime) -> str:
    """Nom de fichier aléatoire + sous-dossier année/mois (jamais le nom client)."""
    # Le nom du client n'entre jamais dans le chemin : uniquement l'extension
    # d'un type déjà validé côté service, sanitisée.
    extension = "".join(c for c in extension if c.isalnum())[:8]
    return f"{instant.year}/{instant.month:02d}/{secrets.token_urlsafe(24)}.{extension}"


class StockageDisque(StockageFichiers):
    """Implémentation locale : `STOCKAGE_FICHIERS_CHEMIN/<cle>`."""

    def __init__(self, racine: Path | None = None) -> None:
        self.racine = Path(racine if racine is not None else settings.STOCKAGE_FICHIERS_CHEMIN)

    def _chemin(self, cle: str) -> Path:
        chemin = (self.racine / cle).resolve()
        # Anti path-traversal : la clé doit rester sous la racine du stockage.
        if not chemin.is_relative_to(self.racine.resolve()):
            raise FileNotFoundError(f"Clé de stockage hors racine : {cle!r}")
        return chemin

    def enregistrer(self, cle: str, contenu: bytes) -> None:
        chemin = self._chemin(cle)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(contenu)

    def ouvrir(self, cle: str) -> BinaryIO:
        return self._chemin(cle).open("rb")

    def supprimer(self, cle: str) -> None:
        chemin = self._chemin(cle)
        if chemin.exists():
            chemin.unlink()

    def existe(self, cle: str) -> bool:
        return self._chemin(cle).exists()

"""Tâche planifiée des paiements : vérification des non-confirmés (secours webhook).

Exécutée périodiquement par le worker (`manage.py worker`) et à la demande
en test via `POST /dev/taches/verifier_paiements`.
"""

from apps.administration.services import parametres
from apps.paiements.services import ServicePaiement


def verifier_paiements() -> int:
    """Vérifie les paiements `en_attente` trop anciens via la passerelle."""
    delai = int(parametres.obtenir("paiement.verification_apres_min"))
    return ServicePaiement.controler_en_attente(delai)

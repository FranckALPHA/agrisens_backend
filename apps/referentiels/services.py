"""Logique métier pour l'accès aux référentiels."""

from commun.exceptions import NonTrouve

from apps.referentiels.donnees import REFERENTIELS_INITIAUX
from apps.referentiels.models import Referentiel


def lister_par_type(type_ref: str) -> list[Referentiel]:
    """Retourne la liste ordonnée des entrées actives pour un type de référentiel."""
    if type_ref not in REFERENTIELS_INITIAUX:
        raise NonTrouve(
            code="referentiel_inconnu",
            message=f"Type de référentiel inconnu : '{type_ref}'.",
            details={"type": type_ref},
        )
    return list(Referentiel.objects.filter(type=type_ref, actif=True).order_by("ordre", "libelle"))

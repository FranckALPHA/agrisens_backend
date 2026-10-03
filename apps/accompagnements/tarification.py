"""Tarification pure de l'accompagnement (journal P-06 et Q-06).

Fonctions sans accès base ni paramètres : tarif au kilomètre, minimum,
plafond, taux de commission et tarif de consultation sont injectés par le
service (clé `parametres`) — aucun montant n'est en dur ici. Testées avec
l'exemple chiffré du journal §4 et de `docs/07` §15.6 :
12 km aller → 24 km aller-retour × 200 FCFA = 4 800 FCFA.
"""

from decimal import ROUND_HALF_UP, Decimal

from commun.geo import km_superieurs


def arrondir_fcfa(montant: Decimal) -> int:
    """Entier FCFA le plus proche, demi vers le haut (conventions §2)."""
    return int(montant.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def distance_aller_km(distance_exacte: float) -> int:
    """Kilomètres aller simple arrondis au km supérieur (conventions §3)."""
    return km_superieurs(distance_exacte)


def frais_deplacement(
    distance_exacte: float,
    *,
    tarif_km_fcfa: int,
    minimum_fcfa: int,
    plafond_fcfa: int,
) -> tuple[int, int]:
    """Frais de déplacement présentiel (P-06, 07 §15.6).

    Allant-retour : km aller arrondi au km supérieur, doublé, multiplié par le
    tarif au kilomètre, puis borné par le minimum et le plafond. Renvoie
    `(km_aller, frais_fcfa)` — la distance stockée est l'aller simple (02 §2.5).
    """
    km_aller = distance_aller_km(distance_exacte)
    brut = (km_aller * 2) * tarif_km_fcfa
    return km_aller, min(max(brut, minimum_fcfa), plafond_fcfa)


def commission_consultation(
    prix_consultation_fcfa: int,
    *,
    taux_pb: int,
) -> int:
    """Commission en points de base, appliquée sur la consultation seule (Q-06).

    Le déplacement n'est jamais commissionné : l'ingénieur le reçoit en entier.
    """
    return arrondir_fcfa(Decimal(prix_consultation_fcfa) * Decimal(taux_pb) / Decimal(10_000))

"""Tarification pure des commandes (SPEC-04, journal P-02 et §4).

Fonctions sans accès base ni paramètres : les valeurs (base, tarif au km,
minimum, plafond) sont injectées par l'appelant (service `parametres` côté
service). Testées avec tous les exemples chiffrés du journal §4.
"""

from decimal import ROUND_HALF_UP, Decimal


def arrondir_fcfa(montant: Decimal) -> int:
    """Entier FCFA le plus proche, demi vers le haut (conventions §2)."""
    return int(montant.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def total_ligne(quantite: Decimal, prix_unitaire_fcfa: int) -> int:
    """Total d'une ligne en FCFA entiers (# Hypothèse Q-40)."""
    return arrondir_fcfa(quantite * Decimal(prix_unitaire_fcfa))


def kilometres_factures(distance_km_exacte: float) -> int:
    """Arrondi au km supérieur avant tout calcul tarifaire (conventions §3)."""
    from commun.geo import km_superieurs

    return km_superieurs(distance_km_exacte)


def frais_livraison(
    distance_km_exacte: float,
    *,
    base_fcfa: int,
    tarif_km_fcfa: int,
    minimum_fcfa: int,
    plafond_fcfa: int,
) -> tuple[int, int]:
    """Frais de livraison (P-02) : base + km × tarif, plancher et plafond.

    Renvoie (km_factures, frais_fcfa). Aucun contrôle de zone ici (le service
    refuse au-delà de `livraison.distance_max_km` avec `hors_zone_livraison`).
    """
    km = kilometres_factures(distance_km_exacte)
    frais = base_fcfa + km * tarif_km_fcfa
    return km, min(max(frais, minimum_fcfa), plafond_fcfa)

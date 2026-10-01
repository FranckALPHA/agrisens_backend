"""Registre en code des clés de PARAMETRE_PLATEFORME.

Source : [[04-journal-decisions]] §4 (valeurs de démarrage et bornes).
Aucun tarif, taux, quota ou délai n'est en dur ailleurs : tout passe ici pour
la validation des bornes et le filtrage des clés publiques.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DefinitionParametre:
    cle: str
    demarrage: int
    minimum: int
    maximum: int
    public: bool  # exposé par GET /parametres/publics


_DEFINITIONS = [
    DefinitionParametre("commission.taux_pb", 500, 0, 2_000, False),
    DefinitionParametre("commission.consultation_taux_pb", 500, 0, 2_000, False),
    DefinitionParametre("livraison.base_fcfa", 500, 0, 5_000, True),
    DefinitionParametre("livraison.tarif_km_fcfa", 150, 0, 2_000, True),
    DefinitionParametre("livraison.minimum_fcfa", 500, 0, 5_000, True),
    DefinitionParametre("livraison.plafond_fcfa", 5_000, 500, 50_000, True),
    DefinitionParametre("livraison.distance_max_km", 50, 1, 500, True),
    DefinitionParametre("livraison.tentatives_code_max", 5, 3, 10, False),
    DefinitionParametre("livraison.reprogrammations_max", 2, 0, 5, False),
    DefinitionParametre("livraison.vitesse_moyenne_kmh", 25, 5, 80, False),
    DefinitionParametre("commande.delai_acceptation_h", 24, 1, 168, False),
    DefinitionParametre("commande.delai_paiement_h", 24, 1, 168, False),
    DefinitionParametre("remboursement.delai_demande_h", 48, 24, 720, False),
    DefinitionParametre("deplacement.tarif_km_fcfa", 200, 0, 2_000, True),
    DefinitionParametre("deplacement.minimum_fcfa", 1_000, 0, 10_000, True),
    DefinitionParametre("deplacement.plafond_fcfa", 10_000, 1_000, 100_000, True),
    DefinitionParametre("deplacement.distance_max_km", 100, 1, 500, True),
    DefinitionParametre("consultation.prix_min_fcfa", 2_000, 0, 100_000, True),
    DefinitionParametre("consultation.prix_max_fcfa", 50_000, 0, 500_000, True),
    # Hypothèse Q-34 : borne de la zone d'intervention des ingénieurs (02 ne la fixe pas)
    DefinitionParametre("consultation.zone_rayon_km_max", 150, 1, 500, True),
    DefinitionParametre("fichiers.image_max_mo", 5, 1, 20, True),
    DefinitionParametre("fichiers.document_max_mo", 10, 1, 50, True),
    DefinitionParametre("fichiers.photos_produit_max", 5, 1, 20, True),
    # Hypothèse Q-35 : durée de vie des URL signées de téléchargement
    DefinitionParametre("fichiers.duree_url_signee_s", 300, 30, 3_600, True),
    DefinitionParametre("ia.delai_max_s", 60, 10, 300, False),
    DefinitionParametre("paiement.verification_apres_min", 15, 5, 120, False),
]

REGISTRE: dict[str, DefinitionParametre] = {d.cle: d for d in _DEFINITIONS}


def cle_connue(cle: str) -> DefinitionParametre | None:
    """Vérifie si une clé appartient au registre."""
    return REGISTRE.get(cle)


def cles_publiques() -> list[str]:
    """Retourne la liste des clés de paramètres publiques."""
    return [d.cle for d in _DEFINITIONS if d.public]


def valeurs_de_demarrage() -> dict[str, int]:
    """Retourne l'ensemble des clés et leurs valeurs de démarrage initiales."""
    return {d.cle: d.demarrage for d in _DEFINITIONS}

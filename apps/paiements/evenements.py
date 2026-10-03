"""Événements publiés par les paiements (bus `commun/evenements.py`).

F-08 (livraisons) et F-11 (accompagnement) s'y abonneront ; en F-07, les
tests s'y abonnent pour vérifier la publication.
"""

from dataclasses import dataclass

from commun.evenements import Evenement


@dataclass
class PaiementReussi(Evenement):
    paiement_id: str = ""
    commande_id: str = ""
    accompagnement_id: str = ""
    montant_fcfa: int = 0

"""Tests unitaires F-09 : logique pure des litiges (sans base de données).

Machine à états du remboursement (02 §3.5), motifs à justificatif
obligatoire (07 §15.3) et statuts déclencheurs du délai de demande.
"""

from apps.commandes.models import StatutCommande
from apps.litiges.models import (
    MOTIFS_JUSTIFICATIF_OBLIGATOIRE,
    STATUTS_ACTIFS_REMBOURSEMENT,
    MotifRemboursement,
    StatutRemboursement,
)
from apps.litiges.services import STATUTS_DECLENCHEURS, TRANSITIONS_REMBOURSEMENT


class TestMachineRemboursement:
    def test_demande_passe_seulement_en_examen(self) -> None:
        assert TRANSITIONS_REMBOURSEMENT[StatutRemboursement.DEMANDE] == frozenset(
            {StatutRemboursement.EN_EXAMEN}
        )

    def test_examen_mene_a_approbation_ou_refus(self) -> None:
        assert TRANSITIONS_REMBOURSEMENT[StatutRemboursement.EN_EXAMEN] == frozenset(
            {StatutRemboursement.APPROUVE, StatutRemboursement.REFUSE}
        )

    def test_approuve_mene_a_effectue_executeur_manuel_q26(self) -> None:
        # Q-26 : pas de `en_cours` automatique, l'admin enregistre la référence.
        assert TRANSITIONS_REMBOURSEMENT[StatutRemboursement.APPROUVE] == frozenset(
            {StatutRemboursement.EFFECTUE}
        )

    def test_refuse_et_effectue_sont_finis(self) -> None:
        assert StatutRemboursement.REFUSE not in TRANSITIONS_REMBOURSEMENT
        assert StatutRemboursement.EFFECTUE not in TRANSITIONS_REMBOURSEMENT

    def test_statuts_actifs_bloquent_la_double_demande(self) -> None:
        assert (
            frozenset(
                {
                    StatutRemboursement.DEMANDE,
                    StatutRemboursement.EN_EXAMEN,
                    StatutRemboursement.APPROUVE,
                    StatutRemboursement.EN_COURS,
                }
            )
            == STATUTS_ACTIFS_REMBOURSEMENT
        )
        assert StatutRemboursement.REFUSE not in STATUTS_ACTIFS_REMBOURSEMENT
        assert StatutRemboursement.EFFECTUE not in STATUTS_ACTIFS_REMBOURSEMENT


class TestJustificatifsObligatoires:
    def test_motifs_d_avec_photo(self) -> None:
        # 07 §15.3 : produit endommagé/incorrect/non reçu → justificatif requis.
        assert (
            frozenset(
                {
                    MotifRemboursement.PRODUIT_ENDOMMAGE,
                    MotifRemboursement.PRODUIT_INCORRECT,
                    MotifRemboursement.NON_RECUS,
                }
            )
            == MOTIFS_JUSTIFICATIF_OBLIGATOIRE
        )

    def test_annulation_et_autre_sans_photo_imposee(self) -> None:
        assert MotifRemboursement.ANNULATION not in MOTIFS_JUSTIFICATIF_OBLIGATOIRE
        assert MotifRemboursement.AUTRE not in MOTIFS_JUSTIFICATIF_OBLIGATOIRE


class TestDelaiDemande:
    def test_declencheurs_sont_livraison_et_incidents(self) -> None:
        assert STATUTS_DECLENCHEURS == (
            StatutCommande.LIVREE,
            StatutCommande.CLIENT_ABSENT,
            StatutCommande.ECHEC_LIVRAISON,
        )

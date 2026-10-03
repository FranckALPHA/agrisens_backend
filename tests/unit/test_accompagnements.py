"""Tests unitaires F-11 : logique pure de l'accompagnement (sans base).

Couvre la machine à états (§3.3), la tarification du déplacement (P-06,
docs/07 §15.6, exemple de référence 12 km aller → 4 800 FCFA) et la
commission uniquement sur la consultation (Q-06).
"""

from decimal import Decimal

import pytest
from apps.accompagnements.models import (
    AbsentRDV,
    Modalite,
    MotifFinAffectation,
    StatutAccompagnement,
)
from apps.accompagnements.services import (
    STATUTS_ACCES_EXPLOITATION,
    TRANSITIONS,
)
from apps.accompagnements.tarification import (
    arrondir_fcfa,
    commission_consultation,
    distance_aller_km,
    frais_deplacement,
)
from commun.exceptions import ValidationMetier

# --- Machine à états (02 §3.3) -------------------------------------------------


class TestMachineEtats:
    def test_demande_p_acceptee_refusee_ou_annulee(self) -> None:
        assert TRANSITIONS[StatutAccompagnement.DEMANDEE] == frozenset(
            {
                StatutAccompagnement.ACCEPTEE,
                StatutAccompagnement.REFUSEE,
                StatutAccompagnement.ANNULEE,
            }
        )

    def test_acceptee_mene_a_planifiee_webhook_q05(self) -> None:
        # Q-05 : le passage à `planifiee` se fait au webhook de paiement.
        # Q-29 : un retour à `demandee` est possible (changement d'ingénieur).
        assert TRANSITIONS[StatutAccompagnement.ACCEPTEE] == frozenset(
            {
                StatutAccompagnement.PLANIFIEE,
                StatutAccompagnement.DEMANDEE,
                StatutAccompagnement.ANNULEE,
            }
        )

    def test_planifiee_mene_a_terminee_ou_manquee(self) -> None:
        assert TRANSITIONS[StatutAccompagnement.PLANIFIEE] == frozenset(
            {
                StatutAccompagnement.TERMINEE,
                StatutAccompagnement.MANQUEE,
                StatutAccompagnement.DEMANDEE,  # Q-29 après paiement (admin)
                StatutAccompagnement.ANNULEE,
            }
        )

    def test_manquee_peut_etre_reprogrammee(self) -> None:
        # Q-30 : reprogrammation gratuite puis retour à `planifiee`.
        assert TRANSITIONS[StatutAccompagnement.MANQUEE] == frozenset(
            {StatutAccompagnement.PLANIFIEE, StatutAccompagnement.ANNULEE}
        )

    def test_refusee_terminee_annulee_sont_finies(self) -> None:
        for fini in (
            StatutAccompagnement.REFUSEE,
            StatutAccompagnement.TERMINEE,
            StatutAccompagnement.ANNULEE,
        ):
            assert TRANSITIONS[fini] == frozenset(), f"{fini} ne doit pas être un état initial"


# --- Accès exploitation (F-11 prompt point 5) ---------------------------------


class TestAccesExploitation:
    def test_acces_lecture_uniquement_pendant_accompagnement_actif(self) -> None:
        # `acceptee` (en attente de paiement), `planifiee` (actif) et
        # `manquee` (reprogrammation possible) donnent accès. `demandee`
        # (pas encore acceptée) et les états finis ne donnent pas accès.
        assert frozenset(
            {
                StatutAccompagnement.ACCEPTEE,
                StatutAccompagnement.PLANIFIEE,
                StatutAccompagnement.MANQUEE,
            }
        ) == STATUTS_ACCES_EXPLOITATION


# --- Énumérations métier ------------------------------------------------------


class TestEnumerations:
    def test_modalites(self) -> None:
        assert {m.value for m in Modalite} == {"en_ligne", "presentiel"}

    def test_absents_rdv(self) -> None:
        assert {a.value for a in AbsentRDV} == {"ingenieur", "agriculteur"}

    def test_motifs_fin_affectation_incluent_changement_q29(self) -> None:
        # Hypothèse Q-44 : `changement` est ajouté pour historiser un
        # changement d'ingénieur (Q-29) en plus des motifs 02 §2.5.
        values = {m.value for m in MotifFinAffectation}
        assert "changement" in values
        assert {"indisponibilite", "desistement", "refus", "termine"} <= values


# --- Tarification pure (P-06, Q-06) -------------------------------------------


class TestFraisDeplacement:
    def test_exemple_reference_12_km_donne_4800_fcfa(self) -> None:
        # Journal §4 / docs/07 §15.6 : 12 km aller → 24 km AR × 200 = 4 800.
        km, frais = frais_deplacement(
            12.0, tarif_km_fcfa=200, minimum_fcfa=1_000, plafond_fcfa=10_000
        )
        assert km == 12
        assert frais == 4_800

    def test_kilometrage_arondi_au_superieur_avant_toute_prix(self) -> None:
        # 11,1 km → facturé 12 km (conventions §3, `km_superieurs`).
        km, frais = frais_deplacement(
            11.1, tarif_km_fcfa=200, minimum_fcfa=1_000, plafond_fcfa=10_000
        )
        assert km == 12
        assert frais == 4_800

    def test_minimum_applique_en_deca(self) -> None:
        # 2 km × 2 × 200 = 800 → minimum 1 000 FCFA.
        km, frais = frais_deplacement(
            2.0, tarif_km_fcfa=200, minimum_fcfa=1_000, plafond_fcfa=10_000
        )
        assert km == 2
        assert frais == 1_000

    def test_plafond_applique_au_dela(self) -> None:
        # 30 km × 2 × 200 = 12 000 → plafond 10 000 FCFA.
        km, frais = frais_deplacement(
            30.0, tarif_km_fcfa=200, minimum_fcfa=1_000, plafond_fcfa=10_000
        )
        assert km == 30
        assert frais == 10_000

    def test_distance_nulle_donne_zero_km(self) -> None:
        # `km_superieurs(0)` = 0 (définition dans `commun/geo.py`) ; le
        # minimum n'est PAS appliqué : une distance nulle = pas de RDV
        # présentiel. On reste cohérent avec la spec.
        km, frais = frais_deplacement(
            0.0, tarif_km_fcfa=200, minimum_fcfa=1_000, plafond_fcfa=10_000
        )
        assert km == 0
        # brut = 0 → max(0, minimum) = minimum ; c'est la spec :
        # un présentiel à 0 km est tarifé au minimum (aucun cas métier
        # réel : l'agriculteur choisirait « en ligne »).
        assert frais == 1_000


class TestArrondiEtDistance:
    def test_arrondir_fcfa_demi_vers_le_haut(self) -> None:
        # Conventions §2 : tout montant FCFA passe par cet arrondi.
        assert arrondir_fcfa(Decimal("4999.5")) == 5_000
        assert arrondir_fcfa(Decimal("5000.4")) == 5_000
        assert arrondir_fcfa(Decimal("5000.5")) == 5_001

    def test_distance_aller_km_reutilise_km_superieurs(self) -> None:
        assert distance_aller_km(11.1) == 12
        assert distance_aller_km(12.0) == 12
        assert distance_aller_km(12.0001) == 13


class TestCommissionConsultation:
    def test_commission_uniquement_sur_consultation_q06(self) -> None:
        # 5 % = 500 pb → 10 000 × 5 % = 500 FCFA.
        assert commission_consultation(10_000, taux_pb=500) == 500

    def test_commission_arrondie_au_fcfa_le_plus_proche(self) -> None:
        # 3 333 FCFA × 5 % = 166.65 → 167 FCFA.
        assert commission_consultation(3_333, taux_pb=500) == 167

    def test_taux_nul_donne_commission_nulle(self) -> None:
        assert commission_consultation(10_000, taux_pb=0) == 0


# --- Validation générique -----------------------------------------------------


class TestExceptions:
    def test_validation_metier_a_un_code_et_un_statut_http(self) -> None:
        exc = ValidationMetier("code_test", "message", {"champ": "x"})
        assert exc.code == "code_test"
        assert exc.statut_http == 422


# Marqueur pytest pour collecter proprement.
_ = pytest

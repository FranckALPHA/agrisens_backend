"""Tests unitaires F-12 : génération IA (SPEC-08).

Partie « logique pure » (sans base) : prompts versionnés, schémas Pydantic,
construction du payload anti-données-personnelles, arrondi du centre, date de
réinitialisation du quota. Partie service (django_db) : quota, génération via
le fournisseur factice (réussite / échec / réponse invalide / idempotence) et
correction sans mutation de la réponse originale.

Aucun réseau : le fournisseur actif est `factice` (garanti par
config/settings/test.py)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from apps.plannings import prompts
from apps.plannings.fournisseurs import FournisseurFactice
from apps.plannings.models import GenerationIA, StatutGeneration, TypeGeneration
from apps.plannings.schemas import (
    PlanningResult,
    RecommandationResult,
    schema_pour,
)
from apps.plannings.services import (
    CLE_PLANNING,
    CLE_RECOMMANDATION,
    ServiceCorrection,
    ServiceEntrees,
    ServiceGeneration,
    ServiceQuota,
    _premiere_prochain_mois,
)
from commun.exceptions import QuotaAtteint, ValidationMetier
from pydantic import ValidationError
from shapely.geometry import Point

# --- Logique pure (sans base) ------------------------------------------------


def test_prompts_versionnes() -> None:
    assert prompts.PROMPT_PLANNING_V1 == "1.0"
    assert prompts.PROMPT_RECOMMANDATION_V1 == "1.0"
    assert "recommandation" in prompts.SystemePour("planning")
    assert "diagnostic" in prompts.SystemePour("recommandation")
    with pytest.raises(ValueError):
        prompts.SystemePour("inconnu")


def test_schema_planning_valide() -> None:
    resultat = PlanningResult.model_validate(
        {
            "culture": "tomate",
            "resume": "r",
            "avertissement": "a",
            "etapes": [
                {
                    "date_debut": "2026-01-01",
                    "date_fin": "2026-02-01",
                    "activite": "Semis",
                    "details": "d",
                    "intrants": ["semences"],
                }
            ],
            "risques": [],
            "conseils": [],
        }
    )
    assert resultat.etapes[0].activite == "Semis"


def test_schema_planning_invalide_sans_etapes() -> None:
    with pytest.raises(ValidationError):
        PlanningResult.model_validate(
            {"culture": "tomate", "avertissement": "a", "etapes": []}
        )


def test_schema_recommandation_priorites() -> None:
    ok = RecommandationResult.model_validate(
        {
            "sujet": "fertilisation",
            "avertissement": "a",
            "recommandations": [
                {"titre": "t", "detail": "d", "priorite": "haute"}
            ],
        }
    )
    assert ok.recommandations[0].priorite == "haute"
    with pytest.raises(ValidationError):
        RecommandationResult.model_validate(
            {
                "sujet": "x",
                "avertissement": "a",
                "recommandations": [
                    {"titre": "t", "detail": "d", "priorite": "critique"}
                ],
            }
        )


def _exploitation_nu() -> Any:
    from apps.exploitations.models import Exploitation

    return Exploitation(
        type_sol="argileux",
        region="centre",
        superficie_ha=Decimal("1.5000"),
        # (lon, lat) = Yaoundé, volontairement à plus d'une décimale.
        centre=Point(11.5023, 3.8487),
    )


def test_entrees_planning_aucune_donnee_personnelle() -> None:
    entrees = ServiceEntrees.construire(
        _exploitation_nu(),
        TypeGeneration.PLANNING,
        {
            "culture": "tomate",
            "periode_debut": date(2026, 1, 1),
            "periode_fin": date(2026, 4, 1),
            "infos_agronomiques": "sol acide",
        },
    )
    assert set(entrees.keys()) == CLE_PLANNING
    for interdite in ("nom", "telephone", "email", "adresse", "utilisateur", "demandeur"):
        assert interdite not in entrees
    assert entrees["periode"] == {"debut": "2026-01-01", "fin": "2026-04-01"}
    assert entrees["superficie_m2"] == 15000


def test_entrees_arrondi_centre_au_dixieme() -> None:
    entrees = ServiceEntrees.construire(
        _exploitation_nu(),
        TypeGeneration.PLANNING,
        {"culture": "maïs", "periode_debut": date(2026, 1, 1), "periode_fin": date(2026, 2, 1)},
    )
    assert entrees["centre"] == {"lat": 3.8, "lon": 11.5}


def test_entrees_recommandation_cles() -> None:
    entrees = ServiceEntrees.construire(
        _exploitation_nu(),
        TypeGeneration.RECOMMANDATION,
        {"sujet": "jaunissement", "culture": "tomate", "contexte": "depuis 15 jours"},
    )
    assert set(entrees.keys()) == CLE_RECOMMANDATION


def test_entrees_donnees_manquantes() -> None:
    with pytest.raises(ValidationMetier) as exc:
        ServiceEntrees.construire(
            _exploitation_nu(), TypeGeneration.PLANNING, {"periode_debut": date(2026, 1, 1)}
        )
    assert exc.value.code == "donnees_manquantes"


def test_reinitialisation_mois_suivant() -> None:
    assert _premiere_prochain_mois("2026-10") == "2026-11-01"
    assert _premiere_prochain_mois("2026-12") == "2027-01-01"


def test_fournisseur_factice_normal_conforme() -> None:
    factice = FournisseurFactice()
    resultat = factice.generer(
        systeme="s",
        utilisateur=(
            '{"culture": "tomate", '
            '"periode": {"debut": "2026-01-01", "fin": "2026-06-01"}}'
        ),
        schema=schema_pour("planning"),
        delai_s=5,
        mode="normal",
    )
    from apps.plannings.fournisseurs import ReponseIA

    assert isinstance(resultat, ReponseIA)
    PlanningResult.model_validate(resultat.contenu)


def test_fournisseur_factice_invalide_et_echec() -> None:
    from apps.plannings.fournisseurs import ErreurIA, ReponseIA

    factice = FournisseurFactice()
    invalide = factice.generer(
        systeme="s", utilisateur="{}", schema=schema_pour("planning"), delai_s=5, mode="invalide"
    )
    assert isinstance(invalide, ReponseIA)
    with pytest.raises(ValidationError):
        PlanningResult.model_validate(invalide.contenu)
    echec = factice.generer(
        systeme="s", utilisateur="{}", schema=schema_pour("planning"), delai_s=5, mode="echec"
    )
    assert isinstance(echec, ErreurIA)
    assert echec.code == "erreur_fournisseur"


# --- Services (base de données) ----------------------------------------------


@pytest.mark.django_db
def test_quota_vue_et_consomme(fabrique_utilisateur: Any) -> None:
    agri = fabrique_utilisateur(role="agriculteur")
    vue = ServiceQuota.vue_quota(agri)
    assert vue["planning"]["limite"] == 2
    assert vue["recommandation"]["limite"] == 5
    assert vue["planning"]["consomme"] == 0
    assert set(vue.keys()) == {"mois", "date_reinitialisation", "planning", "recommandation"}


@pytest.mark.django_db
def test_generer_planning_reussie(fabrique_utilisateur: Any) -> None:
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    exploitation = creer_exploitation(
        agri, nom="Champ T", parcelle=polygone_yaounde(), type_sol="limoneux", region="centre"
    )
    generation = ServiceGeneration.generer(
        agri,
        exploitation,
        type_generation=TypeGeneration.PLANNING,
        donnees={
            "culture": "tomate",
            "periode_debut": date(2026, 1, 1),
            "periode_fin": date(2026, 5, 1),
        },
    )
    assert generation.statut == StatutGeneration.REUSSIE
    assert generation.reponse_originale is not None
    assert generation.version_prompt == "1.0"
    assert generation.fournisseur_ia == "factice"
    assert generation.modele_ia == "factice-normal"
    assert ServiceQuota.consomme(generation.mois_quota, agri, TypeGeneration.PLANNING) == 1


@pytest.mark.django_db
def test_echec_ne_decompte_pas_le_quota(fabrique_utilisateur: Any) -> None:
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    exploitation = creer_exploitation(
        agri, nom="Champ E", parcelle=polygone_yaounde(), type_sol="sableux", region="centre"
    )
    for _ in range(3):
        generation = ServiceGeneration.generer(
            agri,
            exploitation,
            type_generation=TypeGeneration.PLANNING,
            donnees={
                "culture": "maïs",
                "periode_debut": date(2026, 1, 1),
                "periode_fin": date(2026, 3, 1),
            },
            mode="echec",
        )
        assert generation.statut == StatutGeneration.ECHOUEE
        assert "erreur_fournisseur" in (generation.erreur or "")
    mois = generation.mois_quota
    assert ServiceQuota.consomme(mois, agri, TypeGeneration.PLANNING) == 0
    # Le quota n'étant pas entamé, une génération réussie passe encore.
    ok = ServiceGeneration.generer(
        agri,
        exploitation,
        type_generation=TypeGeneration.PLANNING,
        donnees={
            "culture": "maïs",
            "periode_debut": date(2026, 1, 1),
            "periode_fin": date(2026, 3, 1),
        },
    )
    assert ok.statut == StatutGeneration.REUSSIE


@pytest.mark.django_db
def test_reponse_invalide_marquee_echouee(fabrique_utilisateur: Any) -> None:
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    exploitation = creer_exploitation(
        agri, nom="Champ I", parcelle=polygone_yaounde(), type_sol="lateritique", region="centre"
    )
    generation = ServiceGeneration.generer(
        agri,
        exploitation,
        type_generation=TypeGeneration.PLANNING,
        donnees={
            "culture": "manioc",
            "periode_debut": date(2026, 1, 1),
            "periode_fin": date(2026, 8, 1),
        },
        mode="invalide",
    )
    assert generation.statut == StatutGeneration.ECHOUEE
    assert "reponse_invalide" in (generation.erreur or "")
    assert generation.reponse_originale is None


@pytest.mark.django_db
def test_depassement_quota_leve_429(fabrique_utilisateur: Any) -> None:
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    exploitation = creer_exploitation(
        agri, nom="Champ Q", parcelle=polygone_yaounde(), type_sol="argileux", region="centre"
    )
    donnees = {
        "culture": "tomate",
        "periode_debut": date(2026, 1, 1),
        "periode_fin": date(2026, 4, 1),
    }
    ServiceGeneration.generer(agri, exploitation, type_generation="planning", donnees=donnees)
    ServiceGeneration.generer(agri, exploitation, type_generation="planning", donnees=donnees)
    with pytest.raises(QuotaAtteint) as exc:
        ServiceGeneration.generer(agri, exploitation, type_generation="planning", donnees=donnees)
    assert exc.value.statut_http == 429
    assert exc.value.details["limite"] == 2
    assert exc.value.details["consomme"] == 2
    assert "date_reinitialisation" in exc.value.details


@pytest.mark.django_db
def test_idempotence_retourne_meme_objet(fabrique_utilisateur: Any) -> None:
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    exploitation = creer_exploitation(
        agri,
        nom="Champ Id",
        parcelle=polygone_yaounde(),
        type_sol="humifere",
        region="extreme_nord",
    )
    donnees = {
        "culture": "arachide",
        "periode_debut": date(2026, 5, 1),
        "periode_fin": date(2026, 9, 1),
    }
    g1 = ServiceGeneration.generer(
        agri, exploitation, type_generation="planning", donnees=donnees, cle_idempotence="CLE-1"
    )
    g2 = ServiceGeneration.generer(
        agri, exploitation, type_generation="planning", donnees=donnees, cle_idempotence="CLE-1"
    )
    assert g1.id == g2.id
    assert GenerationIA.objects.filter(cle_idempotence="CLE-1").count() == 1
    # Idempotence : aucune double-consommation du quota.
    assert ServiceQuota.consomme(g1.mois_quota, agri, TypeGeneration.PLANNING) == 1


@pytest.mark.django_db
def test_correction_sans_muter_reponse_originale(fabrique_utilisateur: Any) -> None:
    from apps.accompagnements.models import Accompagnement, Modalite, StatutAccompagnement
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    ing = fabrique_utilisateur(role="ingenieur_agronome")
    exploitation = creer_exploitation(
        agri, nom="Champ C", parcelle=polygone_yaounde(), type_sol="argileux", region="centre"
    )
    Accompagnement.objects.create(
        agriculteur=agri,
        exploitation=exploitation,
        ingenieur=ing,
        modalite=Modalite.EN_LIGNE,
        motif="Vérification planning",
        statut=StatutAccompagnement.ACCEPTEE,
    )
    generation = ServiceGeneration.generer(
        agri,
        exploitation,
        type_generation="planning",
        donnees={
            "culture": "tomate",
            "periode_debut": date(2026, 1, 1),
            "periode_fin": date(2026, 4, 1),
        },
    )
    originale = generation.reponse_originale
    correction = ServiceCorrection.creer(
        ing, generation.id, commentaire="À valider", contenu_corrige={"note": "ok"}
    )
    generation.refresh_from_db()
    assert generation.reponse_originale == originale
    assert correction.contenu_corrige == {"note": "ok"}


@pytest.mark.django_db
def test_ing_non_affecte_ne_peut_corriger(fabrique_utilisateur: Any) -> None:
    from apps.exploitations.services import creer as creer_exploitation

    from tests.unit.test_exploitations import polygone_yaounde

    agri = fabrique_utilisateur(role="agriculteur")
    ing_seul = fabrique_utilisateur(role="ingenieur_agronome")
    exploitation = creer_exploitation(
        agri, nom="Champ N", parcelle=polygone_yaounde(), type_sol="argileux", region="centre"
    )
    generation = ServiceGeneration.generer(
        agri,
        exploitation,
        type_generation="planning",
        donnees={
            "culture": "tomate",
            "periode_debut": date(2026, 1, 1),
            "periode_fin": date(2026, 4, 1),
        },
    )
    from commun.exceptions import NonTrouve

    with pytest.raises(NonTrouve):
        ServiceCorrection.creer(ing_seul, generation.id, commentaire="x")

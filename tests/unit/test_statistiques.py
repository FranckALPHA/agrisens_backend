"""Tests unitaires du service des statistiques (SPEC-11, F-14).

Vérifient l'agrégation exacte sur un jeu de données construit, la séparation
stricte des données entre agriculteurs, les filtres de période, la série
temporelle multi-granularités et les agrégats livreur / ingénieur / plateforme.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from apps.accompagnements.models import Accompagnement, Modalite, StatutAccompagnement
from apps.commandes.models import Commande, LigneCommande, StatutCommande
from apps.exploitations.models import Exploitation
from apps.litiges.models import Remboursement, StatutRemboursement
from apps.livraisons.models import Livraison, StatutLivraison
from apps.plannings.models import GenerationIA, StatutGeneration, TypeGeneration
from apps.produits.models import Produit
from apps.statistiques.services import ServiceStatistiques
from commun.horloge import maintenant

POINT: dict[str, Any] = {"type": "Point", "coordinates": [11.5137, 3.848]}
POLYGONE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [
            [11.51, 3.84],
            [11.52, 3.84],
            [11.52, 3.85],
            [11.51, 3.85],
            [11.51, 3.84],
        ]
    ],
}

_COMPTEUR = iter(range(1, 100_000))


def _reference() -> str:
    return f"T{next(_COMPTEUR):08d}"


def _exploitation(agriculteur: Any, nom: str = "Champ") -> Exploitation:
    return Exploitation.objects.create(
        agriculteur=agriculteur,
        nom=nom,
        parcelle=POLYGONE,
        centre=POINT,
        superficie_ha=Decimal("1.5"),
        type_sol="argileux",
        region="centre",
    )


def _produit(exploitation: Exploitation, nom: str) -> Produit:
    return Produit.objects.create(
        exploitation=exploitation,
        nom=nom,
        categorie="legumes",
        unite="kg",
        prix_unitaire_fcfa=500,
        quantite_stock=Decimal("100"),
    )


def _commande(
    acheteur: Any,
    agriculteur: Any,
    *,
    statut: str,
    sous_total: int,
    commission: int = 0,
    cree_le: datetime | None = None,
) -> Commande:
    champs: dict[str, Any] = {
        "reference": _reference(),
        "acheteur": acheteur,
        "agriculteur": agriculteur,
        "statut": statut,
        "adresse_livraison_texte": "Yaoundé",
        "position_livraison": POINT,
        "position_recuperation": POINT,
        "distance_km": 6,
        "sous_total_fcfa": sous_total,
        "frais_livraison_fcfa": 0,
        "total_fcfa": sous_total,
        "commission_fcfa": commission,
    }
    if cree_le is not None:
        champs["cree_le"] = cree_le
    return Commande.objects.create(**champs)


def _ligne(commande: Commande, produit: Produit, *, quantite: str, total: int) -> LigneCommande:
    return LigneCommande.objects.create(
        commande=commande,
        produit=produit,
        nom_produit=produit.nom,
        unite="kg",
        quantite=Decimal(quantite),
        prix_unitaire_fcfa=produit.prix_unitaire_fcfa,
        total_ligne_fcfa=total,
    )


def _utc(annee: int, mois: int, jour: int = 5) -> datetime:
    return datetime(annee, mois, jour, 10, 0, tzinfo=UTC)


@pytest.fixture
def duo(db: Any, fabrique_utilisateur: Any) -> dict[str, Any]:
    acheteur = fabrique_utilisateur(role="acheteur", nom="Amina")
    paul = fabrique_utilisateur(role="agriculteur", nom="Paul")
    autre_agri = fabrique_utilisateur(role="agriculteur", nom="Pierre")
    return {"acheteur": acheteur, "paul": paul, "autre": autre_agri}


# --- Chiffres d'affaires / séparation -------------------------------------


def test_mes_ventes_agrege_commandes_livrees_seulement(duo: dict[str, Any]) -> None:
    paul = duo["paul"]
    acheteur = duo["acheteur"]
    _commande(acheteur, paul, statut=StatutCommande.LIVREE, sous_total=2000, commission=100)
    _commande(
        acheteur,
        paul,
        statut=StatutCommande.REMBOURSEE,
        sous_total=1000,
        commission=50,
    )
    # Non comptée : une commande payée jamais livrée.
    _commande(acheteur, paul, statut=StatutCommande.PAYEE, sous_total=9999, commission=500)

    stats = ServiceStatistiques.mes_ventes(paul, du=None, au=None, granularite="mois")
    assert stats["nombre_ventes"] == 2
    assert stats["chiffre_affaires_brut_fcfa"] == 3000
    assert stats["commissions_fcfa"] == 150
    assert stats["revenu_net_fcfa"] == 2850


def test_mes_ventes_separation_entre_agriculteurs(duo: dict[str, Any]) -> None:
    acheteur = duo["acheteur"]
    _commande(acheteur, duo["paul"], statut=StatutCommande.LIVREE, sous_total=2500, commission=125)
    _commande(acheteur, duo["autre"], statut=StatutCommande.LIVREE, sous_total=7000, commission=350)

    paul = ServiceStatistiques.mes_ventes(duo["paul"], du=None, au=None, granularite="mois")
    autre = ServiceStatistiques.mes_ventes(duo["autre"], du=None, au=None, granularite="mois")
    assert paul["chiffre_affaires_brut_fcfa"] == 2500
    assert autre["chiffre_affaires_brut_fcfa"] == 7000


def test_mes_ventes_produits_populaires_et_quantites(duo: dict[str, Any]) -> None:
    paul = duo["paul"]
    acheteur = duo["acheteur"]
    champ = _exploitation(paul)
    tomates = _produit(champ, "Tomates")
    mais = _produit(champ, "Maïs")

    cmd1 = _commande(acheteur, paul, statut=StatutCommande.LIVREE, sous_total=6000, commission=300)
    _ligne(cmd1, tomates, quantite="10", total=5000)
    _ligne(cmd1, mais, quantite="2", total=1000)
    cmd2 = _commande(acheteur, paul, statut=StatutCommande.LIVREE, sous_total=3000, commission=150)
    _ligne(cmd2, tomates, quantite="6", total=3000)

    stats = ServiceStatistiques.mes_ventes(paul, du=None, au=None, granularite="mois")
    assert stats["quantite_totale"] == pytest.approx(18.0)
    produits = stats["produits_populaires"]
    assert produits[0]["nom_produit"] == "Tomates"
    assert produits[0]["total_fcfa"] == 8000
    assert produits[0]["unites"] == pytest.approx(16.0)


# --- Filtre de période ----------------------------------------------------


def test_mes_ventes_filtre_de_periode(duo: dict[str, Any]) -> None:
    paul = duo["paul"]
    acheteur = duo["acheteur"]
    _commande(
        acheteur,
        paul,
        statut=StatutCommande.LIVREE,
        sous_total=1000,
        cree_le=_utc(2026, 1),
    )
    _commande(
        acheteur,
        paul,
        statut=StatutCommande.LIVREE,
        sous_total=2000,
        cree_le=_utc(2026, 3),
    )

    janvier = ServiceStatistiques.mes_ventes(
        paul, du="2026-01-01", au="2026-01-31", granularite="mois"
    )
    assert janvier["nombre_ventes"] == 1
    assert janvier["chiffre_affaires_brut_fcfa"] == 1000

    mars = ServiceStatistiques.mes_ventes(
        paul, du="2026-03-01", au="2026-03-31", granularite="mois"
    )
    assert mars["chiffre_affaires_brut_fcfa"] == 2000


def test_mes_ventes_periode_invalide(db: Any, fabrique_utilisateur: Any) -> None:
    from commun.exceptions import ErreurMetier

    paul = fabrique_utilisateur(role="agriculteur")
    with pytest.raises(ErreurMetier) as exc:
        ServiceStatistiques.mes_ventes(paul, du="2026-13-99", au=None, granularite="mois")
    assert exc.value.code == "periode_invalide"


# --- Série temporelle -----------------------------------------------------


def test_serie_mensuelle_tri_et_total(duo: dict[str, Any]) -> None:
    paul = duo["paul"]
    acheteur = duo["acheteur"]
    for mois, montant in [(1, 1000), (2, 2000), (1, 500)]:
        _commande(
            acheteur,
            paul,
            statut=StatutCommande.LIVREE,
            sous_total=montant,
            cree_le=_utc(2026, mois),
        )
    stats = ServiceStatistiques.mes_ventes(paul, du=None, au=None, granularite="mois")
    serie = stats["serie"]
    assert [p["periode"] for p in serie] == ["2026-01", "2026-02"]
    assert serie[0]["nombre"] == 2
    assert serie[0]["montant_fcfa"] == 1500


# --- Livraisons / accompagnements / plateforme ----------------------------


def test_mes_livraisons_frais_sur_livrees(
    db: Any, fabrique_utilisateur: Any, duo: dict[str, Any]
) -> None:
    jean = fabrique_utilisateur(role="livreur", nom="Jean")
    paul = duo["paul"]
    acheteur = duo["acheteur"]
    livree = _commande(acheteur, paul, statut=StatutCommande.LIVREE, sous_total=1000)
    Commande.objects.filter(id=livree.id).update(frais_livraison_fcfa=1400)
    livraison = Livraison.objects.create(
        commande=Commande.objects.get(id=livree.id),
        livreur=jean,
        statut=StatutLivraison.LIVREE,
        code_remise_chiffre="x",
    )
    # Deuxième course en cours, non livrée → frais non comptés.
    en_cours = _commande(acheteur, paul, statut=StatutCommande.PREPARATION, sous_total=800)
    Commande.objects.filter(id=en_cours.id).update(frais_livraison_fcfa=900)
    Livraison.objects.create(
        commande=Commande.objects.get(id=en_cours.id),
        livreur=jean,
        statut=StatutLivraison.EN_COURS,
        code_remise_chiffre="y",
    )
    stats = ServiceStatistiques.mes_livraisons(jean, du=None, au=None, granularite="mois")
    assert stats["nombre_livraisons"] == 2
    assert stats["livrees"] == 1
    assert stats["frais_cumules_fcfa"] == 1400
    assert stats["livraisons_par_statut"][livraison.statut] == 1


def test_mes_accompagnements_revenu_net(
    db: Any, fabrique_utilisateur: Any, duo: dict[str, Any]
) -> None:
    nadia = fabrique_utilisateur(role="ingenieur_agronome", nom="Nadia")
    paul = duo["paul"]
    champ = _exploitation(paul)
    Accompagnement.objects.create(
        agriculteur=paul,
        exploitation=champ,
        ingenieur=nadia,
        modalite=Modalite.PRESENTIEL,
        motif="Conseil",
        statut=StatutAccompagnement.TERMINEE,
        prix_consultation_fcfa=10000,
        frais_deplacement_fcfa=4800,
        commission_consultation_fcfa=1250,
    )
    stats = ServiceStatistiques.mes_accompagnements(nadia, du=None, au=None, granularite="mois")
    assert stats["nombre_accompagnements"] == 1
    assert stats["terminees"] == 1
    assert stats["revenu_net_fcfa"] == 10000 + 4800 - 1250


def test_plateforme_agregats_globaux(
    db: Any, fabrique_utilisateur: Any, duo: dict[str, Any]
) -> None:
    acheteur = duo["acheteur"]
    paul = duo["paul"]
    _commande(acheteur, paul, statut=StatutCommande.LIVREE, sous_total=2000, commission=100)
    _commande(acheteur, paul, statut=StatutCommande.EN_ATTENTE, sous_total=500)
    stats = ServiceStatistiques.plateforme(du=None, au=None, granularite="mois")
    # Trois utilisateurs actifs (amina acheteur, paul + autre agriculteur).
    assert stats["total_utilisateurs"] >= 3
    assert stats["utilisateurs_par_role"]["agriculteur"] >= 2
    assert stats["total_commandes"] == 2
    assert stats["commandes_par_statut"][StatutCommande.LIVREE] == 1
    assert stats["chiffre_affaires_fcfa"] == 2000
    assert stats["revenus_plateforme_fcfa"] == 100


def test_plateforme_remboursements_et_generations_ia(db: Any, fabrique_utilisateur: Any) -> None:
    acheteur = fabrique_utilisateur(role="acheteur")
    agr = fabrique_utilisateur(role="agriculteur")
    commande = _commande(
        acheteur, agr, statut=StatutCommande.LIVREE, sous_total=1000, commission=50
    )
    from apps.paiements.models import Paiement, StatutPaiement

    paiement = Paiement.objects.create(
        objet="commande",
        commande=commande,
        payeur=acheteur,
        montant_fcfa=1000,
        reference_interne="ref-test-1",
        cle_idempotence="idem-test-1",
        statut=StatutPaiement.REUSSI,
    )
    Remboursement.objects.create(
        paiement=paiement,
        commande=commande,
        demandeur=acheteur,
        motif="annulation",
        montant_demande_fcfa=1000,
        montant_accorde_fcfa=800,
        statut=StatutRemboursement.EFFECTUE,
    )
    champ = _exploitation(agr)
    GenerationIA.objects.create(
        type=TypeGeneration.PLANNING,
        exploitation=champ,
        demandeur=agr,
        culture="tomate",
        periode_debut=maintenant().date(),
        periode_fin=maintenant().date(),
        entrees={},
        statut=StatutGeneration.REUSSIE,
        version_prompt="v1",
        mois_quota=maintenant().strftime("%Y-%m"),
    )
    stats = ServiceStatistiques.plateforme(du=None, au=None, granularite="mois")
    assert stats["remboursements"]["nombre"] == 1
    assert stats["remboursements"]["montant_effectue_fcfa"] == 800
    assert stats["total_generations_ia"] == 1
    assert stats["generations_ia_par_statut"][StatutGeneration.REUSSIE] == 1

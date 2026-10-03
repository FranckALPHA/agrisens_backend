"""Tests d'intégration de la feature F-10 (administration).

Couvre les règles du prompt F-10 point 7 :
- valeur hors bornes refusée ;
- date d'effet future non appliquée avant l'heure ;
- restauration = recréation de l'avant-dernière valeur ;
- reversement supérieur au solde refusé ;
- audit présent pour chaque action ;
- une commande créée avant un changement de tarif garde l'ancien calcul.
"""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from apps.administration.models import JournalAudit
from apps.administration.services import offres, parametres
from apps.commandes.models import Commande, StatutCommande
from apps.litiges.models import MotifRemboursement, Remboursement, StatutRemboursement
from apps.paiements.models import (
    ObjetPaiement,
    OperateurPaiement,
    Paiement,
    Reversement,
    StatutPaiement,
)
from apps.referentiels import services as referentiels
from apps.referentiels.models import Referentiel
from commun import horloge
from commun.exceptions import Conflit, NonTrouve, ValidationMetier

CLE = "livraison.tarif_km_fcfa"
POINT_YAOUNDE = {"type": "Point", "coordinates": [11.5137, 3.848]}


@pytest.fixture(autouse=True)
def _horloge_reinitialisee() -> Iterator[None]:
    horloge.reinitialiser()
    yield
    horloge.reinitialiser()


# --- Paramètres (bornes, date d'effet, restauration) -----------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_definition_hors_bornes_refusee() -> None:
    """Une valeur hors bornes est refusée sans créer de ligne ni d'audit."""
    with pytest.raises(ValidationMetier) as exc:
        parametres.definir(CLE, 5_000, motif="Test borne max")
    assert exc.value.code == "valeur_hors_bornes"
    assert not JournalAudit.objects.filter(
        action="modifier_parametre", nouvelle_valeur__isnull=False
    ).filter(nouvelle_valeur__valeur=5_000).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_date_effet_future_non_appliquee_avant_l_heure() -> None:
    """Une valeur planifiée ne devient en vigueur qu'à partir de sa date d'effet."""
    futur = horloge.maintenant() + timedelta(days=7)
    parametres.definir(CLE, 200, date_effet=futur, motif="Hausse programmée")

    # Toujours l'ancienne valeur (démarrage 150) avant l'échéance.
    assert parametres.obtenir(CLE) == 150
    assert [p.valeur for p in parametres.planifiees(CLE)] == [200]

    # On avance l'horloge au-delà de la date d'effet : la nouvelle valeur s'applique.
    horloge.decaler(timedelta(days=8))
    assert parametres.obtenir(CLE) == 200


@pytest.mark.integration
@pytest.mark.django_db
def test_restauration_recre_ancienne_valeur(fabrique_utilisateur: Any) -> None:
    """Restaurer recrée une nouvelle ligne avec l'avant-dernière valeur."""
    sadm = fabrique_utilisateur(role="super_admin")
    parametres.definir(CLE, 200, auteur=sadm, motif="Premier changement")
    parametres.definir(CLE, 300, auteur=sadm, motif="Second changement")
    assert parametres.obtenir(CLE) == 300

    ligne = parametres.restaurer(CLE, sadm)
    assert ligne.valeur == 200
    assert parametres.obtenir(CLE) == 200
    assert ligne.motif == "Restauration de la valeur précédente"
    # Trois lignes d'audit paramètre (2 changements + 1 restauration).
    assert JournalAudit.objects.filter(action="modifier_parametre").count() == 3


@pytest.mark.integration
@pytest.mark.django_db
def test_restauration_sans_historique_refusee(fabrique_utilisateur: Any) -> None:
    """Impossible de restaurer quand il n'existe qu'une seule valeur."""
    sadm = fabrique_utilisateur(role="super_admin")
    with pytest.raises(ValidationMetier) as exc:
        parametres.restaurer(CLE, sadm)
    assert exc.value.code == "restauration_impossible"


# --- Soldes et reversements (Q-11) -----------------------------------------


def _commande_livree(
    agriculteur: Any, acheteur: Any, *, sous_total: int, commission: int, frais: int
) -> Commande:
    commande = Commande.objects.create(
        reference=f"C{Commande.objects.count() + 1:05d}",
        acheteur=acheteur,
        agriculteur=agriculteur,
        statut=StatutCommande.LIVREE,
        adresse_livraison_texte="Yaoundé, test F-10",
        position_livraison=POINT_YAOUNDE,
        position_recuperation=POINT_YAOUNDE,
        distance_km=6,
        sous_total_fcfa=sous_total,
        frais_livraison_fcfa=frais,
        total_fcfa=sous_total + frais,
        commission_taux_pb=500,
        commission_fcfa=commission,
    )
    Paiement.objects.create(
        commande=commande,
        objet=ObjetPaiement.COMMANDE,
        payeur=acheteur,
        montant_fcfa=commande.total_fcfa,
        operateur=OperateurPaiement.MTN_MOMO,
        reference_interne=f"P{commande.reference}",
        cle_idempotence=f"I{commande.reference}",
        statut=StatutPaiement.REUSSI,
    )
    return commande


@pytest.mark.integration
@pytest.mark.django_db
def test_solde_agriculteur_formula(fabrique_utilisateur: Any) -> None:
    """Solde agriculteur = sous-total − commission des commandes livrées."""
    from apps.paiements.services import soldes

    paul = fabrique_utilisateur(role="agriculteur", nom="Paul")
    amina = fabrique_utilisateur(role="acheteur")
    _commande_livree(paul, amina, sous_total=12_500, commission=625, frais=1_400)

    details = soldes.details(paul)
    # Gains acquis nets de la commission (02 §2.4 : sous-total − commission).
    assert details["gains_acquis"] == 12_500 - 625
    assert details["solde"] == 12_500 - 625
    assert details["remboursements_imputes"] == 0
    assert details["deja_reverse"] == 0


@pytest.mark.integration
@pytest.mark.django_db
def test_reversement_superieur_solde_refuse(fabrique_utilisateur: Any) -> None:
    """Un reversement dépassant le solde disponible est refusé (422)."""
    from apps.paiements.services import reversements, soldes

    paul = fabrique_utilisateur(role="agriculteur")
    amina = fabrique_utilisateur(role="acheteur")
    admin = fabrique_utilisateur(role="admin")
    _commande_livree(paul, amina, sous_total=10_000, commission=500, frais=0)
    solde = soldes.details(paul)["solde"]

    with pytest.raises(ValidationMetier) as exc:
        reversements.enregistrer(
            admin,
            beneficiaire_id=paul.id,
            montant_fcfa=solde + 1,
            operateur=OperateurPaiement.MTN_MOMO,
            reference_operateur="REF-TROP",
        )
    assert exc.value.code == "montant_superieur_solde"
    assert Reversement.objects.filter(beneficiaire=paul).count() == 0


@pytest.mark.integration
@pytest.mark.django_db
def test_reversement_diminue_solde_et_audit(fabrique_utilisateur: Any) -> None:
    """Un reversement valide réduit le solde d'autant et journalise l'audit."""
    from apps.paiements.services import reversements, soldes

    paul = fabrique_utilisateur(role="agriculteur")
    amina = fabrique_utilisateur(role="acheteur")
    admin = fabrique_utilisateur(role="admin")
    _commande_livree(paul, amina, sous_total=10_000, commission=500, frais=0)
    solde_avant = soldes.details(paul)["solde"]

    reversements.enregistrer(
        admin,
        beneficiaire_id=paul.id,
        montant_fcfa=3_000,
        operateur=OperateurPaiement.ORANGE_MONEY,
        reference_operateur="REF-OK-01",
    )
    assert soldes.details(paul)["solde"] == solde_avant - 3_000
    assert JournalAudit.objects.filter(
        action="enregistrer_reversement", entite_type="reversement"
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_reversement_reference_obligatoire(fabrique_utilisateur: Any) -> None:
    """La référence opérateur est obligatoire."""
    from apps.paiements.services import reversements

    paul = fabrique_utilisateur(role="agriculteur")
    admin = fabrique_utilisateur(role="admin")
    with pytest.raises(ValidationMetier) as exc:
        reversements.enregistrer(
            admin,
            beneficiaire_id=paul.id,
            montant_fcfa=100,
            operateur=OperateurPaiement.MTN_MOMO,
            reference_operateur="   ",
        )
    assert exc.value.code == "reference_requise"


@pytest.mark.integration
@pytest.mark.django_db
def test_remboursement_impute_solde_agriculteur(fabrique_utilisateur: Any) -> None:
    """Hypothèse Q-43 : un remboursement exécuté ampute le solde du vendeur."""
    from apps.paiements.services import soldes

    paul = fabrique_utilisateur(role="agriculteur")
    amina = fabrique_utilisateur(role="acheteur")
    commande = _commande_livree(paul, amina, sous_total=10_000, commission=500, frais=0)
    paiement = commande.paiements.first()
    assert paiement is not None
    Remboursement.objects.create(
        paiement=paiement,
        commande=commande,
        demandeur=amina,
        motif=MotifRemboursement.PRODUIT_ENDOMMAGE,
        montant_demande_fcfa=2_000,
        montant_accorde_fcfa=2_000,
        statut=StatutRemboursement.EFFECTUE,
    )
    details = soldes.details(paul)
    assert details["remboursements_imputes"] == 2_000
    # Gains nets 9 500 − remboursement 2 000.
    assert details["solde"] == 10_000 - 500 - 2_000


# --- Offres (P-05) ----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_nouvelle_version_offre_versionne_et_audit(fabrique_utilisateur: Any) -> None:
    """Modifier une offre crée une version ; l'ancienne reste en base, auditée."""
    sadm = fabrique_utilisateur(role="super_admin")
    avant = offres.en_vigueur("gratuit")
    assert avant is not None
    ancien_plafond = avant.quota_recommandation_mois

    offres.nouvelle_version(
        sadm,
        code="gratuit",
        nom="Gratuit",
        prix_mensuel_fcfa=0,
        quota_planning_mois=3,
        quota_recommandation_mois=ancien_plafond,
    )
    courante = offres.en_vigueur("gratuit")
    assert courante is not None
    assert courante.quota_planning_mois == 3
    # L'ancienne version reste conservée (désactivée).
    assert courante.id != avant.id
    assert JournalAudit.objects.filter(
        action="modifier_offre", entite_type="offre_abonnement"
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_offre_prix_negatif_refuse(fabrique_utilisateur: Any) -> None:
    """Un prix mensuel négatif est refusé."""
    sadm = fabrique_utilisateur(role="super_admin")
    with pytest.raises(ValidationMetier) as exc:
        offres.nouvelle_version(
            sadm,
            code="gratuit",
            nom="Gratuit",
            prix_mensuel_fcfa=-1,
            quota_planning_mois=1,
            quota_recommandation_mois=1,
        )
    assert exc.value.code == "prix_invalide"


# --- Référentiels (jamais de suppression) ----------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiel_creer_renommer_desactiver(fabrique_utilisateur: Any) -> None:
    """Ajout, renommage et désactivation d'une entrée, chaque action auditée."""
    sadm = fabrique_utilisateur(role="super_admin")
    entree = referentiels.creer(
        "categorie_produit", "céréales-test", "Céréales test", acteur=sadm
    )
    assert entree.actif is True

    referentiels.renommer(entree.id, "Nouveau libellé", acteur=sadm)
    entree.refresh_from_db()
    assert entree.libelle == "Nouveau libellé"

    referentiels.desactiver(entree.id, acteur=sadm)
    entree.refresh_from_db()
    assert entree.actif is False
    # Jamais supprimée de la base.
    assert Referentiel.objects.filter(id=entree.id).exists()

    for action in ("creer_referentiel", "renommer_referentiel", "desactiver_referentiel"):
        assert JournalAudit.objects.filter(action=action, entite_type="referentiel").exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiel_code_en_conflit(fabrique_utilisateur: Any) -> None:
    """Un code déjà utilisé pour le type est refusé (409)."""
    sadm = fabrique_utilisateur(role="super_admin")
    referentiels.creer("unite", "kg-dup", "Kilogramme dupliqué", acteur=sadm)
    with pytest.raises(Conflit):
        referentiels.creer("unite", "kg-dup", "Autre libellé", acteur=sadm)


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiel_type_inconnu_refuse(fabrique_utilisateur: Any) -> None:
    """Un type de référentiel hors registre est refusé."""
    sadm = fabrique_utilisateur(role="super_admin")
    with pytest.raises(ValidationMetier):
        referentiels.creer("type_inexistant", "x", "Y", acteur=sadm)


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiel_desactiver_deja_desactive(fabrique_utilisateur: Any) -> None:
    """Désactiver une entrée déjà inactive lève Conflit."""
    sadm = fabrique_utilisateur(role="super_admin")
    entree = referentiels.creer("region", "r-dup", "Région dup", acteur=sadm)
    referentiels.desactiver(entree.id, acteur=sadm)
    with pytest.raises(Conflit):
        referentiels.desactiver(entree.id, acteur=sadm)


@pytest.mark.integration
@pytest.mark.django_db
def test_referentiel_renommer_introuvable(fabrique_utilisateur: Any) -> None:
    """Renommer une entrée inexistante lève NonTrouve."""
    sadm = fabrique_utilisateur(role="super_admin")
    with pytest.raises(NonTrouve):
        referentiels.renommer("00000000-0000-0000-0000-000000000000", "X", acteur=sadm)

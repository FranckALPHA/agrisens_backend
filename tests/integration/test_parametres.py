"""Tests d'intégration du service des paramètres, de l'audit et de l'historique."""

import pytest
from apps.administration.models import HistoriqueStatut, JournalAudit
from apps.administration.services import historique, parametres
from commun.exceptions import NonTrouve, ValidationMetier
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.integration
@pytest.mark.django_db
def test_endpoint_parametres_publics(client_api: APIClient) -> None:
    """GET /api/v1/parametres/publics renvoie les clés publiques avec leurs valeurs en vigueur."""
    reponse = client_api.get("/api/v1/parametres/publics")
    assert reponse.status_code == status.HTTP_200_OK
    data = reponse.data
    assert "livraison.base_fcfa" in data
    assert data["livraison.base_fcfa"] == 500
    assert "livraison.tarif_km_fcfa" in data
    assert data["livraison.tarif_km_fcfa"] == 150
    # Clé privée non exposée
    assert "commission.taux_pb" not in data


@pytest.mark.integration
@pytest.mark.django_db
def test_service_parametres_obtenir_et_definir() -> None:
    """Modification d'un paramètre respectant les bornes avec traçabilité dans l'audit."""
    val_init = parametres.obtenir("livraison.base_fcfa")
    assert val_init == 500

    # Définition d'une nouvelle valeur valide
    param = parametres.definir(
        cle="livraison.base_fcfa",
        valeur=600,
        motif="Ajustement coût carburant",
    )
    assert param.valeur == 600

    # Vérification de la prise en compte
    assert parametres.obtenir("livraison.base_fcfa") == 600

    # Vérification de l'entrée d'audit automatique
    entree_audit = JournalAudit.objects.filter(
        action="modifier_parametre", entite_id=param.id
    ).first()
    assert entree_audit is not None
    assert isinstance(entree_audit.ancienne_valeur, dict)
    assert isinstance(entree_audit.nouvelle_valeur, dict)
    assert entree_audit.ancienne_valeur["valeur"] == 500
    assert entree_audit.nouvelle_valeur["valeur"] == 600


@pytest.mark.integration
@pytest.mark.django_db
def test_service_parametres_bornes_invalides() -> None:
    """Le service refuse les valeurs hors bornes (erreur 422 ValidationMetier)."""
    # livraison.base_fcfa a pour bornes 0 à 5 000
    with pytest.raises(ValidationMetier) as exc_max:
        parametres.definir("livraison.base_fcfa", 6000)
    assert exc_max.value.code == "valeur_hors_bornes"
    assert exc_max.value.statut_http == 422

    with pytest.raises(ValidationMetier) as exc_min:
        parametres.definir("livraison.base_fcfa", -10)
    assert exc_min.value.code == "valeur_hors_bornes"


@pytest.mark.integration
@pytest.mark.django_db
def test_service_parametres_cle_inconnue() -> None:
    """Le service lève NonTrouve sur une clé inconnue."""
    with pytest.raises(NonTrouve):
        parametres.obtenir("cle_totalement_inconnue")


@pytest.mark.integration
@pytest.mark.django_db
def test_service_historique_transitions() -> None:
    """L'enregistrement d'une transition persiste dans HISTORIQUE_STATUT."""
    entree = historique.enregistrer_transition(
        entite_type="commande",
        entite_id="00000000-0000-0000-0000-000000000001",
        ancien="en_attente",
        nouveau="acceptee",
        motif="Stock vérifié et disponible",
    )
    assert entree.id is not None
    assert entree.ancien_statut == "en_attente"
    assert entree.nouveau_statut == "acceptee"
    assert HistoriqueStatut.objects.filter(id=entree.id).exists()

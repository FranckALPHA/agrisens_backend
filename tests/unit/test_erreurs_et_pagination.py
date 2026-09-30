"""Tests unitaires du format d'erreur unique et de la pagination DRF."""

import pytest
from commun.exceptions import (
    AccesInterdit,
    Conflit,
    ErreurMetier,
    NonTrouve,
    TransitionInvalide,
    ValidationMetier,
    gestionnaire_exceptions_drf,
)
from commun.pagination import PaginationStandard
from django.core.paginator import Paginator
from rest_framework import exceptions, status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory


@pytest.mark.unit
def test_hierarchie_exceptions_metier() -> None:
    """Vérifie les codes de statut HTTP et codes d'erreur par défaut."""
    e_base = ErreurMetier("erreur_test", "Erreur générique", 400, {"champ": "val"})
    assert e_base.statut_http == 400
    assert e_base.code == "erreur_test"
    assert e_base.details == {"champ": "val"}

    e_non_trouve = NonTrouve()
    assert e_non_trouve.statut_http == 404
    assert e_non_trouve.code == "ressource_introuvable"

    e_acces = AccesInterdit()
    assert e_acces.statut_http == 403
    assert e_acces.code == "permission_refusee"

    e_conflit = Conflit()
    assert e_conflit.statut_http == 409
    assert e_conflit.code == "conflit"

    e_trans = TransitionInvalide("en_attente", "livree", "commande")
    assert e_trans.statut_http == 409
    assert e_trans.code == "transition_invalide"
    assert e_trans.details["ancien_statut"] == "en_attente"
    assert e_trans.details["nouveau_statut"] == "livree"

    e_val = ValidationMetier("champ_requis", "Le champ X est obligatoire.")
    assert e_val.statut_http == 422
    assert e_val.code == "champ_requis"


@pytest.mark.unit
def test_gestionnaire_exceptions_format_unique() -> None:
    """Le gestionnaire DRF renvoie toujours le format unique {erreur: {code, message, details}}."""
    contexte = {"request": None}

    # Exception métier personnalisée
    exc = TransitionInvalide("initie", "payee", "paiement")
    resp = gestionnaire_exceptions_drf(exc, contexte)
    assert resp is not None
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "erreur" in resp.data
    assert resp.data["erreur"]["code"] == "transition_invalide"
    assert "Transition non autorisée" in resp.data["erreur"]["message"]
    assert resp.data["erreur"]["details"]["entite"] == "paiement"

    # Erreur de validation DRF
    exc_drf = exceptions.ValidationError({"telephone": ["Format invalide."]})
    resp_drf = gestionnaire_exceptions_drf(exc_drf, contexte)
    assert resp_drf is not None
    assert resp_drf.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp_drf.data["erreur"]["code"] == "validation_invalide"
    assert "telephone" in resp_drf.data["erreur"]["details"]

    # 401 Non authentifié
    exc_auth = exceptions.NotAuthenticated()
    resp_auth = gestionnaire_exceptions_drf(exc_auth, contexte)
    assert resp_auth is not None
    assert resp_auth.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp_auth.data["erreur"]["code"] == "authentification_requise"

    # 403 Permission refusée
    exc_perm = exceptions.PermissionDenied()
    resp_perm = gestionnaire_exceptions_drf(exc_perm, contexte)
    assert resp_perm is not None
    assert resp_perm.status_code == status.HTTP_403_FORBIDDEN
    assert resp_perm.data["erreur"]["code"] == "permission_refusee"

    # Exception inattendue 500
    exc_crash = RuntimeError("Panne inattendue du disque")
    resp_crash = gestionnaire_exceptions_drf(exc_crash, contexte)
    assert resp_crash is not None
    assert resp_crash.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert resp_crash.data["erreur"]["code"] == "erreur_interne"
    # Ne fait pas fuiter le texte interne de l'erreur dans la réponse
    assert "Panne inattendue" not in resp_crash.data["erreur"]["message"]


@pytest.mark.unit
def test_pagination_standard_format_reponse() -> None:
    """La pagination produit la structure {elements, page, taille, total}."""
    pagination = PaginationStandard()
    factory = APIRequestFactory()
    req = factory.get("/api/v1/objets?page=2&taille=5")
    drf_request = Request(req)

    donnees = list(range(1, 14))  # 13 éléments au total
    paginator = Paginator(donnees, 5)
    page_obj = paginator.page(2)

    pagination.page = page_obj
    pagination.request = drf_request

    reponse = pagination.get_paginated_response(list(page_obj))
    assert reponse.data == {
        "elements": [6, 7, 8, 9, 10],
        "page": 2,
        "taille": 5,
        "total": 13,
    }

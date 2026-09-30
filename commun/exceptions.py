"""Exceptions métier et gestionnaire d'erreurs uniforme pour l'API REST.

Conventions techniques §4 et prompt F-00 point 8.
Format d'erreur unique :
{
  "erreur": {
    "code": "...",
    "message": "...",
    "details": { ... }
  }
}
"""

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, HttpRequest, JsonResponse
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_exception_handler

logger = logging.getLogger("agrisens.erreurs")


class ErreurMetier(Exception):
    """Exception racine pour les erreurs métier de l'application."""

    def __init__(
        self,
        code: str,
        message: str,
        statut_http: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.statut_http = statut_http
        self.details = details or {}


class NonTrouve(ErreurMetier):
    """Ressource introuvable ou non visible pour l'utilisateur (404)."""

    def __init__(
        self,
        code: str = "ressource_introuvable",
        message: str = "Ressource introuvable.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status.HTTP_404_NOT_FOUND, details)


class AccesInterdit(ErreurMetier):
    """Accès non autorisé selon le rôle ou la propriété (403)."""

    def __init__(
        self,
        code: str = "permission_refusee",
        message: str = "Accès refusé.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status.HTTP_403_FORBIDDEN, details)


class Conflit(ErreurMetier):
    """Conflit d'état ou concurrence (409)."""

    def __init__(
        self,
        code: str = "conflit",
        message: str = "Conflit d'état ou de ressource.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status.HTTP_409_CONFLICT, details)


class TransitionInvalide(Conflit):
    """Transition de statut non permise dans la machine à états (409)."""

    def __init__(
        self,
        ancien_statut: str,
        nouveau_statut: str,
        entite: str = "entite",
        message: str | None = None,
    ) -> None:
        msg = (
            message
            or f"Transition non autorisée pour {entite} "
            f"de '{ancien_statut}' vers '{nouveau_statut}'."
        )
        super().__init__(
            code="transition_invalide",
            message=msg,
            details={
                "ancien_statut": ancien_statut,
                "nouveau_statut": nouveau_statut,
                "entite": entite,
            },
        )


class ValidationMetier(ErreurMetier):
    """Erreur de validation métier ou hors bornes (422)."""

    def __init__(
        self,
        code: str = "validation_invalide",
        message: str = "Données invalides.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status.HTTP_422_UNPROCESSABLE_ENTITY, details)


class ErreurFournisseur(ErreurMetier):
    """Erreur d'un service externe (NotchPay, IA Groq, etc.) (502)."""

    def __init__(
        self,
        code: str = "erreur_fournisseur",
        message: str = "Erreur de communication avec un service partenaire.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status.HTTP_502_BAD_GATEWAY, details)


def construire_reponse_erreur(
    code: str, message: str, statut_http: int, details: dict[str, Any] | None = None
) -> Response:
    """Construit un objet Response DRF avec le format JSON uniforme."""
    return Response(
        {
            "erreur": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
        status=statut_http,
    )


def gestionnaire_exceptions_drf(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Gestionnaire central d'exceptions DRF.

    Convertit toutes les exceptions (métier, DRF, inattendues)
    au format unique AgriSens (journal Q-21 / conventions §4).
    """
    if isinstance(exc, ErreurMetier):
        return construire_reponse_erreur(
            code=exc.code,
            message=exc.message,
            statut_http=exc.statut_http,
            details=exc.details,
        )

    if isinstance(exc, Http404):
        return construire_reponse_erreur(
            code="ressource_introuvable",
            message="Ressource introuvable.",
            statut_http=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, DjangoPermissionDenied):
        return construire_reponse_erreur(
            code="permission_refusee",
            message="Accès refusé.",
            statut_http=status.HTTP_403_FORBIDDEN,
        )

    # Appel au gestionnaire par défaut de DRF
    reponse_drf = drf_default_exception_handler(exc, context)

    if reponse_drf is not None:
        statut = reponse_drf.status_code
        data = reponse_drf.data

        if isinstance(exc, drf_exceptions.ValidationError):
            return construire_reponse_erreur(
                code="validation_invalide",
                message="Données invalides.",
                statut_http=status.HTTP_422_UNPROCESSABLE_ENTITY,
                details=data if isinstance(data, dict) else {"erreurs": data},
            )

        if statut == status.HTTP_400_BAD_REQUEST:
            return construire_reponse_erreur(
                code="donnees_invalides",
                message="Données de requête invalides.",
                statut_http=status.HTTP_422_UNPROCESSABLE_ENTITY,
                details=data if isinstance(data, dict) else {"erreurs": data},
            )

        if statut == status.HTTP_401_UNAUTHORIZED:
            return construire_reponse_erreur(
                code="authentification_requise",
                message="Authentification requise.",
                statut_http=status.HTTP_401_UNAUTHORIZED,
            )

        if statut == status.HTTP_403_FORBIDDEN:
            return construire_reponse_erreur(
                code="permission_refusee",
                message="Accès refusé.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )

        if statut == status.HTTP_404_NOT_FOUND:
            return construire_reponse_erreur(
                code="ressource_introuvable",
                message="Ressource introuvable.",
                statut_http=status.HTTP_404_NOT_FOUND,
            )

        if statut == status.HTTP_405_METHOD_NOT_ALLOWED:
            return construire_reponse_erreur(
                code="methode_non_autorisee",
                message="Méthode non autorisée pour cette route.",
                statut_http=status.HTTP_405_METHOD_NOT_ALLOWED,
            )

        if statut == status.HTTP_429_TOO_MANY_REQUESTS:
            details = {}
            attente = getattr(exc, "wait", None)
            if attente is not None:
                details["attente_secondes"] = int(attente)
            return construire_reponse_erreur(
                code="limite_debit_atteinte",
                message="Trop de requêtes, veuillez réessayer plus tard.",
                statut_http=status.HTTP_429_TOO_MANY_REQUESTS,
                details=details,
            )

        # Autre erreur standard gérée par DRF
        return construire_reponse_erreur(
            code="erreur_requete",
            message=str(data.get("detail", "Erreur lors du traitement de la requête."))
            if isinstance(data, dict)
            else "Erreur requête.",
            statut_http=statut,
        )

    # Erreur inattendue (500)
    logger.exception("Erreur inattendue non gérée : %s", exc)
    return construire_reponse_erreur(
        code="erreur_interne",
        message="Une erreur inattendue est survenue sur le serveur.",
        statut_http=status.HTTP_500_INTERNAL_SERVER_ERROR,
        details={},
    )


def vue_erreur_404(request: HttpRequest, exception: Any = None) -> JsonResponse:
    """Vue personnalisée pour les erreurs 404 au niveau Django."""
    return JsonResponse(
        {
            "erreur": {
                "code": "ressource_introuvable",
                "message": "Ressource introuvable.",
                "details": {},
            }
        },
        status=404,
    )


def vue_erreur_500(request: HttpRequest) -> JsonResponse:
    """Vue personnalisée pour les erreurs 500 au niveau Django."""
    return JsonResponse(
        {
            "erreur": {
                "code": "erreur_interne",
                "message": "Une erreur inattendue est survenue sur le serveur.",
                "details": {},
            }
        },
        status=500,
    )

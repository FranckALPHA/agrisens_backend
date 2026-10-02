"""Passerelle de paiement Mobile Money (SPEC-05, journal Q-22).

Interface `PasserellePaiement` + implémentations `PasserelleNotchPay`
(réelle, `httpx` synchrone) et `PasserelleFactice` (tests, local sans
clé), choisies par `NOTCHPAY_MODE`. Champs et signature vérifiés sur le
projet de référence sandbox-testé (base `https://api.notchpay.co`,
`Authorization: <clé publique>`, webhook `X-Notch-Signature`).
"""

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from commun.exceptions import ErreurFournisseur
from django.conf import settings

logger = logging.getLogger("agrisens.paiements")

EN_TETE_SIGNATURE = "X-Notch-Signature"

# Statuts NotchPay (transaction.status) → statuts PAIEMENT.
STATUTS_REUSSI = {"complete"}
STATUTS_ECHOUE = {"failed", "canceled"}
STATUTS_EXPIRE = {"expired"}


class PasserellePaiement(ABC):
    """Contrat d'une passerelle Mobile Money."""

    @abstractmethod
    def initialiser(
        self,
        *,
        montant_fcfa: int,
        reference_interne: str,
        description: str,
        retour_url: str,
        telephone: str | None = None,
        email: str | None = None,
        nom_client: str | None = None,
    ) -> dict[str, str]:
        """Crée la transaction côté opérateur ; renvoie url + référence externe."""

    @abstractmethod
    def verifier_statut(self, reference_externe: str) -> str:
        """Statut normalisé : reussi | echoue | expire | en_attente | inconnu."""

    @abstractmethod
    def verifier_signature(self, entetes: Any, corps: bytes) -> bool:
        """Vérifie l'authenticité d'un webhook reçu."""

    @abstractmethod
    def rembourser(
        self, *, reference_externe: str, montant_fcfa: int, motif: str
    ) -> dict[str, Any]:
        """Exécute un reversement (Q-26 : méthode conservée, non utilisée en V1)."""


def _normaliser_statut_notchpay(statut: str) -> str:
    statut = (statut or "").lower()
    if statut in STATUTS_REUSSI:
        return "reussi"
    if statut in STATUTS_ECHOUE:
        return "echoue"
    if statut in STATUTS_EXPIRE:
        return "expire"
    if statut in ("pending", "processing", "initiated"):
        return "en_attente"
    return "en_attente"


class PasserelleNotchPay(PasserellePaiement):
    """Implémentation réelle NotchPay (`httpx` synchrone)."""

    def __init__(
        self,
        cle_publique: str | None = None,
        secret_webhook: str | None = None,
        url_base: str | None = None,
    ) -> None:
        self.cle_publique = cle_publique or settings.NOTCHPAY_CLE_PUBLIQUE
        self.secret_webhook = secret_webhook or settings.NOTCHPAY_SECRET_WEBHOOK
        self.url_base = (url_base or settings.NOTCHPAY_URL_BASE).rstrip("/")

    def _entetes(self) -> dict[str, str]:
        # Clé publique BRUTE (pas de schéma Bearer) — contrat vérifié.
        return {"Authorization": self.cle_publique, "Content-Type": "application/json"}

    def initialiser(self, **kwargs: Any) -> dict[str, str]:
        client_infos: dict[str, str] = {}
        if kwargs.get("email"):
            client_infos["email"] = kwargs["email"]
        if kwargs.get("telephone"):
            client_infos["phone"] = kwargs["telephone"]
        if kwargs.get("nom_client"):
            client_infos["name"] = kwargs["nom_client"]
        charge = {
            "amount": kwargs["montant_fcfa"],
            "currency": "XAF",
            "customer": client_infos,
            "description": kwargs["description"],
            "callback": kwargs["retour_url"],
            "reference": kwargs["reference_interne"],
        }
        try:
            reponse = httpx.post(
                f"{self.url_base}/payments", json=charge, headers=self._entetes(), timeout=30
            )
        except httpx.HTTPError as exc:
            raise ErreurFournisseur(details={"fournisseur": "notchpay"}) from exc
        if reponse.status_code not in (200, 201):
            raise ErreurFournisseur(
                code="paiement_refuse",
                message="Le fournisseur de paiement a refusé l'initialisation.",
                details={"fournisseur": "notchpay", "statut": reponse.status_code},
            )
        donnees = reponse.json()
        transaction = donnees.get("transaction", {})
        reference_externe = transaction.get("reference") or transaction.get("id")
        url_paiement = donnees.get("authorization_url")
        if not reference_externe or not url_paiement:
            raise ErreurFournisseur(details={"fournisseur": "notchpay"})
        return {"url_paiement": url_paiement, "reference_externe": reference_externe}

    def verifier_statut(self, reference_externe: str) -> str:
        # NotchPay ne retrouve un paiement que par SON identifiant (trx.…).
        try:
            reponse = httpx.get(
                f"{self.url_base}/payments/{reference_externe}",
                headers=self._entetes(),
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ErreurFournisseur(details={"fournisseur": "notchpay"}) from exc
        if reponse.status_code == 404:
            return "inconnu"
        if reponse.status_code != 200:
            raise ErreurFournisseur(details={"fournisseur": "notchpay"})
        transaction = reponse.json().get("transaction", {})
        return _normaliser_statut_notchpay(transaction.get("status", ""))

    def verifier_signature(self, entetes: Any, corps: bytes) -> bool:
        if hasattr(entetes, "get"):
            recue = entetes.get(EN_TETE_SIGNATURE, "") or entetes.get(EN_TETE_SIGNATURE.lower(), "")
        else:
            recue = ""
        if not self.secret_webhook or not recue:
            return False
        attendue = hmac.new(self.secret_webhook.encode("utf-8"), corps, hashlib.sha256).hexdigest()
        return hmac.compare_digest(attendue, recue)

    def rembourser(self, **kwargs: Any) -> dict[str, Any]:
        # Q-26 : l'API NotchPay répond 403 avec les clés de test ; méthode
        # conservée pour plus tard, jamais appelée en V1.
        raise ErreurFournisseur(
            code="remboursement_non_pris_en_charge",
            message="Remboursement automatique non disponible en V1 (manuel hors plateforme).",
        )


class PasserelleFactice(PasserellePaiement):
    """Implémentation de test : aucune requête réseau.

    `initialiser` renvoie l'URL du simulateur local ; `verifier_statut`
    suit `resultats` (référence externe → statut), `en_attente` par défaut.
    Les tests d'intégration pilotent `resultats` directement.
    """

    resultats: dict[str, str] = {}

    def __init__(self, url_simulateur: str | None = None) -> None:
        base = (url_simulateur or settings.API_URL_PUBLIQUE).rstrip("/")
        self.url_simulateur = base

    def initialiser(self, **kwargs: Any) -> dict[str, str]:
        reference_interne = kwargs["reference_interne"]
        chemin = f"/api/v1/dev/notchpay/simulateur/{reference_interne}/"
        return {
            "url_paiement": f"{self.url_simulateur}{chemin}",
            "reference_externe": f"FCT-{reference_interne}",
        }

    def verifier_statut(self, reference_externe: str) -> str:
        return self.resultats.get(reference_externe, "en_attente")

    def verifier_signature(self, entetes: Any, corps: bytes) -> bool:
        # Le simulateur signe avec le même secret que la vraie passerelle.
        secret = settings.NOTCHPAY_SECRET_WEBHOOK
        recue = entetes.get(EN_TETE_SIGNATURE, "") if hasattr(entetes, "get") else ""
        if not secret or not recue:
            return False
        attendue = hmac.new(secret.encode("utf-8"), corps, hashlib.sha256).hexdigest()
        return hmac.compare_digest(attendue, recue)

    def rembourser(self, **kwargs: Any) -> dict[str, Any]:
        raise ErreurFournisseur(
            code="remboursement_non_pris_en_charge",
            message="Remboursement automatique non disponible en V1.",
        )


def passerelle_paiement(url_simulateur: str | None = None) -> PasserellePaiement:
    """Choisit l'implémentation selon NOTCHPAY_MODE (Q-22)."""
    if getattr(settings, "NOTCHPAY_MODE", "factice") == "reel":
        return PasserelleNotchPay()
    return PasserelleFactice(url_simulateur=url_simulateur)


def signer_webhook(corps: bytes, secret: str | None = None) -> str:
    """Calcule la signature d'un webhook sortant (simulateur, utilitaires E2E)."""
    cle = secret if secret is not None else settings.NOTCHPAY_SECRET_WEBHOOK
    return hmac.new(cle.encode("utf-8"), corps, hashlib.sha256).hexdigest()

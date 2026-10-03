"""Adaptateurs fournisseurs IA (SPEC-08, 01 §1ter).

Deux implémentations derrière une même interface `FournisseurIA` :

- `FournisseurGroq` : appel HTTP **synchrone** (httpx) vers l'endpoint
  OpenAI-compatible `chat/completions` de Groq, sortie JSON contrainte, une
  nouvelle tentative sur erreur réseau ou 5xx, gestion du 429 via `Retry-After`
  ; le modèle vient toujours de `settings.IA_MODELE` (jamais en dur) ;
- `FournisseurFactice` : aucun réseau, déterministe, trois modes stables
  (`normal`, `invalide`, `echec`) plus un mode `timeout` pour tester
  l'annulation côté client. C'est le fournisseur par défaut en local/test.

Le fournisseur renvoie soit une `ReponseIA` (dict **brut**, pas encore validé
par Pydantic — la validation métier est faite par `ServiceGeneration`), soit
une `ErreurIA` avec un code stable. Il ne lève jamais d'exception réseau au
service : toute défaillance techniques se traduit par une `ErreurIA`.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from django.conf import settings
from pydantic import BaseModel

from apps.plannings.schemas import PlanningResult, RecommandationResult


@dataclass(frozen=True)
class ReponseIA:
    """Sortie brute d'un fournisseur, avant validation Pydantic."""

    contenu: dict[str, Any]
    fournisseur: str
    modele: str


@dataclass(frozen=True)
class ErreurIA:
    """Échec technique du fournisseur, avec code stable."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class FournisseurIA(ABC):
    """Interface unique des fournisseurs IA."""

    nom: str = "abstract"

    @abstractmethod
    def generer(
        self,
        *,
        systeme: str,
        utilisateur: str,
        schema: type[BaseModel],
        delai_s: int,
        mode: str = "normal",
    ) -> ReponseIA | ErreurIA:
        """Produit une réponse JSON conforme à `schema` ou une `ErreurIA`."""
        raise NotImplementedError


class FournisseurGroq(FournisseurIA):
    """Appel réel à Groq via l'API compatible OpenAI (httpx synchrone)."""

    nom = "groq"

    def __init__(
        self,
        *,
        url_base: str,
        cle_api: str,
        modele: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url_base = url_base.rstrip("/")
        self.cle_api = cle_api
        self.modele = modele
        self._transport = transport

    def generer(
        self,
        *,
        systeme: str,
        utilisateur: str,
        schema: type[BaseModel],
        delai_s: int,
        mode: str = "normal",
    ) -> ReponseIA | ErreurIA:
        corps = {
            "model": self.modele,
            "messages": [
                {"role": "system", "content": systeme},
                {"role": "user", "content": utilisateur},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        en_tetes = {
            "Authorization": f"Bearer {self.cle_api}",
            "Content-Type": "application/json",
        }
        url = f"{self.url_base}/chat/completions"

        derniere: ErreurIA | None = None
        for tentative in range(2):
            resultat = self._appel_unique(url, en_tetes, corps, delai_s)
            if isinstance(resultat, ReponseIA):
                return resultat
            # 429 : on ne retente qu'une fois, en respectant Retry-After.
            if resultat.code == "limite_fournisseur":
                return resultat
            # Erreur réseau ou 5xx : une nouvelle tentative après temporisation.
            derniere = resultat
            if tentative == 0:
                time.sleep(2)
        return derniere if derniere is not None else ErreurIA(
            code="erreur_fournisseur", message="Échec inconnu de l'appel Groq."
        )

    def _appel_unique(
        self,
        url: str,
        en_tetes: dict[str, str],
        corps: dict[str, Any],
        delai_s: int,
    ) -> ReponseIA | ErreurIA:
        try:
            with httpx.Client(transport=self._transport, timeout=delai_s) as client:
                reponse = client.post(url, headers=en_tetes, json=corps)
        except httpx.TimeoutException:
            return ErreurIA(code="delai_depasse", message="Délai d'appel IA dépassé.")
        except httpx.HTTPError as exc:
            return ErreurIA(code="reseau", message=f"Erreur réseau vers le fournisseur : {exc}.")

        if reponse.status_code == 429:
            return ErreurIA(
                code="limite_fournisseur",
                message="Le fournisseur IA a renvoyé une limite de débit persistante.",
                details={"attente": reponse.headers.get("Retry-After")},
            )
        if reponse.status_code >= 500:
            return ErreurIA(
                code="erreur_fournisseur",
                message=f"Le fournisseur IA est en erreur ({reponse.status_code}).",
            )
        if reponse.status_code >= 400:
            return ErreurIA(
                code="erreur_fournisseur",
                message=f"Requête refusée par le fournisseur ({reponse.status_code}).",
            )

        try:
            donnees = reponse.json()
            texte = donnees["choices"][0]["message"]["content"]
            contenu = json.loads(texte)
        except (ValueError, KeyError, IndexError, TypeError):
            return ErreurIA(
                code="reponse_invalide",
                message="La réponse du fournisseur n'est pas un JSON exploitable.",
            )
        if not isinstance(contenu, dict):
            return ErreurIA(
                code="reponse_invalide",
                message="Réponse IA non conforme (objet attendu).",
            )
        return ReponseIA(contenu=contenu, fournisseur=self.nom, modele=self.modele)


class FournisseurFactice(FournisseurIA):
    """Fournisseur déterministe sans réseau, pour local/test et les E2E."""

    nom = "factice"

    def generer(
        self,
        *,
        systeme: str,
        utilisateur: str,
        schema: type[BaseModel],
        delai_s: int,
        mode: str = "normal",
    ) -> ReponseIA | ErreurIA:
        if mode == "echec":
            return ErreurIA(
                code="erreur_fournisseur",
                message="Échec simulé du fournisseur (test_simule=echec).",
            )
        if mode == "invalide":
            # Réponse volontairement hors schéma : le service la marquera
            # `echouee` (`reponse_invalide`) sans décompter le quota.
            return ReponseIA(
                contenu={"culture": "tomate"},
                fournisseur=self.nom,
                modele="factice-invalide",
            )
        if mode == "timeout":
            # Simule une latence pour tester l'annulation côté client ; le
            # serveur va au bout (hypothèse Q-45) et consomme bien le quota.
            temps_attente = min(delai_s, 2)
            time.sleep(temps_attente)

        contenu = self._reponse_conforme(schema, utilisateur)
        return ReponseIA(contenu=contenu, fournisseur=self.nom, modele="factice-normal")

    @staticmethod
    def _reponse_conforme(schema: type[BaseModel], utilisateur: str) -> dict[str, Any]:
        entrees = _parser_json_souple(utilisateur)
        if schema is PlanningResult:
            return _fabrique_planning(entrees)
        if schema is RecommandationResult:
            return _fabrique_recommandation(entrees)
        # Schéma inconnu : réponse générique vide que la validation rejettera.
        return {}


def _parser_json_souple(texte: str) -> dict[str, Any]:
    try:
        valeur = json.loads(texte)
    except (ValueError, TypeError):
        return {}
    return valeur if isinstance(valeur, dict) else {}


def _dater(texte: Any, defaut: date) -> date:
    if isinstance(texte, str):
        try:
            return datetime.strptime(texte[:10], "%Y-%m-%d").date()
        except ValueError:
            return defaut
    return defaut


def _fabrique_planning(entrees: dict[str, Any]) -> dict[str, Any]:
    culture = str(entrees.get("culture") or "culture")
    periode = entrees.get("periode") or {}
    debut = _dater(periode.get("debut"), date(2026, 1, 1))
    fin = _dater(periode.get("fin"), debut + timedelta(days=90))
    if fin < debut:
        fin = debut + timedelta(days=90)
    tiers = max((fin - debut).days // 3, 1)
    t1 = debut
    t2 = debut + timedelta(days=tiers)
    t3 = debut + timedelta(days=2 * tiers)
    return {
        "culture": culture,
        "resume": f"Planning cultural indicatif pour la {culture} sur la période visée.",
        "avertissement": (
            "Ceci est une recommandation générée par une IA, pas un diagnostic "
            "agronomique. Faites valider par un ingénieur agronome."
        ),
        "etapes": [
            {
                "date_debut": t1.isoformat(),
                "date_fin": t2.isoformat(),
                "activite": "Préparation du sol et semis",
                "details": "Fumure de fond, ameublissement, semis en lignes espacées.",
                "intrants": ["semences certifiées", "compost / engrais de fond"],
            },
            {
                "date_debut": t2.isoformat(),
                "date_fin": t3.isoformat(),
                "activite": "Entretien et sarclage",
                "details": "Sarclage, binage, apport d'engrais d'entretien, gestion de l'eau.",
                "intrants": ["engrais d'entretien", "traitement phytosanitaire ciblé"],
            },
            {
                "date_debut": t3.isoformat(),
                "date_fin": fin.isoformat(),
                "activite": "Récolte",
                "details": "Récolte aux heures fraîches, tri et conditionnement.",
                "intrants": ["contenants propres"],
            },
        ],
        "risques": [
            "Pluies irrégulières en début de saison",
            "Attaques d'insectes en cas de forte humidité",
        ],
        "conseils": [
            "Privilégier un semis en début de saison des pluies.",
            "Faire analyser le sol si possible avant la fertilisation.",
        ],
    }


def _fabrique_recommandation(entrees: dict[str, Any]) -> dict[str, Any]:
    sujet = str(entrees.get("sujet") or entrees.get("culture") or "pratique agronomique")
    return {
        "sujet": sujet,
        "avertissement": (
            "Ceci est une recommandation générée par une IA, pas un diagnostic. "
            "Un accompagnement humain est conseillé pour toute décision importante."
        ),
        "recommandations": [
            {
                "titre": f"Préparer le terrain pour {sujet}",
                "detail": "Réaliser une analyse de sol et amender avant la culture.",
                "priorite": "haute",
            },
            {
                "titre": "Suivre l'irrigation",
                "detail": "Apporter l'eau aux heures fraîches, sans excès.",
                "priorite": "moyenne",
            },
            {
                "titre": "Surveiller les ravageurs",
                "detail": "Inspection hebdomadaire et lutte raisonnée.",
                "priorite": "basse",
            },
        ],
    }


def obtenir_fournisseur() -> FournisseurIA:
    """Fabrique le fournisseur configuré (défaut : factice).

    `groq` exige `IA_CLE_API` non vide ; sinon on élève une erreur claire afin
    de ne jamais retomber silencieusement sur le factice en production.
    """
    choix = getattr(settings, "IA_FOURNISSEUR", "factice")
    if choix == "groq":
        cle = getattr(settings, "IA_CLE_API", "")
        if not cle:
            raise RuntimeError(
                "IA_FOURNISSEUR=groq mais IA_CLE_API est vide : configuration invalide."
            )
        return FournisseurGroq(
            url_base=settings.IA_URL_BASE,
            cle_api=cle,
            modele=settings.IA_MODELE,
        )
    return FournisseurFactice()

"""Logique métier de la génération IA (SPEC-08, modèle 02 §2.5, docs/08 SPEC-08).

Règles appliquées ici (et uniquement ici — les vues restent minces) :

- **Quota mensuel** : l'offre en vigueur en V1 est toujours `gratuit`
  (l'abonnement payant est F-17). Seules les générations `reussie` décomptent
  le quota ; `echouee` (erreur technique, time-out, limite fournisseur,
  réponse invalide) ne décompte rien (prompt F-12 point 5). Remise à zéro le
  1er du mois suivant UTC.
- **Idempotence** : un `Idempotency-Key` rejoué par le même demandeur renvoie
  la génération existante sans rappeler le fournisseur.
- **Aucune donnée personnelle** : `ServiceEntrees.construire()` ne transmet
  que des données agronomiques de la parcelle ; le nom, le téléphone, l'e-mail
  et l'identifiant du demandeur ne quittent jamais le serveur (01 §1ter). Le
  centre est arrondi à 0,1° pour ne pas géolocaliser précisément.
- **Concurrence** : verrou pessimiste (`select_for_update`) sur les lignes du
  mois pour éviter une double consommation simultanée (hypothèse Q-45).
- **Réponse originale immuable** : une correction n'écrase jamais
  `reponse_originale` ; elle crée une `CorrectionIA` (journal D-11).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from commun.exceptions import (
    NonTrouve,
    QuotaAtteint,
    ValidationMetier,
)
from commun.horloge import maintenant
from django.db import IntegrityError, transaction
from pydantic import ValidationError

from apps.accompagnements.services import ServiceAccompagnement
from apps.administration.services import ServiceOffres
from apps.exploitations.models import Exploitation
from apps.plannings.fournisseurs import (
    ErreurIA,
    FournisseurIA,
    obtenir_fournisseur,
)
from apps.plannings.models import (
    CorrectionIA,
    GenerationIA,
    StatutGeneration,
    TypeGeneration,
)
from apps.plannings.prompts import SystemePour, VersionPour
from apps.plannings.schemas import decrire_schema, schema_pour

# Clés strictes du payload envoyé à l'IA — aucune donnée personnelle.
CLE_PLANNING = frozenset(
    {"culture", "sol", "superficie_m2", "periode", "region", "centre", "infos_agronomiques"}
)
CLE_RECOMMANDATION = frozenset(
    {"sujet", "culture", "contexte", "sol", "superficie_m2", "region", "centre"}
)


def _mois_courant() -> str:
    """Clé `AAAA-MM` UTC du mois en cours."""
    return maintenant().strftime("%Y-%m")


def _premiere_prochain_mois(mois: str) -> str:
    """Premier jour du mois suivant (date de réinitialisation du quota)."""
    analyse = datetime.strptime(mois, "%Y-%m").date()
    if analyse.month == 12:
        suivant = date(analyse.year + 1, 1, 1)
    else:
        suivant = date(analyse.year, analyse.month + 1, 1)
    return suivant.isoformat()


def _round_01(valeur: float) -> float:
    """Arrondi à 0,1 degré (1 décimale) sans artefact binaire visible."""
    return round(valeur, 1)


def _latlon_de_centre(centre: Any) -> tuple[float | None, float | None]:
    """Extrait (lat, lon) d'un centre, qu'il soit Shapely ou GeoJSON dict.

    Après un `objects.create()`, l'attribut reste le dict GeoJSON fourni ; après
    une relecture SQL, `ChampPoint.from_db_value` renvoie un objet Shapely
    (x=lon, y=lat). Les deux représentations sont acceptées.
    """
    if centre is None:
        return None, None
    if isinstance(centre, dict):
        coordonnees = centre.get("coordinates") or []
        if len(coordonnees) >= 2:
            return float(coordonnees[1]), float(coordonnees[0])
        return None, None
    lat = getattr(centre, "y", None)
    lon = getattr(centre, "x", None)
    if lat is None or lon is None:
        return None, None
    return float(lat), float(lon)


class ServiceEntrees:
    """Construit le payload exact envoyé au fournisseur IA (audit anti-personnel)."""

    @staticmethod
    def construire(
        exploitation: Exploitation, type_generation: str, donnees: dict[str, Any]
    ) -> dict[str, Any]:
        centre = exploitation.centre
        lat_brute, lon_brut = _latlon_de_centre(centre)
        lat = _round_01(lat_brute) if lat_brute is not None else None
        lon = _round_01(lon_brut) if lon_brut is not None else None
        superficie_m2 = int(Decimal(exploitation.superficie_ha or 0) * 10000)
        socle: dict[str, Any] = {
            "sol": exploitation.type_sol,
            "superficie_m2": superficie_m2,
            "region": exploitation.region,
            "centre": {"lat": lat, "lon": lon},
        }
        if type_generation == TypeGeneration.PLANNING:
            culture = (donnees.get("culture") or "").strip()
            debut = donnees.get("periode_debut")
            fin = donnees.get("periode_fin")
            if not culture:
                raise ValidationMetier(
                    code="donnees_manquantes",
                    message="La culture est obligatoire pour un planning.",
                    details={"champ": "culture"},
                )
            if not debut or not fin:
                raise ValidationMetier(
                    code="donnees_manquantes",
                    message="Les dates de début et de fin sont obligatoires.",
                    details={"champ": "periode"},
                )
            return {
                "culture": culture,
                "periode": {"debut": _iso(debut), "fin": _iso(fin)},
                "infos_agronomiques": (donnees.get("infos_agronomiques") or None),
                **socle,
            }
        if type_generation == TypeGeneration.RECOMMANDATION:
            sujet = (donnees.get("sujet") or "").strip()
            if not sujet:
                raise ValidationMetier(
                    code="donnees_manquantes",
                    message="Le sujet de la recommandation est obligatoire.",
                    details={"champ": "sujet"},
                )
            return {
                "sujet": sujet,
                "culture": (donnees.get("culture") or "").strip() or None,
                "contexte": (donnees.get("contexte") or None),
                **socle,
            }
        raise ValidationMetier(
            code="donnees_manquantes",
            message=f"Type de génération inconnu : {type_generation}.",
            details={"champ": "type"},
        )


def _iso(valeur: Any) -> str:
    if isinstance(valeur, (date, datetime)):
        return (
            valeur.strftime("%Y-%m-%d")
            if isinstance(valeur, date) and not isinstance(valeur, datetime)
            else valeur.date().strftime("%Y-%m-%d")
        )
    return str(valeur)[:10]


class ServiceQuota:
    """Compteur mensuel par type, adossé à l'offre en vigueur."""

    @staticmethod
    def offre_appliquee(demandeur: Any) -> Any:
        """V1 : toujours l'offre `gratuit` (l'abonnement payant arrive en F-17)."""
        offre = ServiceOffres.en_vigueur("gratuit")
        if offre is None:
            raise ValidationMetier(
                code="offre_indisponible",
                message="Aucune offre gratuite en vigueur pour évaluer le quota.",
            )
        return offre

    @staticmethod
    def limite(offre: Any, type_generation: str) -> int:
        if type_generation == TypeGeneration.PLANNING:
            return int(offre.quota_planning_mois)
        return int(offre.quota_recommandation_mois)

    @staticmethod
    def consomme(mois: str, demandeur: Any, type_generation: str) -> int:
        return GenerationIA.objects.filter(
            demandeur=demandeur,
            type=type_generation,
            mois_quota=mois,
            statut=StatutGeneration.REUSSIE,
        ).count()

    @classmethod
    def verifier(cls, limite: int, consomme: int, mois: str) -> None:
        if consomme >= limite:
            raise QuotaAtteint(
                message="Vous avez atteint votre quota mensuel pour ce type de génération.",
                details={
                    "limite": limite,
                    "consomme": consomme,
                    "date_reinitialisation": _premiere_prochain_mois(mois),
                },
            )

    @classmethod
    def vue_quota(cls, demandeur: Any) -> dict[str, Any]:
        mois = _mois_courant()
        offre = cls.offre_appliquee(demandeur)
        resultat: dict[str, Any] = {
            "mois": mois,
            "date_reinitialisation": _premiere_prochain_mois(mois),
        }
        for type_generation, cle in (
            (TypeGeneration.PLANNING, "planning"),
            (TypeGeneration.RECOMMANDATION, "recommandation"),
        ):
            resultat[cle] = {
                "consomme": cls.consomme(mois, demandeur, type_generation),
                "limite": cls.limite(offre, type_generation),
            }
        return resultat


class ServiceGeneration:
    """Déclenche une génération IA (planning ou recommandation) pour un demandeur."""

    @classmethod
    def generer(
        cls,
        demandeur: Any,
        exploitation: Exploitation,
        *,
        type_generation: str,
        donnees: dict[str, Any],
        cle_idempotence: str | None = None,
        mode: str = "normal",
        fournisseur: FournisseurIA | None = None,
    ) -> GenerationIA:
        if type_generation not in TypeGeneration.values:
            raise ValidationMetier(
                code="donnees_manquantes",
                message=f"Type de génération inconnu : {type_generation}.",
                details={"champ": "type"},
            )
        fournisseur = fournisseur or obtenir_fournisseur()
        mois = _mois_courant()
        offre = ServiceQuota.offre_appliquee(demandeur)
        limite = ServiceQuota.limite(offre, type_generation)

        generation: GenerationIA | None = None
        # Préparation transactionnelle : idempotence, verrou quota, création
        # `en_cours`. L'appel au fournisseur se fait APRÈS commit, pour qu'un
        # plantage laisse bien une ligne `en_cours` en base (hypothèse Q-45).
        try:
            with transaction.atomic():
                if cle_idempotence:
                    existant = GenerationIA.objects.filter(
                        demandeur=demandeur, cle_idempotence=cle_idempotence
                    ).first()
                    if existant is not None:
                        return existant
                lignes_verrouillees = list(
                    GenerationIA.objects.select_for_update()
                    .filter(demandeur=demandeur, type=type_generation, mois_quota=mois)
                    .only("statut")
                )
                consomme = sum(
                    1 for g in lignes_verrouillees if g.statut == StatutGeneration.REUSSIE
                )
                # Le mode simulé `echec` force l'échec du fournisseur : une
                # génération échouée ne décomptant pas le quota (SPEC-09, plan
                # PW-F12-04 « quota non consommé, compteur inchangé »), la
                # limite mensuelle ne s'applique pas à cette simulation.
                if mode != "echec":
                    ServiceQuota.verifier(limite, consomme, mois)
                entrees = ServiceEntrees.construire(exploitation, type_generation, donnees)
                generation = GenerationIA.objects.create(
                    type=type_generation,
                    exploitation=exploitation,
                    demandeur=demandeur,
                    culture=cls._culture_pour(type_generation, donnees, entrees),
                    periode_debut=cls._date_pour(donnees, "periode_debut", entrees),
                    periode_fin=cls._date_pour(donnees, "periode_fin", entrees),
                    entrees=entrees,
                    fournisseur_ia=fournisseur.nom,
                    modele_ia=cls._modele_initial(fournisseur),
                    version_prompt=VersionPour(type_generation),
                    statut=StatutGeneration.EN_COURS,
                    mois_quota=mois,
                    cle_idempotence=cle_idempotence,
                )
        except IntegrityError:
            # Course sur une même `Idempotency-Key` : contrainte unique violée,
            # on renvoie la génération déjà créée par le concurrent.
            if cle_idempotence:
                existant = GenerationIA.objects.filter(
                    demandeur=demandeur, cle_idempotence=cle_idempotence
                ).first()
                if existant is not None:
                    return existant
            raise
        assert generation is not None

        cls._executer_appel_fournisseur(generation, fournisseur, mode)
        return generation

    @staticmethod
    def _modele_initial(fournisseur: FournisseurIA) -> str:
        from django.conf import settings

        return getattr(settings, "IA_MODELE", "") if fournisseur.nom == "groq" else "factice"

    @staticmethod
    def _culture_pour(
        type_generation: str, donnees: dict[str, Any], entrees: dict[str, Any]
    ) -> str:
        if type_generation == TypeGeneration.PLANNING:
            return str(entrees.get("culture") or "")[:100]
        return str(donnees.get("culture") or entrees.get("sujet") or "")[:100]

    @staticmethod
    def _date_pour(donnees: dict[str, Any], cle: str, entrees: dict[str, Any]) -> date:
        valeur = donnees.get(cle)
        if isinstance(valeur, (date, datetime)):
            return valeur.date() if isinstance(valeur, datetime) else valeur
        periode = entrees.get("periode") or {}
        retourne = periode.get(cle.replace("periode_", ""))
        if retourne:
            return datetime.strptime(str(retourne)[:10], "%Y-%m-%d").date()
        return maintenant().date()

    @classmethod
    def _executer_appel_fournisseur(
        cls,
        generation: GenerationIA,
        fournisseur: FournisseurIA,
        mode: str,
    ) -> None:
        from apps.administration.services import parametres

        delai_s = int(parametres.obtenir("ia.delai_max_s"))
        systeme = SystemePour(generation.type)
        utilisateur = json.dumps(
            {
                "donnees": generation.entrees,
                "schema_requis": decrire_schema(generation.type),
            },
            ensure_ascii=False,
        )
        resultat = fournisseur.generer(
            systeme=systeme,
            utilisateur=utilisateur,
            schema=schema_pour(generation.type),
            delai_s=delai_s,
            mode=mode,
        )

        with transaction.atomic():
            verrouillee = GenerationIA.objects.select_for_update().filter(id=generation.id).first()
            if verrouillee is None:  # pragma: no cover - protection défensive
                return
            verrouillee.fournisseur_ia = fournisseur.nom
            if isinstance(resultat, ErreurIA):
                verrouillee.statut = StatutGeneration.ECHOUEE
                verrouillee.erreur = f"{resultat.code} : {resultat.message}"
                verrouillee.save(update_fields=["statut", "erreur", "fournisseur_ia", "maj_le"])
                generation.refresh_from_db()
                return

            verrouillee.modele_ia = resultat.modele or verrouillee.modele_ia
            try:
                valide = schema_pour(generation.type).model_validate(resultat.contenu)
            except ValidationError as exc:
                verrouillee.statut = StatutGeneration.ECHOUEE
                verrouillee.erreur = f"reponse_invalide : {exc}"
                verrouillee.save(
                    update_fields=["statut", "erreur", "modele_ia", "fournisseur_ia", "maj_le"]
                )
                generation.refresh_from_db()
                return

            verrouillee.reponse_originale = valide.model_dump(mode="json")
            verrouillee.statut = StatutGeneration.REUSSIE
            verrouillee.erreur = None
            verrouillee.save(
                update_fields=[
                    "reponse_originale",
                    "statut",
                    "erreur",
                    "modele_ia",
                    "fournisseur_ia",
                    "maj_le",
                ]
            )
            generation.refresh_from_db()

    @staticmethod
    def liste_pour_exploitation(exploitation: Exploitation) -> Any:
        return GenerationIA.objects.filter(exploitation=exploitation).select_related("demandeur")


class ServiceCorrection:
    """Création de corrections par un ingénieur affecté ; réponse originale intacte."""

    @classmethod
    @transaction.atomic
    def creer(
        cls,
        ingenieur: Any,
        generation_id: str,
        *,
        commentaire: str,
        contenu_corrige: dict[str, Any] | None = None,
    ) -> CorrectionIA:
        generation = (
            GenerationIA.objects.select_related("exploitation").filter(id=generation_id).first()
        )
        if generation is None:
            raise NonTrouve(
                code="generation_introuvable",
                message="Génération IA introuvable.",
            )
        if not ServiceAccompagnement.peut_acceder_exploitation(ingenieur, generation.exploitation):
            # Un ingénieur non affecté ne doit même pas savoir qu'elle existe.
            raise NonTrouve(
                code="generation_introuvable",
                message="Génération IA introuvable.",
            )
        commentaire_propre = (commentaire or "").strip()
        if not commentaire_propre:
            raise ValidationMetier(
                code="donnees_manquantes",
                message="Le commentaire de correction est obligatoire.",
                details={"champ": "commentaire"},
            )
        return CorrectionIA.objects.create(
            generation=generation,
            ingenieur=ingenieur,
            commentaire=commentaire_propre,
            contenu_corrige=contenu_corrige,
        )


def resoudre_mode_simule(valeur: str | None) -> str:
    """Mode de simulation du fournisseur factice, sinon `normal`.

    `?test_simule=echec|invalide|timeout|normal` n'est honoré que si le
    fournisseur actif est `factice` et que l'environnement est `local`/`test`
    (même philosophie que `/dev/horloge`). Sinon, ignoré silencieusement.
    """
    from django.conf import settings

    autorises = {"normal", "invalide", "echec", "timeout"}
    if (
        valeur in autorises
        and getattr(settings, "IA_FOURNISSEUR", "factice") == "factice"
        and getattr(settings, "ENVIRONNEMENT", "local") in ("local", "test")
    ):
        return valeur
    return "normal"

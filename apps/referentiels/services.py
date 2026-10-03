"""Logique métier pour l'accès et la gestion des référentiels."""

from typing import Any

from commun.exceptions import Conflit, NonTrouve, ValidationMetier

from apps.referentiels.donnees import REFERENTIELS_INITIAUX
from apps.referentiels.models import Referentiel


def _valider_type(type_ref: str) -> None:
    if type_ref not in REFERENTIELS_INITIAUX:
        raise ValidationMetier(
            code="type_referentiel_inconnu",
            message=f"Type de référentiel inconnu : '{type_ref}'.",
            details={"type": type_ref},
        )


def _journaliser(
    action: str,
    entree: Referentiel,
    acteur: Any,
    adresse_ip: str | None,
    *,
    ancienne_valeur: dict[str, Any] | None = None,
) -> None:
    """Audit obligatoire pour toute action de gestion (prompt F-10 point 3)."""
    from apps.administration.services import audit

    audit.journaliser(
        action=action,
        entite_type="referentiel",
        entite_id=str(entree.id),
        acteur=acteur,
        ancienne_valeur=ancienne_valeur,
        nouvelle_valeur={
            "type": entree.type,
            "code": entree.code,
            "libelle": entree.libelle,
            "ordre": entree.ordre,
            "actif": entree.actif,
        },
        adresse_ip=adresse_ip,
    )


def lister_par_type(type_ref: str) -> list[Referentiel]:
    """Retourne la liste ordonnée des entrées actives pour un type de référentiel."""
    if type_ref not in REFERENTIELS_INITIAUX:
        raise NonTrouve(
            code="referentiel_inconnu",
            message=f"Type de référentiel inconnu : '{type_ref}'.",
            details={"type": type_ref},
        )
    return list(Referentiel.objects.filter(type=type_ref, actif=True).order_by("ordre", "libelle"))


def lister_tous(type_ref: str, *, inclure_inactifs: bool = False) -> list[Referentiel]:
    """Liste de gestion (super-admin) : toutes les entrées d'un type."""
    _valider_type(type_ref)
    queryset = Referentiel.objects.filter(type=type_ref).order_by("ordre", "libelle")
    if not inclure_inactifs:
        queryset = queryset.filter(actif=True)
    return list(queryset)


def creer(
    type_ref: str,
    code: str,
    libelle: str,
    ordre: int = 0,
    *,
    acteur: Any = None,
    adresse_ip: str | None = None,
) -> Referentiel:
    """Ajoute une entrée de référentiel (super-admin). Code unique par type."""
    _valider_type(type_ref)
    code = (code or "").strip()
    libelle = (libelle or "").strip()
    if not code or not libelle:
        raise ValidationMetier(
            code="champ_requis",
            message="Le code et le libellé sont obligatoires.",
            details={"code": code, "libelle": libelle},
        )
    if Referentiel.objects.filter(type=type_ref, code=code).exists():
        raise Conflit(
            code="code_existants",
            message=f"Le code '{code}' existe déjà pour le type '{type_ref}'.",
            details={"type": type_ref, "code": code},
        )
    entree = Referentiel.objects.create(type=type_ref, code=code, libelle=libelle, ordre=ordre)
    _journaliser("creer_referentiel", entree, acteur, adresse_ip)
    return entree


def renommer(
    referentiel_id: str,
    libelle: str,
    *,
    acteur: Any = None,
    adresse_ip: str | None = None,
) -> Referentiel:
    """Renomme le libellé affiché ; le code stable ne change jamais."""
    entree = Referentiel.objects.filter(id=referentiel_id).first()
    if entree is None:
        raise NonTrouve(
            code="referentiel_introuvable",
            message="Entrée de référentiel introuvable.",
            details={"id": referentiel_id},
        )
    libelle = (libelle or "").strip()
    if not libelle:
        raise ValidationMetier(
            code="champ_requis",
            message="Le nouveau libellé est obligatoire.",
            details={"libelle": libelle},
        )
    ancien_libelle = entree.libelle
    entree.libelle = libelle
    entree.save(update_fields=["libelle", "maj_le"])
    _journaliser(
        "renommer_referentiel",
        entree,
        acteur,
        adresse_ip,
        ancienne_valeur={"type": entree.type, "code": entree.code, "libelle": ancien_libelle},
    )
    return entree


def desactiver(
    referentiel_id: str,
    *,
    acteur: Any = None,
    adresse_ip: str | None = None,
) -> Referentiel:
    """Désactive une entrée : jamais de suppression, les codes utilisés restent en base."""
    entree = Referentiel.objects.filter(id=referentiel_id).first()
    if entree is None:
        raise NonTrouve(
            code="referentiel_introuvable",
            message="Entrée de référentiel introuvable.",
            details={"id": referentiel_id},
        )
    if not entree.actif:
        raise Conflit(
            code="deja_desactive",
            message="Cette entrée est déjà désactivée.",
            details={"id": referentiel_id},
        )
    entree.actif = False
    entree.save(update_fields=["actif", "maj_le"])
    _journaliser("desactiver_referentiel", entree, acteur, adresse_ip)
    return entree

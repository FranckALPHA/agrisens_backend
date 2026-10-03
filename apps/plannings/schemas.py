"""Schémas Pydantic de la réponse IA (SPEC-08, 01 §1ter).

Seul usage autorisé de Pydantic dans le projet : valider la structure JSON
renvoyée par le fournisseur IA avant de la persister. Une `ValidationError`
transforme la génération en `echouee` (code `reponse_invalide`) sans décompter
le quota (prompt F-12 point 5, docs/08 SPEC-08).

Les modèles servent aussi à générer, via `model_json_schema()`, la description
de schéma injectée dans le prompt utilisateur pour cadrer la sortie du LLM.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EtapePlanning(BaseModel):
    """Une étape du planning cultural."""

    model_config = ConfigDict(extra="forbid")

    date_debut: date
    date_fin: date
    activite: str = Field(min_length=1)
    details: str = Field(default="")
    intrants: list[str] = Field(default_factory=list)


class PlanningResult(BaseModel):
    """Réponse attendue pour une génération de type `planning`."""

    model_config = ConfigDict(extra="forbid")

    culture: str = Field(min_length=1)
    resume: str = Field(default="")
    avertissement: str = Field(min_length=1)
    etapes: list[EtapePlanning] = Field(min_length=1)
    risques: list[str] = Field(default_factory=list)
    conseils: list[str] = Field(default_factory=list)


class RecommandationItem(BaseModel):
    """Une recommandation isolée, priorisée."""

    model_config = ConfigDict(extra="forbid")

    titre: str = Field(min_length=1)
    detail: str = Field(default="")
    priorite: Literal["basse", "moyenne", "haute"]


class RecommandationResult(BaseModel):
    """Réponse attendue pour une génération de type `recommandation`."""

    model_config = ConfigDict(extra="forbid")

    sujet: str = Field(min_length=1)
    avertissement: str = Field(min_length=1)
    recommandations: list[RecommandationItem] = Field(min_length=1)


def schema_pour(type_generation: str) -> type[BaseModel]:
    """Schéma Pydantic de référence pour un type de génération."""
    if type_generation == "planning":
        return PlanningResult
    if type_generation == "recommandation":
        return RecommandationResult
    raise ValueError(f"Type de génération inconnu : {type_generation}")


def decrire_schema(type_generation: str) -> dict[str, Any]:
    """JSON Schema du modèle, injecté dans le prompt utilisateur."""
    return schema_pour(type_generation).model_json_schema()

"""Test de contrat OpenAPI : vérifie que openapi.json est strictement à jour."""

import json
from pathlib import Path

import pytest
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiJsonRenderer


@pytest.mark.integration
def test_fichier_openapi_a_jour() -> None:
    """Échoue si le schéma exposé ne correspond pas exactement au fichier openapi.json versionné."""
    chemin_fichier = Path(__file__).resolve().parent.parent.parent / "openapi.json"
    assert chemin_fichier.exists(), (
        "Le fichier openapi.json doit exister à la racine du dépôt backend."
    )

    with open(chemin_fichier, encoding="utf-8") as f:
        schema_versionne = json.load(f)

    # Génération du schéma courant
    generateur = SchemaGenerator()
    schema_courant_dict = generateur.get_schema(request=None, public=True)
    rendu_bytes = OpenApiJsonRenderer().render(schema_courant_dict, renderer_context={})
    schema_courant = json.loads(rendu_bytes.decode("utf-8"))

    assert schema_versionne == schema_courant, (
        "Le fichier openapi.json n'est pas à jour avec le code actuel. "
        "Exécutez : uv run python manage.py spectacular --file openapi.json --format openapi-json"
    )

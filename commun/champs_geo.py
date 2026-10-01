"""Champs de modèles Django personnalisés pour MySQL spatial sans GeoDjango.

Conventions techniques §3, journal Q-32 et prompt F-00 point 12 :
- Colonnes MySQL SRID 4326 : POINT, POLYGON, LINESTRING.
- Écriture via ST_GeomFromText(%s, 4326) avec ordre latitude, longitude pour MySQL 8.
- Lecture via ST_AsText ou décodage WKB.
- Valeur Python = objet Shapely (ordre standard x=longitude, y=latitude).
- Aide pour index spatial : creer_index_spatial(table, colonne).
"""

from typing import Any, ClassVar

from django.db import migrations, models
from django.db.backends.base.base import BaseDatabaseWrapper
from rest_framework import serializers as drf_serializers
from shapely import wkb, wkt
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from commun.exceptions import ValidationMetier
from commun.geo import (
    SRID,
    _echanger_axes,
    geojson_vers_wkt_mysql,
    valider_geojson,
)


class _ChampGeometriqueBase(models.Field):
    """Classe de base pour les champs spatiaux MySQL sans GeoDjango."""

    type_sql: str = "GEOMETRY"

    def db_type(self, connection: BaseDatabaseWrapper) -> str:
        return f"{self.type_sql} SRID {SRID}"

    def select_format(self, compiler: Any, sql: str, params: Any) -> Any:
        """Lit la colonne sous forme WKT avec ST_AsText."""
        return f"ST_AsText({sql})", params

    def get_placeholder(self, value: Any, compiler: Any, connection: BaseDatabaseWrapper) -> str:
        """Écrit la valeur en base via ST_GeomFromText(%s, 4326)."""
        return f"ST_GeomFromText(%s, {SRID})"

    def from_db_value(
        self, value: Any, expression: Any, connection: BaseDatabaseWrapper
    ) -> BaseGeometry | None:
        """Convertit la valeur brute MySQL (WKT str ou WKB bytes) en objet Shapely (lon, lat)."""
        if value is None:
            return None
        if isinstance(value, BaseGeometry):
            return value
        if isinstance(value, str):
            # ST_AsText renvoie du WKT en ordre MySQL (latitude longitude pour SRID 4326)
            geom_mysql = wkt.loads(value)
            return _echanger_axes(geom_mysql)
        if isinstance(value, bytes):
            # Le format binaire interne de MySQL comporte 4 octets de SRID suivis du WKB
            raw_wkb = value[4:] if len(value) > 4 else value
            geom_wkb = wkb.loads(raw_wkb)
            return geom_wkb
        return None

    def to_python(self, value: Any) -> BaseGeometry | None:
        """Convertit une entrée GeoJSON (dict), WKT ou Shapely en objet Shapely (lon, lat)."""
        if value is None or isinstance(value, BaseGeometry):
            return value
        if isinstance(value, dict):
            valider_geojson(value)
            return shape(value)
        if isinstance(value, str):
            try:
                return wkt.loads(value)
            except Exception:
                pass
        return None

    def get_prep_value(self, value: Any) -> str | None:
        """Prépare la chaîne WKT en ordre MySQL (latitude longitude) pour ST_GeomFromText."""
        if value is None:
            return None
        if isinstance(value, dict):
            return geojson_vers_wkt_mysql(value)
        if isinstance(value, BaseGeometry):
            # L'objet Shapely Python est en ordre standard (x=lon, y=lat).
            # On échange pour que le WKT injecté dans MySQL soit (lat lon).
            geom_mysql = _echanger_axes(value)
            return geom_mysql.wkt
        if isinstance(value, str):
            return value
        return str(value)


class ChampPoint(_ChampGeometriqueBase):
    """Champ spatial pour POINT NOT NULL SRID 4326."""

    type_sql = "POINT"
    description = "Point géographique WGS84 (SRID 4326)"


class ChampPolygone(_ChampGeometriqueBase):
    """Champ spatial pour POLYGON NOT NULL SRID 4326."""

    type_sql = "POLYGON"
    description = "Polygone géographique WGS84 (SRID 4326)"


class ChampLigne(_ChampGeometriqueBase):
    """Champ spatial pour LINESTRING SRID 4326."""

    type_sql = "LINESTRING"
    description = "Ligne géographique WGS84 (SRID 4326)"


def creer_index_spatial(nom_table: str, nom_colonne: str) -> migrations.RunSQL:
    """Génère une opération de migration RunSQL créant un SPATIAL INDEX sur MySQL."""
    nom_index = f"idx_spatial_{nom_table}_{nom_colonne}"
    sql = f"CREATE SPATIAL INDEX {nom_index} ON {nom_table} ({nom_colonne});"
    reverse_sql = f"DROP INDEX {nom_index} ON {nom_table};"
    return migrations.RunSQL(sql, reverse_sql=reverse_sql)


class ChampGeoJSONSerializer(drf_serializers.Field):
    """Champ DRF : GeoJSON (lon, lat) ↔ géométrie Shapely lue de MySQL.

    Accepte Point, LineString et Polygon ; la validation métier
    (emprise Cameroun, géométrie valide) reste dans le service.
    """

    types_autorises = ("Point", "LineString", "Polygon")
    default_error_messages: ClassVar[dict[str, Any]] = {
        "invalid": "Position attendue au format GeoJSON (objet JSON).",
        "type_invalide": "Type de géométrie non autorisé : attendu {types}.",
        "invalide": "Géométrie invalide : {message}.",
        "non_serialisable": "Valeur géographique non serialisable ({valeur}).",
    }

    def __init__(self, **kwargs: Any) -> None:
        self.types_autorises = tuple(kwargs.pop("types", self.types_autorises))
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            self.fail("invalid")
        if data.get("type") not in self.types_autorises:
            self.fail("type_invalide", types=", ".join(self.types_autorises))
        # La validation métier (valide, non vide, emprise Cameroun) lève
        # ValidationMetier : on la propage telle quelle pour conserver le
        # code stable (ex. hors_emprise_cameroun) dans la réponse 422,
        # au lieu de le noyer dans « validation_invalide ».
        try:
            valider_geojson(data)
        except ValidationMetier:
            raise
        except Exception as exc:
            self.fail("invalide", message=str(exc))
        return data

    def to_representation(self, value: Any) -> Any:
        if isinstance(value, BaseGeometry):
            # Objet Shapely côté Python = ordre standard (lon, lat) = GeoJSON direct.
            return value.__geo_interface__
        if value is None or isinstance(value, dict):
            return value
        self.fail("non_serialisable", valeur=type(value).__name__)
        return None


try:
    from drf_spectacular.extensions import OpenApiSerializerFieldExtension

    class ChampGeoJSONScheme(OpenApiSerializerFieldExtension):
        """Schéma OpenAPI du champ GeoJSON : objet « type » + « coordinates ».

        Les géométries complexes (Polygon, LineString) ont des coordonnées
        imbriquées : le contrat reste un objet GeoJSON générique.
        """

        target_class = "commun.champs_geo.ChampGeoJSONSerializer"

        def map_serializer_field(self, auto_schema: Any, direction: Any) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "coordinates": {
                        "type": "array",
                        "items": {},
                        "description": (
                            "Coordonnées GeoJSON (imbrication selon le type : "
                            "Point, LineString ou Polygon)."
                        ),
                    },
                },
                "required": ["type", "coordinates"],
                "description": "Géométrie GeoJSON (SRID 4326, ordre x=longitude, y=latitude).",
            }
except ImportError:  # pragma: no cover - drf-spectacular est une dépendance fixe
    pass

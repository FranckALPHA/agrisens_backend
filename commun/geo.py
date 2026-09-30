"""Géographie : conversions GeoJSON ↔ MySQL, validation, distances, superficies.

Conventions techniques §3 et prompt F-00 point 12 :
- Pour le SRID 4326 dans MySQL 8, l'ordre attendu par ST_GeomFromText est latitude, longitude.
- Le format GeoJSON (RFC 7946) utilise l'ordre longitude, latitude.
- La conversion d'axes est centralisée ici.
- Emprise du Cameroun : latitude [1.6, 13.1], longitude [8.4, 16.2].
"""

import math
from collections.abc import Iterator
from typing import Any

from pyproj import Geod
from shapely import wkt
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from commun.exceptions import ValidationMetier

SRID = 4326

# Emprise géographique du Cameroun
LAT_MIN, LAT_MAX = 1.6, 13.1
LON_MIN, LON_MAX = 8.4, 16.2

_GEODESIQUE = Geod(ellps="WGS84")


def _echanger_axes(geom: BaseGeometry) -> BaseGeometry:
    """Échange récursif x ↔ y quel que soit le type de géométrie Shapely."""
    return transform(lambda x, y, z=None: (y, x), geom)


def geojson_vers_geometrie(geojson: dict[str, Any]) -> BaseGeometry:
    """Convertit un GeoJSON (lon, lat) en géométrie Shapely en ordre MySQL (x=lat, y=lon)."""
    valider_geojson(geojson)
    return _echanger_axes(shape(geojson))


def geometrie_vers_geojson(geom: BaseGeometry) -> dict[str, Any]:
    """Convertit une géométrie lue de MySQL (x=lat, y=lon) en dictionnaire GeoJSON (lon, lat)."""
    return _echanger_axes(geom).__geo_interface__


def geojson_vers_wkt_mysql(geojson: dict[str, Any]) -> str:
    """Convertit un GeoJSON (lon, lat) en WKT adapté à MySQL SRID 4326 (lat lon)."""
    geom_mysql = geojson_vers_geometrie(geojson)
    return geom_mysql.wkt


def wkt_mysql_vers_geojson(wkt_str: str) -> dict[str, Any]:
    """Convertit un WKT issu de MySQL (lat lon) en GeoJSON (lon lat)."""
    geom_mysql = wkt.loads(wkt_str)
    return geometrie_vers_geojson(geom_mysql)


def valider_geojson(geojson: dict[str, Any]) -> None:
    """Vérifie : géométrie reconnue, non vide, valide et dans l'emprise du Cameroun."""
    if not isinstance(geojson, dict) or geojson.get("type") not in (
        "Point",
        "LineString",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
    ):
        raise ValidationMetier(
            "geometrie_invalide",
            "GeoJSON : type de géométrie manquant ou non pris en charge.",
        )
    try:
        geom = shape(geojson)
    except Exception:
        raise ValidationMetier("geometrie_invalide", "GeoJSON mal formé.") from None

    if geom.is_empty:
        raise ValidationMetier("geometrie_invalide", "Géométrie vide.")

    if not geom.is_valid:
        raise ValidationMetier("geometrie_invalide", "Géométrie invalide topologiquement.")

    for lon, lat in _coordonnees(geom):
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            raise ValidationMetier(
                "hors_emprise_cameroun",
                f"Coordonnée hors de l'emprise du Cameroun "
                f"(lat {LAT_MIN}–{LAT_MAX}, lon {LON_MIN}–{LON_MAX}).",
                {"longitude": lon, "latitude": lat},
            )


def _coordonnees(geom: BaseGeometry) -> Iterator[tuple[float, float]]:
    """Itère sur tous les couples (lon, lat) d'une géométrie GeoJSON."""
    if geom.geom_type == "Point":
        yield geom.x, geom.y
    elif geom.geom_type == "LineString":
        yield from (c[:2] for c in geom.coords)
    elif geom.geom_type == "Polygon":
        for anneau in [geom.exterior, *geom.interiors]:
            yield from (c[:2] for c in anneau.coords)
    elif geom.geom_type.startswith(("Multi", "GeometryCollection")):
        for sous in getattr(geom, "geoms", []):
            yield from _coordonnees(sous)
    else:
        raise ValidationMetier(
            "geometrie_invalide", f"Type de géométrie non supporté : {geom.geom_type}."
        )


def distance_km(geojson_a: dict[str, Any], geojson_b: dict[str, Any]) -> float:
    """Calcule la distance géodésique WGS84 en km entre deux points GeoJSON."""
    valider_geojson(geojson_a)
    valider_geojson(geojson_b)
    a, b = shape(geojson_a), shape(geojson_b)
    if a.geom_type != "Point" or b.geom_type != "Point":
        raise ValidationMetier("geometrie_invalide", "La distance se calcule entre deux points.")
    _, _, dist_m = _GEODESIQUE.inv(a.x, a.y, b.x, b.y)
    return dist_m / 1000.0


def km_superieurs(distance: float) -> int:
    """Arrondi au kilomètre supérieur avant tout calcul tarifaire (conventions §3)."""
    if distance <= 0:
        return 0
    return math.ceil(distance - 1e-9)


def superficie_ha(geojson_polygone: dict[str, Any]) -> float:
    """Superficie géodésique WGS84 en hectares (pyproj.Geod, conventions §3)."""
    valider_geojson(geojson_polygone)
    geom = shape(geojson_polygone)
    if geom.geom_type != "Polygon":
        raise ValidationMetier("geometrie_invalide", "La superficie se calcule sur un polygone.")
    aire_m2, _ = _GEODESIQUE.geometry_area_perimeter(geom)
    return abs(aire_m2) / 10000.0

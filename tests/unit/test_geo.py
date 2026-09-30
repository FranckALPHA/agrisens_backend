"""Tests unitaires des calculs géographiques, conversions et validations."""

import pytest
from commun.exceptions import ValidationMetier
from commun.geo import (
    distance_km,
    geojson_vers_wkt_mysql,
    km_superieurs,
    superficie_ha,
    valider_geojson,
    wkt_mysql_vers_geojson,
)


@pytest.mark.unit
def test_distance_yaounde_douala() -> None:
    """Distance géodésique WGS84 entre Yaoundé et Douala ≈ 194 km (±2)."""
    yaounde = {"type": "Point", "coordinates": [11.5021, 3.8480]}
    douala = {"type": "Point", "coordinates": [9.7679, 4.0511]}

    dist = distance_km(yaounde, douala)
    assert 192.0 <= dist <= 196.0

    arrondi = km_superieurs(dist)
    assert arrondi == 194


@pytest.mark.unit
def test_arrondi_km_superieurs() -> None:
    """Arrondi au km supérieur strict (conventions §3)."""
    assert km_superieurs(0.0) == 0
    assert km_superieurs(0.1) == 1
    assert km_superieurs(5.0) == 5
    assert km_superieurs(5.2) == 6
    assert km_superieurs(6.0) == 6


@pytest.mark.unit
def test_conversion_axes_mysql_wkt() -> None:
    """GeoJSON (lon, lat) converti en WKT MySQL avec ordre (lat lon) pour SRID 4326."""
    point_geojson = {"type": "Point", "coordinates": [11.5021, 3.8480]}
    wkt_mysql = geojson_vers_wkt_mysql(point_geojson)

    # Dans le WKT généré pour MySQL 8 SRID 4326, la latitude doit précéder la longitude
    assert "POINT (3.848 11.5021)" in wkt_mysql or "POINT (3.8480 11.5021)" in wkt_mysql

    # Reconversion vers GeoJSON
    reconverti = wkt_mysql_vers_geojson(wkt_mysql)
    assert reconverti["type"] == "Point"
    assert abs(reconverti["coordinates"][0] - 11.5021) < 1e-4
    assert abs(reconverti["coordinates"][1] - 3.8480) < 1e-4


@pytest.mark.unit
def test_validation_emprise_cameroun() -> None:
    """Refuse les coordonnées hors des frontières géographiques du Cameroun."""
    # Point à Paris (hors Cameroun)
    point_paris = {"type": "Point", "coordinates": [2.3522, 48.8566]}
    with pytest.raises(ValidationMetier) as exc_info:
        valider_geojson(point_paris)
    assert exc_info.value.code == "hors_emprise_cameroun"

    # Point dans le Nord du Cameroun (Garoua : lat 9.3, lon 13.4)
    point_garoua = {"type": "Point", "coordinates": [13.4, 9.3]}
    valider_geojson(point_garoua)  # ne doit pas lever d'exception


@pytest.mark.unit
def test_validation_geometrie_vide_ou_invalide() -> None:
    """Refuse les géométries vides ou malformées."""
    with pytest.raises(ValidationMetier) as exc1:
        valider_geojson({"type": "Point", "coordinates": []})
    assert exc1.value.code == "geometrie_invalide"

    with pytest.raises(ValidationMetier) as exc2:
        valider_geojson("ceci n'est pas un dict")  # type: ignore[arg-type]
    assert exc2.value.code == "geometrie_invalide"


@pytest.mark.unit
def test_superficie_polygone() -> None:
    """Calcul de la superficie en hectares d'une parcelle rectangulaire."""
    # Carré d'environ 0.01 degré (~1.1 km de côté ≈ 120-130 hectares)
    polygone = {
        "type": "Polygon",
        "coordinates": [
            [
                [11.50, 3.84],
                [11.51, 3.84],
                [11.51, 3.85],
                [11.50, 3.85],
                [11.50, 3.84],
            ]
        ],
    }
    surface = superficie_ha(polygone)
    assert 110.0 <= surface <= 140.0

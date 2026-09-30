"""Tests d'intégration des champs géométriques spatiaux MySQL sans GeoDjango."""

import pytest
from commun.champs_geo import ChampPoint, ChampPolygone
from django.db import connection
from shapely.geometry import Point, Polygon


@pytest.mark.integration
@pytest.mark.django_db
def test_ecriture_et_lecture_geometrie_mysql() -> None:
    """Écriture puis relecture d'un point et polygone dans MySQL via les champs personnalisés."""
    champ_pt = ChampPoint()
    champ_poly = ChampPolygone()

    # Yaoundé : lat 3.8480, lon 11.5021 (ordre Shapely Python : x=lon, y=lat)
    point_yaounde = Point(11.5021, 3.8480)
    polygone_parcelle = Polygon(
        [
            (11.50, 3.84),
            (11.51, 3.84),
            (11.51, 3.85),
            (11.50, 3.85),
            (11.50, 3.84),
        ]
    )

    wkt_pt = champ_pt.get_prep_value(point_yaounde)
    wkt_poly = champ_poly.get_prep_value(polygone_parcelle)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMPORARY TABLE test_spatial (
                id INT AUTO_INCREMENT PRIMARY KEY,
                position POINT NOT NULL SRID 4326,
                parcelle POLYGON NOT NULL SRID 4326
            )
            """
        )
        try:
            cursor.execute(
                """
                INSERT INTO test_spatial (position, parcelle)
                VALUES (ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326))
                """,
                [wkt_pt, wkt_poly],
            )
            cursor.execute(
                """
                SELECT ST_AsText(position), ST_AsText(parcelle)
                FROM test_spatial
                """
            )
            row = cursor.fetchone()
            assert row is not None

            pt_relit = champ_pt.from_db_value(row[0], None, connection)
            poly_relit = champ_poly.from_db_value(row[1], None, connection)

            assert isinstance(pt_relit, Point)
            assert abs(pt_relit.x - 11.5021) < 1e-4
            assert abs(pt_relit.y - 3.8480) < 1e-4

            assert isinstance(poly_relit, Polygon)
            assert poly_relit.is_valid
        finally:
            cursor.execute("DROP TEMPORARY TABLE IF EXISTS test_spatial")


@pytest.mark.integration
@pytest.mark.django_db
def test_distance_sphere_mysql_yaounde_douala() -> None:
    """Vérification du calcul ST_Distance_Sphere côté MySQL (≈ 193,7 km entre Yaoundé et Douala)."""
    # En MySQL 8 pour SRID 4326, ST_GeomFromText attend (latitude longitude)
    requete = """
        SELECT ST_Distance_Sphere(
            ST_GeomFromText('POINT(3.8480 11.5021)', 4326),
            ST_GeomFromText('POINT(4.0511 9.7679)', 4326)
        ) / 1000.0;
    """
    with connection.cursor() as cursor:
        cursor.execute(requete)
        distance_km = cursor.fetchone()[0]

    # Distance attendue ≈ 193.7 km (±0.5)
    assert 193.0 <= distance_km <= 194.5

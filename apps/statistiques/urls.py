"""Routes du module statistiques ([[03-api-endpoints]] SPEC-11, F-14)."""

from django.urls import re_path

from apps.statistiques.views import (
    VueMesAccompagnements,
    VueMesLivraisons,
    VueMesVentes,
    VuePlateforme,
)

app_name = "statistiques"

urlpatterns = [
    re_path(r"^statistiques/mes-ventes/?$", VueMesVentes.as_view(), name="mes_ventes"),
    re_path(r"^statistiques/mes-livraisons/?$", VueMesLivraisons.as_view(), name="mes_livraisons"),
    re_path(
        r"^statistiques/mes-accompagnements/?$",
        VueMesAccompagnements.as_view(),
        name="mes_accompagnements",
    ),
    re_path(r"^statistiques/plateforme/?$", VuePlateforme.as_view(), name="plateforme"),
]

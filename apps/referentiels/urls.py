"""Routes pour le module referentiels."""

from django.urls import re_path

from apps.referentiels.views import VueReferentielParType

app_name = "referentiels"

urlpatterns = [
    re_path(
        r"^referentiels/(?P<type_ref>[a-zA-Z0-9_-]+)/?$",
        VueReferentielParType.as_view(),
        name="referentiel_detail",
    ),
]

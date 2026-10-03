"""Routes pour le module referentiels (lecture publique + gestion super-admin F-10)."""

from django.urls import re_path

from apps.referentiels.views import VueReferentielParType
from apps.referentiels.views_f10 import (
    VueAdminReferentielDesactiver,
    VueAdminReferentielEntree,
    VueAdminReferentiels,
)

app_name = "referentiels"

urlpatterns = [
    re_path(
        r"^referentiels/(?P<type_ref>[a-zA-Z0-9_-]+)/?$",
        VueReferentielParType.as_view(),
        name="referentiel_detail",
    ),
    # Gestion par le super-admin (prompt F-10 point 3) — jamais de suppression
    re_path(
        r"^admin/referentiels/entrees/(?P<id>[0-9a-f-]{36})/desactiver/?$",
        VueAdminReferentielDesactiver.as_view(),
        name="admin_referentiels_desactiver",
    ),
    re_path(
        r"^admin/referentiels/entrees/(?P<id>[0-9a-f-]{36})/?$",
        VueAdminReferentielEntree.as_view(),
        name="admin_referentiels_entree",
    ),
    re_path(
        r"^admin/referentiels/(?P<type_ref>[a-zA-Z0-9_-]+)/?$",
        VueAdminReferentiels.as_view(),
        name="admin_referentiels_type",
    ),
]

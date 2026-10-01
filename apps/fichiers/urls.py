"""Routes du module fichiers (Fichiers, [[03-api-endpoints]])."""

from django.urls import re_path

from apps.fichiers.views import VueFichierContenu, VueFichierDetail, VueTeleversementFichier

app_name = "fichiers"

urlpatterns = [
    re_path(r"^fichiers/?$", VueTeleversementFichier.as_view(), name="fichiers_upload"),
    re_path(
        r"^fichiers/(?P<id>[0-9a-f-]{36})/?$",
        VueFichierDetail.as_view(),
        name="fichier_detail",
    ),
    re_path(
        r"^fichiers/(?P<id>[0-9a-f-]{36})/contenu/?$",
        VueFichierContenu.as_view(),
        name="fichier_contenu",
    ),
]

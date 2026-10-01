"""Routes du module profils (SPEC-01 profils + décisions admin, F-03 point 3-4)."""

from django.urls import re_path

from apps.profils.views import (
    VueDisponibiliteLivreur,
    VueIngenieurDetail,
    VueJustificatifsMoi,
    VueListeIngenieurs,
    VueProfilIngenieurMoi,
    VueRefuserIngenieur,
    VueValiderIngenieur,
)

app_name = "profils"

urlpatterns = [
    re_path(r"^ingenieurs/moi/profil/?$", VueProfilIngenieurMoi.as_view(), name="moi_profil_ing"),
    re_path(
        r"^ingenieurs/moi/justificatifs/?$",
        VueJustificatifsMoi.as_view(),
        name="moi_justificatifs",
    ),
    re_path(r"^ingenieurs/?$", VueListeIngenieurs.as_view(), name="ingenieurs_liste"),
    re_path(
        r"^ingenieurs/(?P<id>[0-9a-f-]{36})/?$",
        VueIngenieurDetail.as_view(),
        name="ingenieur_detail",
    ),
    re_path(
        r"^livreurs/moi/disponibilite/?$",
        VueDisponibiliteLivreur.as_view(),
        name="livreur_disponibilite",
    ),
    # Décisions d'administration sur les ingénieurs (SPEC-11)
    re_path(
        r"^admin/ingenieurs/(?P<id>[0-9a-f-]{36})/valider/?$",
        VueValiderIngenieur.as_view(),
        name="admin_ingenieur_valider",
    ),
    re_path(
        r"^admin/ingenieurs/(?P<id>[0-9a-f-]{36})/refuser/?$",
        VueRefuserIngenieur.as_view(),
        name="admin_ingenieur_refuser",
    ),
]

"""Routes du module utilisateurs (SPEC-01, prompt F-02 point 5).

Toutes sous le préfixe /api/v1 ajouté par config/urls.py.
"""

from django.urls import re_path

from apps.utilisateurs.views import (
    VueAdresseDetail,
    VueAdresses,
    VueConnexion,
    VueDeconnexion,
    VueDemandeReinitialisation,
    VueInscription,
    VueMoi,
    VueMotDePasse,
    VueRafraichir,
)

app_name = "utilisateurs"

urlpatterns = [
    # Authentification
    re_path(r"^auth/inscription/?$", VueInscription.as_view(), name="inscription"),
    re_path(r"^auth/connexion/?$", VueConnexion.as_view(), name="connexion"),
    re_path(r"^auth/rafraichir/?$", VueRafraichir.as_view(), name="rafraichir"),
    re_path(r"^auth/deconnexion/?$", VueDeconnexion.as_view(), name="deconnexion"),
    re_path(
        r"^auth/demande-reinitialisation/?$",
        VueDemandeReinitialisation.as_view(),
        name="demande_reinitialisation",
    ),
    # Profil personnel
    re_path(r"^utilisateurs/moi/?$", VueMoi.as_view(), name="moi"),
    re_path(r"^utilisateurs/moi/mot-de-passe/?$", VueMotDePasse.as_view(), name="moi_mot_de_passe"),
    # Adresses
    re_path(r"^utilisateurs/moi/adresses/?$", VueAdresses.as_view(), name="moi_adresses"),
    re_path(
        r"^utilisateurs/moi/adresses/(?P<id>[0-9a-f-]{36})/?$",
        VueAdresseDetail.as_view(),
        name="moi_adresse_detail",
    ),
]

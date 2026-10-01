from django.urls import re_path

from apps.commandes.views import (
    VueCommandeAccepter,
    VueCommandeAnnuler,
    VueCommandeDetail,
    VueCommandeHistorique,
    VueCommandePreparer,
    VueCommandeRejeter,
    VueCommandes,
    VueDevis,
    VueMesAchats,
    VueMesVentes,
)

urlpatterns = [
    re_path(r"^commandes/devis/?$", VueDevis.as_view(), name="commande_devis"),
    re_path(r"^commandes/?$", VueCommandes.as_view(), name="commandes"),
    re_path(r"^commandes/mes-achats/?$", VueMesAchats.as_view(), name="mes_achats"),
    re_path(r"^commandes/mes-ventes/?$", VueMesVentes.as_view(), name="mes_ventes"),
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/?$",
        VueCommandeDetail.as_view(),
        name="commande_detail",
    ),
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/accepter/?$",
        VueCommandeAccepter.as_view(),
        name="commande_accepter",
    ),
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/rejeter/?$",
        VueCommandeRejeter.as_view(),
        name="commande_rejeter",
    ),
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/annuler/?$",
        VueCommandeAnnuler.as_view(),
        name="commande_annuler",
    ),
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/preparer/?$",
        VueCommandePreparer.as_view(),
        name="commande_preparer",
    ),
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/historique/?$",
        VueCommandeHistorique.as_view(),
        name="commande_historique",
    ),
]

from django.urls import re_path

from apps.paiements.views import VueInitierPaiement, VuePaiementDetail, VueWebhookNotchpay
from apps.paiements.views_f10 import (
    VueAdminReversements,
    VueAdminSoldes,
    VueSoldesMoi,
)

urlpatterns = [
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/paiements/?$",
        VueInitierPaiement.as_view(),
        name="commande_paiements",
    ),
    re_path(
        r"^paiements/(?P<identifiant>[0-9a-f-]+)/?$",
        VuePaiementDetail.as_view(),
        name="paiement_detail",
    ),
    re_path(
        r"^webhooks/notchpay/?$",
        VueWebhookNotchpay.as_view(),
        name="webhook_notchpay",
    ),
    # Soldes et reversements (Q-11, prompt F-10 point 5)
    re_path(r"^soldes/moi/?$", VueSoldesMoi.as_view(), name="soldes_moi"),
    re_path(r"^admin/soldes/?$", VueAdminSoldes.as_view(), name="admin_soldes"),
    re_path(
        r"^admin/reversements/?$",
        VueAdminReversements.as_view(),
        name="admin_reversements",
    ),
]

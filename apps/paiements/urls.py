from django.urls import re_path

from apps.paiements.views import VueInitierPaiement, VuePaiementDetail, VueWebhookNotchpay

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
]

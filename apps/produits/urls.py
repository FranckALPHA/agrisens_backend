from django.urls import re_path

from apps.produits.views import (
    VueMesProduits,
    VueProduitDetail,
    VueProduitPhotoDetail,
    VueProduitPhotos,
    VueProduitRetirer,
    VueProduits,
    VueProduitStock,
)

urlpatterns = [
    re_path(r"^produits/?$", VueProduits.as_view(), name="produits"),
    re_path(r"^produits/mes-produits/?$", VueMesProduits.as_view(), name="mes_produits"),
    re_path(
        r"^produits/(?P<identifiant>[0-9a-f-]+)/?$",
        VueProduitDetail.as_view(),
        name="produit_detail",
    ),
    re_path(
        r"^produits/(?P<identifiant>[0-9a-f-]+)/stock/?$",
        VueProduitStock.as_view(),
        name="produit_stock",
    ),
    re_path(
        r"^produits/(?P<identifiant>[0-9a-f-]+)/retirer/?$",
        VueProduitRetirer.as_view(),
        name="produit_retirer",
    ),
    re_path(
        r"^produits/(?P<identifiant>[0-9a-f-]+)/photos/?$",
        VueProduitPhotos.as_view(),
        name="produit_photos",
    ),
    re_path(
        r"^produits/(?P<identifiant>[0-9a-f-]+)/photos/(?P<fichier_id>[0-9a-f-]+)/?$",
        VueProduitPhotoDetail.as_view(),
        name="produit_photo_detail",
    ),
]

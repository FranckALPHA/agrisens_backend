"""Routes pour le module administration."""

from django.urls import re_path

from apps.administration.views import VueParametresPublics

app_name = "administration"

urlpatterns = [
    re_path(
        r"^parametres/publics/?$",
        VueParametresPublics.as_view(),
        name="parametres_publics",
    ),
]

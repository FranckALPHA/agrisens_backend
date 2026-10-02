"""Configuration des URLs principales pour AgriSens.

Conventions techniques §4, prompt F-00 points 16 et 17.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.routes_dev import VueDevDeclencherTache, VueDevHorloge
from config.routes_sante import VueSante, VueSantePret

handler404 = "commun.exceptions.vue_erreur_404"
handler500 = "commun.exceptions.vue_erreur_500"

api_v1_patterns: list[URLPattern | URLResolver] = [
    # Santé (liveness et readiness)
    path("sante", VueSante.as_view(), name="sante"),
    path("sante/pret", VueSantePret.as_view(), name="sante_pret"),
    # Modules métier
    path("", include("apps.referentiels.urls")),
    path("", include("apps.administration.urls")),
    path("", include("apps.utilisateurs.urls")),
    path("", include("apps.fichiers.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.profils.urls")),
    path("", include("apps.exploitations.urls")),
    path("", include("apps.produits.urls")),
    path("", include("apps.commandes.urls")),
    path("", include("apps.paiements.urls")),
    path("", include("apps.livraisons.urls")),
]

# Routes réservées aux tests Playwright (uniquement local et test)
if getattr(settings, "ENVIRONNEMENT", "local") in ("local", "test"):
    from apps.paiements.simulateur import urlpatterns_simulateur

    api_v1_patterns += [
        path("dev/horloge", VueDevHorloge.as_view(), name="dev_horloge"),
        path("dev/taches/<str:nom>", VueDevDeclencherTache.as_view(), name="dev_tache"),
        *urlpatterns_simulateur,
    ]

urlpatterns = [
    # Administration Django
    path("admin/", admin.site.urls),
    # Préfixe officiel API v1
    path("api/v1/", include(api_v1_patterns)),
    # Spécification OpenAPI et documentation Swagger
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

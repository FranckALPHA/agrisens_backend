from django.urls import re_path

from apps.plannings.views import (
    VueCorrection,
    VueGenerationDetail,
    VuePlanningsExploitation,
    VueQuota,
    VueRecommandationsExploitation,
)

urlpatterns = [
    re_path(r"^ia/quota/?$", VueQuota.as_view(), name="ia_quota"),
    re_path(
        r"^exploitations/(?P<identifiant>[0-9a-f-]+)/plannings/?$",
        VuePlanningsExploitation.as_view(),
        name="exploitation_plannings",
    ),
    re_path(
        r"^exploitations/(?P<identifiant>[0-9a-f-]+)/recommandations/?$",
        VueRecommandationsExploitation.as_view(),
        name="exploitation_recommandations",
    ),
    re_path(
        r"^generations-ia/(?P<identifiant>[0-9a-f-]+)/?$",
        VueGenerationDetail.as_view(),
        name="generation_ia_detail",
    ),
    re_path(
        r"^generations-ia/(?P<identifiant>[0-9a-f-]+)/corrections/?$",
        VueCorrection.as_view(),
        name="generation_ia_corrections",
    ),
]

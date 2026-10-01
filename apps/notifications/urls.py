"""Routes du module notifications ([[03-api-endpoints]] SPEC-10)."""

from django.urls import re_path

from apps.notifications.views import (
    VueCompteurNotifications,
    VueListeNotifications,
    VueNotificationLue,
    VueToutLireNotifications,
)

app_name = "notifications"

urlpatterns = [
    re_path(r"^notifications/?$", VueListeNotifications.as_view(), name="notifications_liste"),
    re_path(
        r"^notifications/compteur/?$",
        VueCompteurNotifications.as_view(),
        name="notifications_compteur",
    ),
    re_path(
        r"^notifications/tout-lire/?$",
        VueToutLireNotifications.as_view(),
        name="notifications_tout_lire",
    ),
    re_path(
        r"^notifications/(?P<id>[0-9a-f-]{36})/lue/?$",
        VueNotificationLue.as_view(),
        name="notification_lue",
    ),
]

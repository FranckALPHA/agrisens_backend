"""Routes du module conversations ([[03-api-endpoints]] SPEC-10, F-13)."""

from django.urls import re_path

from apps.conversations.views import (
    VueBlocage,
    VueBlocageSuppr,
    VueConversations,
    VueDetailConversation,
    VueMarquerLu,
    VueMessages,
    VueTicket,
)

app_name = "conversations"

urlpatterns = [
    re_path(r"^conversations/?$", VueConversations.as_view(), name="conversations"),
    re_path(
        r"^ws/ticket/?$",
        VueTicket.as_view(),
        name="ws_ticket",
    ),
    re_path(
        r"^conversations/(?P<id>[0-9a-f-]{36})/?$",
        VueDetailConversation.as_view(),
        name="conversations_detail",
    ),
    re_path(
        r"^conversations/(?P<id>[0-9a-f-]{36})/messages/?$",
        VueMessages.as_view(),
        name="conversations_messages",
    ),
    re_path(
        r"^conversations/(?P<id>[0-9a-f-]{36})/lu/?$",
        VueMarquerLu.as_view(),
        name="conversations_lu",
    ),
    re_path(r"^blocages/?$", VueBlocage.as_view(), name="blocages_creer"),
    re_path(
        r"^blocages/(?P<utilisateur_id>[0-9a-f-]{36})/?$",
        VueBlocageSuppr.as_view(),
        name="blocages_suppr",
    ),
]

"""Configuration ASGI pour AgriSens (HTTP + WebSocket).

Le chat temps réel (F-13) ajoute un routeur de protocoles : le trafic HTTP
classique reste géré par Django, le trafic WebSocket est routé vers les
consumers de l'app `conversations`. L'authentification n'est pas faite par un
middleware Channels : le consumer valide lui-même le ticket à usage unique
fourni en query string (`/ws?ticket=…`, docs/03 SPEC-10).
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

# `get_asgi_application()` doit être appelé avant d'importer les modules qui
# touchent aux modèles Django (les consumers), pour que l'app soit prête.
django_asgi_app = get_asgi_application()

from apps.conversations.consommateurs import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)

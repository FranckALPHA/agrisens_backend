"""Configuration ASGI pour AgriSens (HTTP + WebSocket)."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

# Application ASGI de base (Django HTTP)
# Sera enrichie par Django Channels en F-13 pour le chat temps réel.
application = get_asgi_application()

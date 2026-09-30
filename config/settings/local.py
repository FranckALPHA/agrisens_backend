"""Configuration Django pour l'environnement local de développement."""

from config.settings.base import *  # noqa: F403

DEBUG = True

# En local, autorise localhost et 127.0.0.1
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*"]

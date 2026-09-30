"""Configuration Django pour l'environnement de production."""

from config.settings.base import *  # noqa: F403

DEBUG = False
ENVIRONNEMENT = "production"

# Sécurité HTTP en production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True

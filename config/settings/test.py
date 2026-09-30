"""Configuration Django pour l'environnement de test automatisé."""

from config.settings.base import *  # noqa: F403
from config.settings.base import _preparer_db_config

DEBUG = False
ENVIRONNEMENT = "test"

# Utilise la base agrisens_test si spécifiée
if DATABASE_URL_TEST:  # noqa: F405
    DATABASES = {  # noqa: F405
        "default": _preparer_db_config(DATABASE_URL_TEST)  # noqa: F405
    }

DATABASES["default"]["TEST"] = {  # noqa: F405
    "NAME": "agrisens_test",
}

# Hachage rapide pour accélérer la suite de tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Mode factice garanti pour les tests
NOTCHPAY_MODE = "factice"
IA_FOURNISSEUR = "factice"

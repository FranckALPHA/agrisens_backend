"""Configuration Django pour l'environnement de test automatisé."""

from config.settings.base import *  # noqa: F403
from config.settings.base import _preparer_db_config

DEBUG = False
ENVIRONNEMENT = "test"

# Utilise la base agrisens_e2e si spécifiée pour les tests E2E Playwright, sinon agrisens_test
url_db = env.str("DATABASE_URL", default="")  # noqa: F405
if "agrisens_e2e" in url_db:
    DATABASES = {  # noqa: F405
        "default": _preparer_db_config(url_db)  # noqa: F405
    }
elif DATABASE_URL_TEST:  # noqa: F405
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

# Débits très larges : les suites pytest/E2E enchaînent les connexions
# depuis la même IP ; les tests de limitation patchent directement
# ScopedRateThrottle.THROTTLE_RATES (DRF fige cet attribut à l'import).
REST_FRAMEWORK = {**REST_FRAMEWORK}  # noqa: F405
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "connexion": "1000/min",
    "inscription": "1000/min",
    "reinitialisation": "1000/min",
}

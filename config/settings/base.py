"""Configuration de base pour AgriSens (Django 5.2 LTS + DRF).

Conventions techniques §1, §4, §5, §6 et prompt F-00 point 3.
"""

from pathlib import Path

import environ
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Chargement du fichier .env s'il existe
chemin_env = BASE_DIR / ".env"
if chemin_env.exists():
    env.read_env(str(chemin_env))

# --- Environnement et validation des variables obligatoires ---
ENVIRONNEMENT = env.str("ENVIRONNEMENT", default="local")
if ENVIRONNEMENT not in ("local", "test", "staging", "production"):
    raise ImproperlyConfigured(
        f"ENVIRONNEMENT invalide : {ENVIRONNEMENT}. Attendu : local, test, staging ou production."
    )

VARIABLES_OBLIGATOIRES = [
    "DJANGO_SECRET_KEY",
    "DATABASE_URL",
    "JWT_SECRET",
    "CODE_REMISE_CLE",
    "NOTCHPAY_CLE_PUBLIQUE",
    "NOTCHPAY_CLE_PRIVEE",
    "NOTCHPAY_SECRET_WEBHOOK",
    "URL_SIGNEE_SECRET",
]

variables_manquantes = [var for var in VARIABLES_OBLIGATOIRES if var not in env]
if variables_manquantes:
    manquantes_str = ", ".join(variables_manquantes)
    raise ImproperlyConfigured(
        f"Variables d'environnement obligatoires manquantes dans .env : {manquantes_str}"
    )

SECRET_KEY = env.str("DJANGO_SECRET_KEY")

# --- Hôtes et réseau ---
APPEND_SLASH = False
ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# CORS
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS: list[str] = env.list(
    "CORS_ORIGINES",
    default=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
)
# En-têtes non simples autorisés en préflight (F-07 : idempotence navigateur).
CORS_ALLOW_HEADERS = [*default_headers, "Idempotency-Key"]
FRONTEND_URL = env.str("FRONTEND_URL", default="http://localhost:5173")
API_URL_PUBLIQUE = env.str("API_URL_PUBLIQUE", default="http://localhost:8000")


# --- Base de données MySQL 8.4 ---
def _preparer_db_config(url_str: str) -> dict:
    if url_str.startswith("mysql+pymysql://"):
        url_str = "mysql://" + url_str[len("mysql+pymysql://") :]
    config = env.db_url_config(url_str)
    config["ENGINE"] = "django.db.backends.mysql"
    config.setdefault("OPTIONS", {})
    config["OPTIONS"]["charset"] = "utf8mb4"
    config["OPTIONS"]["init_command"] = "SET sql_mode='STRICT_TRANS_TABLES'"
    return config


DATABASE_URL_STR = env.str("DATABASE_URL")
DATABASES = {"default": _preparer_db_config(DATABASE_URL_STR)}

# Base de données pour les tests et E2E
DATABASE_URL_TEST = env.str("DATABASE_URL_TEST", default="")
DATABASE_URL_E2E = env.str("DATABASE_URL_E2E", default="")

# --- Authentification et utilisateur personnalisé ---
AUTH_USER_MODEL = "utilisateurs.Utilisateur"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

# JWT et tokens
JWT_SECRET = env.str("JWT_SECRET")
JWT_DUREE_ACCES_MIN = env.int("JWT_DUREE_ACCES_MIN", default=15)
JWT_DUREE_RAFRAICHISSEMENT_JOURS = env.int("JWT_DUREE_RAFRAICHISSEMENT_JOURS", default=30)
CODE_REMISE_CLE = env.str("CODE_REMISE_CLE")

# Cookie de rafraîchissement (journal Q-25) : Secure seulement dès staging,
# sinon le navigateur rejette le cookie sur http://localhost en local et E2E.
COOKIE_AUTH_SECURE = ENVIRONNEMENT in ("staging", "production")

# --- Intégrations externes ---
# NotchPay
NOTCHPAY_MODE = env.str("NOTCHPAY_MODE", default="factice")
NOTCHPAY_CLE_PUBLIQUE = env.str("NOTCHPAY_CLE_PUBLIQUE")
NOTCHPAY_CLE_PRIVEE = env.str("NOTCHPAY_CLE_PRIVEE")
NOTCHPAY_SECRET_WEBHOOK = env.str("NOTCHPAY_SECRET_WEBHOOK")
# Base vérifiée : https://api.notchpay.co en test comme en production, le
# mode étant déterminé par les clés (sandbox.notchpay.co n'existe pas).
NOTCHPAY_URL_BASE = env.str("NOTCHPAY_URL_BASE", default="https://api.notchpay.co")

# IA Groq
IA_FOURNISSEUR = env.str("IA_FOURNISSEUR", default="factice")
IA_URL_BASE = env.str("IA_URL_BASE", default="https://api.groq.com/openai/v1")
IA_CLE_API = env.str("IA_CLE_API", default="")
IA_MODELE = env.str("IA_MODELE", default="llama-3.3-70b-versatile")

# Stockage et URL signées
STOCKAGE_FICHIERS_CHEMIN = Path(
    env.str("STOCKAGE_FICHIERS_CHEMIN", default=str(BASE_DIR / "stockage"))
)
URL_SIGNEE_SECRET = env.str("URL_SIGNEE_SECRET")

# --- Applications installées ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Bibliothèques tierces
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    # Applications AgriSens
    "apps.utilisateurs.apps.UtilisateursConfig",
    "apps.administration.apps.AdministrationConfig",
    "apps.referentiels.apps.ReferentielsConfig",
    "apps.fichiers.apps.FichiersConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.profils.apps.ProfilsConfig",
    "apps.exploitations.apps.ExploitationsConfig",
    "apps.produits.apps.ProduitsConfig",
    "apps.commandes.apps.CommandesConfig",
    "apps.paiements.apps.PaiementsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "commun.journaux.MiddlewareIdentifiantRequete",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Internationalisation et Heure ---
LANGUAGE_CODE = "fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Fichiers statiques
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Django REST Framework ---
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        # POST /fichiers (F-03) : uploads multipart
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.utilisateurs.authentification.AuthentificationJWT",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_PAGINATION_CLASS": "commun.pagination.PaginationStandard",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "commun.exceptions.gestionnaire_exceptions_drf",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Limitation de débit (conventions §6) : valeurs ajustables par variables d'environnement.
    "DEFAULT_THROTTLE_RATES": {
        "connexion": env.str("DEBIT_CONNEXION", default="10/min"),
        "inscription": env.str("DEBIT_INSCRIPTION", default="10/min"),
        "reinitialisation": env.str("DEBIT_REINITIALISATION", default="5/min"),
    },
}

# --- drf-spectacular (OpenAPI 3) ---
SPECTACULAR_SETTINGS = {
    "TITLE": "API AgriSens",
    "DESCRIPTION": (
        "API REST AgriSens (Django 5.2 LTS + DRF + MySQL spatial). "
        "Mise en relation d'agriculteurs, acheteurs, ingénieurs et livreurs au Cameroun."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    # Noms d'enum explicites : plusieurs champs `role` et `type` portent des
    # jeux de choix distincts (modèle, inscription, création admin ; notif vs
    # justificatif) — sans ceci, drf-spectacular génère des noms hachés.
    "ENUM_NAME_OVERRIDES": {
        "RoleUtilisateurEnum": "apps.utilisateurs.models.RoleUtilisateur",
        "RoleInscriptionEnum": "apps.utilisateurs.serializers.CHOIX_ROLES_INSCRIPTION",
        "RoleAdministrationEnum": "apps.administration.serializers_admin.CHOIX_ROLES_ADMIN",
        "TypeNotificationEnum": "apps.notifications.models.TypeNotification",
        "TypeJustificatifEnum": "apps.profils.models.TypeJustificatif",
        "StatutCommandeEnum": "apps.commandes.models.StatutCommande",
    },
}

# --- Journalisation structurée JSON ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "filtre_masquage": {
            "()": "commun.journaux.FiltreMasquage",
        },
    },
    "formatters": {
        "json_masque": {
            "()": "commun.journaux.FormateurJsonMasque",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json_masque",
            "filters": ["filtre_masquage"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "agrisens": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

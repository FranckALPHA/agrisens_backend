# AgriSens — Backend API

API Django REST Framework connectée à MySQL 8.4 spatial pour la plateforme camerounaise **AgriSens**.

## 1. Prérequis

- **Python 3.12** géré par [`uv`](https://docs.astral.sh/uv/)
- **MySQL 8.4** installé nativement (sans Docker, port `3306`)
- Trois bases de données : `agrisens` (dév), `agrisens_test` (tests), `agrisens_e2e` (tests E2E)

## 2. Installation et démarrage rapide

```powershell
# Cloner et se placer dans le backend
cd agrisens-backend

# Créer le fichier d'environnement
Copy-Item .env.example .env
# Renseigner dans .env la chaîne BASE_DE_DONNEES_URL et les clés secrètes

# Installer l'environnement virtuel et toutes les dépendances
uv sync --all-groups

# Initialiser les bases si premier démarrage
Get-Content scripts/init_bases.sql | mysql -u root -p

# Appliquer les migrations
uv run python manage.py migrate

# Charger les 6 utilisateurs de démonstration
uv run python manage.py seed_demo

# Démarrer le serveur de développement (http://127.0.0.1:8000)
uv run python manage.py runserver

# Dans un terminal séparé : lancer le worker APScheduler
uv run python manage.py worker
```

## 3. Documentation de l'API

- **Swagger UI** : `http://127.0.0.1:8000/api/docs/`
- **Schéma OpenAPI v3** : `http://127.0.0.1:8000/api/schema/`
- **Fichier versionné** : `openapi.json` à la racine du dépôt

## 4. Tests et qualité du code

```powershell
# Exécuter tous les tests (unitaires + intégration)
uv run python -m pytest -v

# Vérification du typage statique
uv run python -m mypy .

# Formatage et linting
uv run ruff check .
uv run ruff format --check .
```

# AGENTS.md — agrisens-backend

Dépôt de l'API AgriSens (FastAPI, SQLAlchemy 2, MySQL 8.4 spatial,
Alembic, JWT, NotchPay, Groq). **Pas de Docker** : MySQL installé
nativement, API et worker lancés avec `uv run` (journal Q-31).

## À lire avant tout

1. `../AGENTS.md` — règles communes, ordre de priorité des documents.
2. `../docs/04-journal-decisions.md` — décisions (prime sur tout).
3. `../docs/01-conventions-techniques.md` — conventions (API, argent,
   géographie, sécurité, git).
4. Le prompt de la feature en cours dans `../docs/prompts/`.

## Environnement local déjà installé (poste de développement)

- MySQL 8.4.11 installé sans Docker dans `C:\Users\jessca johnson\mysql\`,
  écoute sur `127.0.0.1:3306`, démarré automatiquement à l'ouverture de
  session (sinon : `C:\Users\jessca johnson\mysql\demarrer-mysql.cmd`).
- Bases déjà créées : `agrisens`, `agrisens_test`, `agrisens_e2e` ;
  utilisateur applicatif `agrisens` limité à ces trois bases.
- Identifiants et URL de connexion prêtes à copier dans `.env` :
  `C:\Users\jessca johnson\mysql\identifiants.txt` (hors dépôt).
  **Ne jamais les copier dans le code, `.env.example` ou un commit.**
- Python 3.12 installé par `uv` (`uv python find 3.12`).
- `scripts/init_bases.sql` (F-00) reste nécessaire pour les autres
  postes et la CI.

## Rappels propres au backend

- Arborescence imposée : `../docs/10-dossier-final-agent-code.md` §3.
- Logique métier dans `service.py` uniquement ; routes minces.
- Tarifs, taux, délais, quotas : service `parametres`, jamais en dur.
- Paiement confirmé uniquement par webhook NotchPay signé, idempotent.
- Géométries SRID 4326, conversions dans `app/shared/geo.py`.
- Une migration Alembic par changement de schéma.
- Aucun appel réseau réel en test (NotchPay, Groq : adaptateurs factices).
- Après tout changement de route : régénérer `openapi.json` (le
  frontend en dérive ses types).

## Commandes

À compléter par F-00 avec les commandes réelles :

    # créer les bases (scripts/init_bases.sql)
    # démarrer l'API et le worker (uv run …)
    # appliquer les migrations
    # créer une migration
    # charger les données de démonstration
    # tests unitaires / intégration
    # lint, format, types
    # exporter openapi.json

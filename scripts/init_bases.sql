-- AgriSens — création des bases et de l'utilisateur applicatif.
-- À exécuter avec un compte administrateur MySQL :
--   mysql -u root -p < scripts/init_bases.sql
-- Sous Windows comme sous Linux (MySQL 8.4 natif, pas de Docker — journal Q-31).
--
-- ⚠️ Remplacez AGRISENS_MOT_DE_PASSE par un mot de passe fort avant
-- d'exécuter, et ne versionnez jamais ce mot de passe modifié.

CREATE DATABASE IF NOT EXISTS agrisens      CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS agrisens_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS agrisens_e2e  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'agrisens'@'localhost'   IDENTIFIED BY 'AGRISENS_MOT_DE_PASSE';
CREATE USER IF NOT EXISTS 'agrisens'@'127.0.0.1'   IDENTIFIED BY 'AGRISENS_MOT_DE_PASSE';

-- Droits limités aux trois bases de la plateforme : rien d'autre.
GRANT ALL PRIVILEGES ON agrisens.*      TO 'agrisens'@'localhost', 'agrisens'@'127.0.0.1';
GRANT ALL PRIVILEGES ON agrisens_test.* TO 'agrisens'@'localhost', 'agrisens'@'127.0.0.1';
GRANT ALL PRIVILEGES ON agrisens_e2e.*  TO 'agrisens'@'localhost', 'agrisens'@'127.0.0.1';

FLUSH PRIVILEGES;

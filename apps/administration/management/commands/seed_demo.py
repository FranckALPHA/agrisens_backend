"""Commande manage.py seed_demo pour injecter les données de démonstration et tests E2E.

Idempotente : les comptes existants sont remis à l'état attendu
(rôle, statut, mot de passe) à chaque passage. Structure selon
docs/12-plan-tests-playwright.md §3, prompt F-02 point 8 et
e2e/fixtures/roles.ts côté frontend (mêmes téléphones, même mot de passe).

Le mot de passe de démonstration n'est valide que pour les bases
locales / test / e2e — jamais en production (prompt F-02 point 8).
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.utilisateurs.models import RoleUtilisateur, StatutCompte, Utilisateur

# Comptes des personnages (docs/12 §3 + prompt F-02 point 8).
# Nadia est en attente de validation (F-02) ; Clarisse est l'ingénieure
# validée que les features suivantes (F-11 accompagnement) utiliseront.
UTILISATEURS_DEMO: list[dict[str, str]] = [
    {
        "telephone": "+237690000000",
        "nom": "Super Administrateur",
        "role": RoleUtilisateur.SUPER_ADMIN,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "root@agrisens.local",
    },
    {
        "telephone": "+237690000001",
        "nom": "Paul Mbianda",
        "role": RoleUtilisateur.AGRICULTEUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Ouest",
        "email": "paul@agrisens.local",
    },
    {
        "telephone": "+237690000002",
        "nom": "Amina Oumarou",
        "role": RoleUtilisateur.ACHETEUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "amina@agrisens.local",
    },
    {
        "telephone": "+237690000003",
        "nom": "Nadia Kamga",
        "role": RoleUtilisateur.INGENIEUR_AGRONOME,
        "statut_compte": StatutCompte.EN_ATTENTE_VALIDATION,
        "region": "Littoral",
        "email": "nadia@agrisens.local",
    },
    {
        "telephone": "+237690000004",
        "nom": "Jean Tchinda",
        "role": RoleUtilisateur.LIVREUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "jean@agrisens.local",
    },
    {
        "telephone": "+237690000005",
        "nom": "Mireille Ngo",
        "role": RoleUtilisateur.ADMIN,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "mireille@agrisens.local",
    },
    {
        "telephone": "+237690000006",
        "nom": "Clarisse Eboa",
        "role": RoleUtilisateur.INGENIEUR_AGRONOME,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Littoral",
        "email": "clarisse@agrisens.local",
    },
    {
        "telephone": "+237690000007",
        "nom": "Alain Njoya",
        "role": RoleUtilisateur.LIVREUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Ouest",
        "email": "alain@agrisens.local",
    },
    {
        "telephone": "+237690000099",
        "nom": "Compte Suspendu",
        "role": RoleUtilisateur.ACHETEUR,
        "statut_compte": StatutCompte.SUSPENDU,
        "region": "Ouest",
        "email": "suspendu@agrisens.local",
    },
]

MOT_DE_PASSE_DEMO = "Motdepasse123!"


class Command(BaseCommand):
    help = "Injecte les données de démonstration (local / test / e2e uniquement)."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Initialisation des données de démonstration (idempotent)...")

        crees = 0
        existants = 0
        for info in UTILISATEURS_DEMO:
            telephone = info["telephone"]
            utilisateur = Utilisateur.objects.filter(telephone=telephone).first()
            if utilisateur is None:
                utilisateur = Utilisateur.objects.create_user(
                    telephone=telephone,
                    password=MOT_DE_PASSE_DEMO,
                    nom=info["nom"],
                    role=info["role"],
                    statut_compte=info["statut_compte"],
                    region=info["region"],
                    email=info["email"],
                )
                crees += 1
            else:
                # Recalage de l'état attendu : les tests E2E partent du
                # principe que ces comptes ont ces rôles/statuts/mot de passe.
                utilisateur.nom = info["nom"]
                utilisateur.role = info["role"]
                utilisateur.statut_compte = info["statut_compte"]
                utilisateur.region = info["region"]
                utilisateur.email = info["email"]
                utilisateur.set_password(MOT_DE_PASSE_DEMO)
                utilisateur.doit_changer_mot_de_passe = False
                utilisateur.save()
                existants += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Données de démonstration prêtes "
                f"({crees} utilisateurs créés, {existants} recalés)."
            )
        )

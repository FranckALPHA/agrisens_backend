"""Commande manage.py seed_demo pour injecter les données de démonstration et tests E2E.

Idempotente, structure prête selon docs/12-plan-tests-playwright.md §3 et prompt F-00 point 18.
"""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

Utilisateur = get_user_model()

# Données des utilisateurs de démonstration (docs/12 §3)
UTILISATEURS_DEMO = [
    {
        "telephone": "+237670000001",
        "nom": "Paul (Agriculteur)",
        "role": "agriculteur",
        "statut_compte": "actif",
        "region": "centre",
        "email": "paul@agrisens.local",
    },
    {
        "telephone": "+237670000002",
        "nom": "Amina (Acheteuse)",
        "role": "acheteur",
        "statut_compte": "actif",
        "region": "littoral",
        "email": "amina@agrisens.local",
    },
    {
        "telephone": "+237670000003",
        "nom": "Nadia (Ingénieure Agronome)",
        "role": "ingenieur_agronome",
        "statut_compte": "actif",
        "region": "centre",
        "email": "nadia@agrisens.local",
    },
    {
        "telephone": "+237670000004",
        "nom": "Jean (Livreur)",
        "role": "livreur",
        "statut_compte": "actif",
        "region": "littoral",
        "email": "jean@agrisens.local",
    },
    {
        "telephone": "+237670000005",
        "nom": "Mireille (Super Admin)",
        "role": "super_admin",
        "statut_compte": "actif",
        "region": "centre",
        "email": "mireille@agrisens.local",
    },
    {
        "telephone": "+237670000099",
        "nom": "Compte Suspendu",
        "role": "acheteur",
        "statut_compte": "suspendu",
        "region": "ouest",
        "email": "suspendu@agrisens.local",
    },
]

MOT_DE_PASSE_DEMO = "AgriSens2026!"


class Command(BaseCommand):
    help = "Injecte les données de démonstration de base pour les tests et la recette."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Initialisation des données de démonstration (idempotent)...")

        crees = 0
        existants = 0
        for info in UTILISATEURS_DEMO:
            tel = info["telephone"]
            user = Utilisateur.objects.filter(telephone=tel).first()
            if not user:
                user = Utilisateur.objects.create_user(
                    telephone=tel,
                    password=MOT_DE_PASSE_DEMO,
                    nom=info["nom"],
                    role=info["role"],
                    statut_compte=info["statut_compte"],
                    region=info["region"],
                    email=info["email"],
                )
                crees += 1
            else:
                existants += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Données de démonstration prêtes "
                f"({crees} utilisateurs créés, {existants} déjà existants)."
            )
        )

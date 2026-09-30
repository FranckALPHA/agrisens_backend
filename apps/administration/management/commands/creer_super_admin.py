"""Commande manage.py creer_super_admin pour créer un compte super-administrateur.

Conventions techniques §6 et prompt F-00 point 18.
Téléphone et mot de passe demandés de façon interactive, jamais en dur.
"""

import getpass
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

Utilisateur = get_user_model()


class Command(BaseCommand):
    help = "Crée un compte super-administrateur pour AgriSens."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--telephone",
            type=str,
            help="Numéro de téléphone au format E.164 (ex: +237670000000).",
        )
        parser.add_argument(
            "--nom",
            type=str,
            help="Nom complet du super-administrateur.",
        )
        parser.add_argument(
            "--mot-de-passe",
            type=str,
            help="Mot de passe (réservé aux scripts non-interactifs).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        telephone = options.get("telephone")
        nom = options.get("nom")
        mot_de_passe = options.get("mot_de_passe")

        # Saisie interactive si les arguments ne sont pas fournis
        if not telephone:
            telephone = input("Numéro de téléphone (+237...) : ").strip()

        if not telephone:
            raise CommandError("Le numéro de téléphone est obligatoire.")

        if Utilisateur.objects.filter(telephone=telephone).exists():
            raise CommandError(f"Un utilisateur avec le téléphone '{telephone}' existe déjà.")

        if not nom:
            nom = input("Nom complet : ").strip() or "Super Administrateur"

        if not mot_de_passe:
            mot_de_passe = getpass.getpass("Mot de passe (min 8 caractères) : ")
            confirmation = getpass.getpass("Confirmez le mot de passe : ")
            if mot_de_passe != confirmation:
                raise CommandError("Les mots de passe ne correspondent pas.")

        if len(mot_de_passe) < 8:
            raise CommandError("Le mot de passe doit comporter au moins 8 caractères.")

        user = Utilisateur.objects.create_superuser(
            telephone=telephone,
            password=mot_de_passe,
            nom=nom,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Super-administrateur créé avec succès : "
                f"{user.nom} ({user.telephone}) [id={user.id}]"
            )
        )

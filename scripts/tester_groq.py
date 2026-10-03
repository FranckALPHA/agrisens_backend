#!/usr/bin/env python
"""Essai manuel du fournisseur Groq (F-12, SPEC-08) — jamais lancé en CI.

Usage :

    uv run python scripts/tester_groq.py --type planning --exploitation <uuid>
    uv run python scripts/tester_groq.py --type recommandation \
        --exploitation <uuid> --sujet "jaunissement des feuilles"

Ce script charge les settings Django (et donc le `.env`), construit le payload
agronomique exact d'une exploitation réelle via `ServiceEntrees`, puis appelle
directement `FournisseurGroq` et affiche la réponse brute. Il n'écrit rien en
base et n'est importé par aucun test : c'est une simple prise réseau de contrôle
pour valider la clé API et le modèle configurés.

Prérequis : `IA_FOURNISSEUR=groq`, `IA_CLE_API` non vide, `IA_MODELE` défini.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def _configurer_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Essai manuel du fournisseur IA Groq.")
    analyseur.add_argument("--type", dest="type_generation", default="planning",
                           choices=["planning", "recommandation"])
    analyseur.add_argument("--exploitation", required=True, help="UUID de l'exploitation.")
    analyseur.add_argument("--culture", default="tomate")
    analyseur.add_argument("--sujet", default="Conseils de fertilisation de la tomate")
    args = analyseur.parse_args(argv)

    _configurer_django()

    from apps.exploitations.models import Exploitation
    from apps.plannings.fournisseurs import ErreurIA, FournisseurGroq, ReponseIA
    from apps.plannings.prompts import SystemePour
    from apps.plannings.schemas import decrire_schema, schema_pour
    from apps.plannings.services import ServiceEntrees
    from django.conf import settings
    from django.utils import timezone

    if not settings.IA_CLE_API:
        print("IA_CLE_API est vide : renseignez la clé Groq dans le .env.", file=sys.stderr)
        return 2

    exploitation = Exploitation.objects.filter(id=args.exploitation).first()
    if exploitation is None:
        print(f"Exploitation introuvable : {args.exploitation}", file=sys.stderr)
        return 2

    aujourdhui = timezone.localdate()
    if args.type_generation == "planning":
        donnees = {
            "culture": args.culture,
            "periode_debut": aujourdhui,
            "periode_fin": aujourdhui.replace(year=aujourdhui.year + 1),
            "infos_agronomiques": None,
        }
    else:
        donnees = {"sujet": args.sujet, "culture": args.culture, "contexte": None}

    entrees: dict[str, Any] = ServiceEntrees.construire(
        exploitation, args.type_generation, donnees
    )
    utilisateur = json.dumps(
        {"donnees": entrees, "schema_requis": decrire_schema(args.type_generation)},
        ensure_ascii=False,
    )

    fournisseur = FournisseurGroq(
        url_base=settings.IA_URL_BASE,
        cle_api=settings.IA_CLE_API,
        modele=settings.IA_MODELE,
    )
    print(f"Appel de {settings.IA_URL_BASE} (modèle {settings.IA_MODELE})…")
    resultat = fournisseur.generer(
        systeme=SystemePour(args.type_generation),
        utilisateur=utilisateur,
        schema=schema_pour(args.type_generation),
        delai_s=60,
    )

    if isinstance(resultat, ErreurIA):
        print(f"ÉCHEC [{resultat.code}] : {resultat.message}", file=sys.stderr)
        return 1

    assert isinstance(resultat, ReponseIA)
    valide = schema_pour(args.type_generation).model_validate(resultat.contenu)
    print("Succès — réponse validée par le schéma Pydantic :")
    print(json.dumps(valide.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

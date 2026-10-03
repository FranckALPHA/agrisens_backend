"""Migration de données : offres d'abonnement initiales (P-05, D-04).

En V1 seule l'offre `gratuit` sert (quotas IA) ; `standard` et `premium` sont
enregistrées mais non vendues (abonnements payants « plus tard »).
"""

import uuid

from django.db import migrations
from django.utils import timezone

_OFFRES = [
    {
        "code": "gratuit",
        "nom": "Gratuit",
        "prix_mensuel_fcfa": 0,
        "quota_planning_mois": 2,
        "quota_recommandation_mois": 5,
        "fonctionnalites": {"services": "Fonctions essentielles"},
    },
    {
        "code": "standard",
        "nom": "Standard",
        "prix_mensuel_fcfa": 5_000,
        "quota_planning_mois": 10,
        "quota_recommandation_mois": 30,
        "fonctionnalites": {"services": "Historique étendu et priorité normale"},
    },
    {
        "code": "premium",
        "nom": "Premium",
        "prix_mensuel_fcfa": 10_000,
        "quota_planning_mois": 30,
        "quota_recommandation_mois": 100,
        "fonctionnalites": {"services": "Priorité élevée et accompagnement avancé"},
    },
]


def inserer_offres(apps, schema_editor):
    OffreAbonnement = apps.get_model("administration", "OffreAbonnement")
    maintenant = timezone.now()
    objets = [
        OffreAbonnement(
            id=str(uuid.uuid4()),
            date_effet=maintenant,
            active=True,
            **offre,
        )
        for offre in _OFFRES
    ]
    OffreAbonnement.objects.bulk_create(objets, ignore_conflicts=True)


def supprimer_offres(apps, schema_editor):
    OffreAbonnement = apps.get_model("administration", "OffreAbonnement")
    OffreAbonnement.objects.filter(code__in=[o["code"] for o in _OFFRES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0004_offreabonnement"),
    ]

    operations = [
        migrations.RunPython(inserer_offres, reverse_code=supprimer_offres),
    ]

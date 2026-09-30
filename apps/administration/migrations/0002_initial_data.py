"""Migration de données initiale pour les paramètres de plateforme (Journal §4 et F-00 point 7)."""

import uuid

from django.db import migrations
from django.utils import timezone

from apps.administration.registre import valeurs_de_demarrage


def inserer_parametres(apps, schema_editor):
    ParametrePlateforme = apps.get_model("administration", "ParametrePlateforme")
    demarrages = valeurs_de_demarrage()
    maintenant = timezone.now()

    objets = []
    for cle, valeur in demarrages.items():
        objets.append(
            ParametrePlateforme(
                id=str(uuid.uuid4()),
                cle=cle,
                valeur=valeur,
                date_effet=maintenant,
                motif="Valeur de démarrage initiale (Journal de décisions §4)",
            )
        )
    ParametrePlateforme.objects.bulk_create(objets, ignore_conflicts=True)


def supprimer_parametres(apps, schema_editor):
    ParametrePlateforme = apps.get_model("administration", "ParametrePlateforme")
    cles = list(valeurs_de_demarrage().keys())
    ParametrePlateforme.objects.filter(cle__in=cles).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            inserer_parametres,
            reverse_code=supprimer_parametres,
        ),
    ]

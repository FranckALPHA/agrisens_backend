"""Migration de données initiale pour les référentiels (Q-10 et F-00 point 7)."""

import uuid

from django.db import migrations

from apps.referentiels.donnees import REFERENTIELS_INITIAUX


def inserer_referentiels(apps, schema_editor):
    Referentiel = apps.get_model("referentiels", "Referentiel")

    objets = []
    for type_ref, elements in REFERENTIELS_INITIAUX.items():
        for ordre, (code, libelle) in enumerate(elements, start=1):
            objets.append(
                Referentiel(
                    id=str(uuid.uuid4()),
                    type=type_ref,
                    code=code,
                    libelle=libelle,
                    ordre=ordre,
                    actif=True,
                )
            )
    Referentiel.objects.bulk_create(objets, ignore_conflicts=True)


def supprimer_referentiels(apps, schema_editor):
    Referentiel = apps.get_model("referentiels", "Referentiel")
    for type_ref in REFERENTIELS_INITIAUX:
        Referentiel.objects.filter(type=type_ref).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("referentiels", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            inserer_referentiels,
            reverse_code=supprimer_referentiels,
        ),
    ]

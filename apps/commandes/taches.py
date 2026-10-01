"""Tâche planifiée des commandes : expiration des commandes dépassées (Q-03).

Exécutée toutes les 5 minutes par le worker (`manage.py worker`) et à la
demande en test via `POST /dev/taches/expirer_commandes`.
"""

from commun.horloge import maintenant
from django.db import transaction

from apps.commandes.models import Commande, StatutCommande
from apps.commandes.services import ServiceCommande


def expirer_commandes() -> int:
    """Annule les commandes `en_attente`/`acceptee` dont `expire_le` est passé.

    Le stock réservé est libéré, les deux parties sont notifiées.
    Renvoie le nombre de commandes expirées.
    """
    selection = Commande.objects.filter(
        statut__in=[StatutCommande.EN_ATTENTE, StatutCommande.ACCEPTEE],
        expire_le__lte=maintenant(),
    )
    expirees = 0
    for commande in selection.iterator():
        with transaction.atomic():
            fraiche = Commande.objects.select_for_update().filter(id=commande.id).first()
            if (
                fraiche is None
                or fraiche.statut not in (StatutCommande.EN_ATTENTE, StatutCommande.ACCEPTEE)
                or (fraiche.expire_le is not None and fraiche.expire_le > maintenant())
            ):
                continue
            ServiceCommande.expirer(fraiche)
            expirees += 1
    return expirees

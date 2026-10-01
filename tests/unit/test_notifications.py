"""Tests unitaires du service de notification (prompt F-03 backend point 6).

`notifier` est le point d'entrée unique de toutes les features :
création, type contrôlé, lecture idempotente, marquage global.
"""

from typing import Any

import pytest
from apps.notifications.models import Notification, TypeNotification
from apps.notifications.services import marquer_lue, notifier, tout_marquer_lu
from commun.exceptions import ValidationMetier


@pytest.mark.unit
def test_notifier_cree_une_notification_non_lue(db: Any, fabrique_utilisateur: Any) -> None:
    nadia = fabrique_utilisateur(role="ingenieur_agronome", statut_compte="en_attente_validation")
    notification = notifier(
        nadia,
        TypeNotification.ADMINISTRATION,
        "Votre profil ingénieur est validé",
        "Vous pouvez maintenant utiliser toutes les fonctions de votre espace.",
        lien="/ingenieur",
    )
    assert notification.pk is not None
    assert notification.destinataire_id == nadia.id
    assert notification.non_lue is True
    assert Notification.objects.filter(destinataire=nadia).count() == 1


@pytest.mark.unit
def test_notifier_type_inconnu_est_une_erreur(db: Any, fabrique_utilisateur: Any) -> None:
    nadia = fabrique_utilisateur(role="ingenieur_agronome")
    with pytest.raises(ValidationMetier) as exc:
        notifier(nadia, "teleportation", "Jamais", "")
    assert exc.value.code == "type_notification_inconnu"
    assert Notification.objects.count() == 0


@pytest.mark.unit
def test_marquer_lue_idempotente(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    notification = notifier(amina, TypeNotification.COMMANDE, "Commande expédiée")
    marquer_lue(notification)
    premiere_date = notification.lue_le
    assert premiere_date is not None
    marquer_lue(notification)
    assert notification.lue_le == premiere_date


@pytest.mark.unit
def test_tout_marquer_lu_ne_touche_pas_autrui(db: Any, fabrique_utilisateur: Any) -> None:
    amina = fabrique_utilisateur(role="acheteur")
    paul = fabrique_utilisateur(role="agriculteur")
    notifier(amina, TypeNotification.COMMANDE, "Une")
    notifier(amina, TypeNotification.PAIEMENT, "Deux")
    notifier(paul, TypeNotification.COMMANDE, "Pour Paul")
    assert tout_marquer_lu(amina) == 2
    assert Notification.objects.filter(destinataire=amina, lue_le__isnull=True).count() == 0
    assert Notification.objects.filter(destinataire=paul, lue_le__isnull=True).count() == 1

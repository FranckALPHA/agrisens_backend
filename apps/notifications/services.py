"""Service de notification — point d'entrée unique de toutes les features.

`notifier(destinataire, type, titre, contenu, lien)` (prompt F-03 backend
point 2). Les features postérieures (commandes, paiements, livraisons…)
importent cette fonction et ne créent jamais de Notification directement.
La livraison se fait par interrogation périodique côté client en attendant
le canal WebSocket de F-13.
"""

from typing import Any

from commun.exceptions import ValidationMetier
from commun.horloge import maintenant

from apps.notifications.models import Notification, TypeNotification


def notifier(
    destinataire: Any,
    type: str,
    titre: str,
    contenu: str = "",
    lien: str | None = None,
) -> Notification:
    """Crée une notification interne pour `destinataire` (instance Utilisateur).

    Lève ValidationMetier si le type n'appartient pas aux catégories FE-05 :
    une feature qui notifie d'un type inconnu est une erreur de programmation.
    """
    if type not in TypeNotification.values:
        raise ValidationMetier(
            code="type_notification_inconnu",
            message=f"Type de notification inconnu : « {type} ».",
            details={"champ": "type", "valeurs_autorisees": list(TypeNotification.values)},
        )
    return Notification.objects.create(
        destinataire=destinataire,
        type=type,
        titre=titre[:150],
        contenu=(contenu or "")[:500],
        lien=lien[:500] if lien else None,
    )


def marquer_lue(notification: Notification) -> Notification:
    """Marque une notification comme lue (idempotent)."""
    if notification.lue_le is None:
        notification.lue_le = maintenant()
        notification.save(update_fields=["lue_le", "maj_le"])
    return notification


def tout_marquer_lu(destinataire: Any) -> int:
    """Marque toutes les non lues du destinataire ; renvoie le nombre touché."""
    return Notification.objects.filter(destinataire=destinataire, lue_le__isnull=True).update(
        lue_le=maintenant()
    )

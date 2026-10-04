"""Règle d'accès aux fichiers de contexte `chat` (F-13).

Inscrite dans `apps.fichiers.acces.REGISTRE_ACCESS` au `ready()` de l'app. Un
fichier joint à un message est lisible par son propriétaire, par les admins
(règle par défaut reprise) et surtout par **tout participant de la
conversation** où ce message apparaît — jamais par un tiers.
"""

from apps.fichiers.models import Fichier


def verifier_acces_chat(fichier: Fichier, utilisateur: object | None) -> bool:
    """Autorise les participants de la conversation porteuse du fichier."""
    if utilisateur is None:
        return False
    utilisateur_id = getattr(utilisateur, "id", None)
    if utilisateur_id is None:
        return False
    # Le propriétaire (l'émetteur) et les admins gardent l'accès.
    role = getattr(utilisateur, "role", None)
    if fichier.proprietaire_id == utilisateur_id or role in ("admin", "super_admin"):
        return True
    # Un destinataire qui n'a pas envoyé le fichier y accède s'il est
    # participant de la conversation contenant un message avec ce fichier.
    from apps.conversations.models import Participant

    return Participant.objects.filter(
        utilisateur_id=utilisateur_id,
        conversation__messages__fichier_id=fichier.id,
    ).exists()

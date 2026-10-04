"""Configuration de l'application conversations (SPEC-10 — chat, F-13)."""

from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.conversations"
    label = "conversations"
    verbose_name = "Conversations et messagerie"

    def ready(self) -> None:
        # Déclare la règle d'accès aux fichiers de contexte `chat` : seuls les
        # participants d'une conversation peuvent lire une pièce jointe qui y a
        # été envoyée (en plus du propriétaire et des admins). L'import est ici
        # pour éviter tout import circulaire au chargement des modèles.
        from apps.conversations.acces import verifier_acces_chat
        from apps.fichiers.acces import inscrire_verificateur
        from apps.fichiers.models import ContexteFichier

        inscrire_verificateur(ContexteFichier.CHAT, verifier_acces_chat)

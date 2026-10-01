"""Modèle FICHIER ([[02-modele-donnees]] §2.8, prompt F-03 backend point 1).

- `cle_stockage` : chemin interne jamais exposé tel quel à l'API ;
- `public` : true seulement pour les photos de produits ;
- le type MIME est vérifié sur le **contenu** par le service (bibliothèque
  `filetype`), pas sur l'extension ni sur l'en-tête du client.
"""

from commun.modeles import ModeleBase
from django.conf import settings
from django.db import models


class ContexteFichier(models.TextChoices):
    """Contexte d'usage d'un fichier ; décide qui a le droit de le relire."""

    PRODUIT = "produit", "Photo de produit"
    SIGNALEMENT = "signalement", "Pièce d'un signalement"
    ACCOMPAGNEMENT = "accompagnement", "Fichier d'un accompagnement"
    JUSTIFICATIF = "justificatif", "Justificatif professionnel"
    CHAT = "chat", "Fichier de conversation"
    INCIDENT = "incident", "Photo d'incident de livraison"


class Fichier(ModeleBase):
    """Fichier binaire stocké derrière l'adaptateur `StockageFichiers`."""

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fichiers",
        help_text="Utilisateur qui a téléversé le fichier",
    )
    contexte = models.CharField(
        max_length=30,
        choices=ContexteFichier.choices,
        help_text="Usage annoncé à l'upload ; détermine les règles d'accès",
    )
    cle_stockage = models.CharField(
        max_length=255,
        unique=True,
        help_text="Chemin interne dans le stockage, jamais exposé tel quel",
    )
    nom_original = models.CharField(
        max_length=255,
        help_text="Nom donné par le client, affiché pour rappel uniquement",
    )
    type_mime = models.CharField(
        max_length=100,
        help_text="Type détecté sur le contenu (filetype), pas sur l'extension",
    )
    taille_octets = models.IntegerField(
        help_text="Taille en octets après traitement (EXIF retiré)",
    )
    public = models.BooleanField(
        default=False,
        help_text="True seulement pour les photos de produits",
    )

    class Meta:
        db_table = "fichier"
        verbose_name = "Fichier"
        verbose_name_plural = "Fichiers"
        ordering = ["-cree_le"]

    def __str__(self) -> str:
        return f"Fichier {self.id[:8]}… [{self.contexte}] {self.type_mime}"

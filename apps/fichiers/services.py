"""Logique métier du module fichiers (prompt F-03 backend point 1).

Règles appliquées ici, jamais dans les vues :
- type vérifié sur le **contenu** (bibliothèque `filetype`), pas sur
  l'extension ni sur l'en-tête annoncé par le client (Q-14) ;
- tailles maximum lues dans les paramètres `fichiers.*` (aucun nombre en dur) ;
- métadonnées EXIF supprimées des images (Pillow) ;
- nom de stockage aléatoire, chemin interne jamais exposé ;
- `GET /fichiers/{id}` : redirection 302 vers une URL signée HMAC de courte
  durée (clé `URL_SIGNEE_SECRET`, durée paramètre `fichiers.duree_url_signee_s`,
  hypothèse Q-35) ;
- accès régi par le registre `apps.fichiers.acces` ; ressource non visible → 404.
"""

import hashlib
import hmac
import io
from datetime import timedelta
from typing import Any

from commun.exceptions import ErreurMetier, NonTrouve, ValidationMetier
from commun.horloge import maintenant
from django.conf import settings
from django.db import transaction
from filetype import match as filetype_match
from PIL import Image, JpegImagePlugin  # noqa: F401  (enregistre le format JPEG)
from rest_framework import status

from apps.administration.services import parametres
from apps.fichiers.acces import acces_autorise
from apps.fichiers.models import ContexteFichier, Fichier
from apps.fichiers.stockage import StockageDisque, StockageFichiers, generer_cle_stockage

# Types acceptés (journal Q-14) : images jpeg/png/webp, documents PDF.
TYPES_IMAGE = {
    "image/jpeg": ("jpg", "JPEG"),
    "image/png": ("png", "PNG"),
    "image/webp": ("webp", "WEBP"),
}
TYPES_DOCUMENT = {
    "application/pdf": ("pdf",),
}

CHEMIN_TELECHARGEMENT = "/api/v1/fichiers"


def _detecter_type(contenu: bytes) -> str:
    """Type MIME déterminé à partir des octets du fichier (jamais de l'extension)."""
    kind = filetype_match(contenu)
    return kind.mime if kind else ""


def _nettoyer_image(contenu: bytes, type_mime: str, nom_original: str) -> bytes:
    """Ré-encode l'image sans métadonnées EXIF/ICC (Pillow)."""
    try:
        image = Image.open(io.BytesIO(contenu))
        image.load()
    except Exception:
        raise ValidationMetier(
            code="fichier_image_invalide",
            message=f"Image illisible ou corrompue : {nom_original}.",
            details={"champ": "fichier"},
        ) from None
    extension, _ = TYPES_IMAGE[type_mime]
    format_img = TYPES_IMAGE[type_mime][1]
    sortie = io.BytesIO()
    params: dict[str, Any] = {}
    if format_img == "WEBP":
        params["quality"] = 90
    image.save(sortie, format=format_img, exif=b"", **params)
    return sortie.getvalue()


class ServiceFichiers:
    """Téléversement, redistribution signée et suppression des fichiers."""

    def __init__(self, stockage: StockageFichiers | None = None) -> None:
        self.stockage: StockageFichiers = stockage or StockageDisque()

    # --- Téléversement -----------------------------------------------------

    @transaction.atomic
    def televerser(
        self,
        *,
        proprietaire: Any,
        contexte: str,
        fichier: Any,
    ) -> Fichier:
        """Valide (type sur contenu, taille paramétrée), retire l'EXIF, stocke."""
        if contexte not in ContexteFichier.values:
            raise ValidationMetier(
                code="contexte_inconnu",
                message=f"Contexte de fichier inconnu : « {contexte} ».",
                details={
                    "champ": "contexte",
                    "valeurs_autorisees": list(ContexteFichier.values),
                },
            )

        contenu: bytes = fichier.read()
        nom_original = (getattr(fichier, "name", "") or "fichier")[:255]
        type_mime = _detecter_type(contenu)

        if type_mime in TYPES_IMAGE:
            limite_octets = parametres.obtenir("fichiers.image_max_mo") * 1024 * 1024
            if len(contenu) > limite_octets:
                raise self._erreur_trop_lourd(nom_original, type_mime, limite_octets)
            contenu = _nettoyer_image(contenu, type_mime, nom_original)
            if len(contenu) > limite_octets:
                raise self._erreur_trop_lourd(nom_original, type_mime, limite_octets)
            extension = TYPES_IMAGE[type_mime][0]
        elif type_mime in TYPES_DOCUMENT:
            limite_octets = parametres.obtenir("fichiers.document_max_mo") * 1024 * 1024
            if len(contenu) > limite_octets:
                raise self._erreur_trop_lourd(nom_original, type_mime, limite_octets)
            extension = TYPES_DOCUMENT[type_mime][0]
        else:
            raise ValidationMetier(
                code="type_fichier_non_autorise",
                message=(
                    "Type de fichier non autorisé : seules les images "
                    "jpeg/png/webp et les documents pdf sont acceptés."
                ),
                details={"champ": "fichier", "nom": nom_original},
            )

        instant = maintenant()
        cle = generer_cle_stockage(extension, instant)
        self.stockage.enregistrer(cle, contenu)

        # Une photo de produit ne devient publique qu'une fois publiée au
        # catalogue (F-05) ; à l'upload elle reste privée pour son auteur.
        return Fichier.objects.create(
            proprietaire=proprietaire,
            contexte=contexte,
            cle_stockage=cle,
            nom_original=nom_original,
            type_mime=type_mime,
            taille_octets=len(contenu),
            public=False,
        )

    @staticmethod
    def _erreur_trop_lourd(nom: str, type_mime: str, limite_octets: int) -> ValidationMetier:
        return ValidationMetier(
            code="fichier_trop_volumineux",
            message=(
                f"Fichier trop volumineux : la limite pour ce type est de "
                f"{limite_octets // (1024 * 1024)} Mo."
            ),
            details={
                "champ": "fichier",
                "nom": nom,
                "type_mime": type_mime,
                "limite_octets": limite_octets,
            },
        )

    # --- Lecture et redistribution ------------------------------------------

    @staticmethod
    def obtenir(fichier_id: str) -> Fichier:
        fichier = Fichier.objects.filter(id=fichier_id).first()
        if fichier is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            )
        return fichier

    def verifier_acces(self, fichier: Fichier, utilisateur: Any) -> None:
        """404 (jamais 403) si la règle du contexte n'autorise pas l'accès."""
        if not acces_autorise(fichier, utilisateur):
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            )

    def construire_url_signee(self, fichier: Fichier) -> str:
        """URL de téléchargement signée HMAC, valable `fichiers.duree_url_signee_s`."""
        expire_le = maintenant() + timedelta(
            seconds=parametres.obtenir("fichiers.duree_url_signee_s")
        )
        expire_ts = int(expire_le.timestamp())
        signature = self._signature(fichier.id, expire_ts)
        base = f"{settings.API_URL_PUBLIQUE}{CHEMIN_TELECHARGEMENT}/{fichier.id}/contenu"
        return f"{base}?expire={expire_ts}&signature={signature}"

    @staticmethod
    def _signature(fichier_id: str, expire_ts: int) -> str:
        message = f"{fichier_id}:{expire_ts}".encode()
        return hmac.new(
            settings.URL_SIGNEE_SECRET.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()

    def valider_url_signee(self, fichier_id: str, expire_ts: str, signature: str) -> Fichier:
        """Vérifie signature et expiration ; 404 si signature invalide (silencieux)."""
        fichier = self.obtenir(fichier_id)
        try:
            ts = int(expire_ts)
        except (TypeError, ValueError):
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            ) from None
        attendu = self._signature(fichier.id, ts)
        if not hmac.compare_digest(attendu, signature or ""):
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            )
        if ts < int(maintenant().timestamp()):
            raise ErreurMetier(
                code="url_expiree",
                message="Ce lien de téléchargement a expiré, demandez-le à nouveau.",
                statut_http=status.HTTP_410_GONE,
            )
        return fichier

    def ouvrir_contenu(self, fichier: Fichier) -> Any:
        """Retourne le binaire stocké ; 404 si la clé a disparu du stockage."""
        try:
            return self.stockage.ouvrir(fichier.cle_stockage)
        except FileNotFoundError:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            ) from None

    @transaction.atomic
    def supprimer(self, fichier: Fichier) -> None:
        self.stockage.supprimer(fichier.cle_stockage)
        fichier.delete()


# Instance par défaut (stockage disque) réutilisable par les vues et les services.
service_fichiers = ServiceFichiers()

"""Tests unitaires du module fichiers (prompt F-03 backend point 6).

Couvre : type vérifié sur le contenu (faux .jpg), taille paramétrée,
retrait EXIF, URL signée HMAC (expiration, signature invalide) et le
registre d'accès par contexte.
"""

import io
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from apps.fichiers.acces import acces_autorise
from apps.fichiers.models import ContexteFichier
from apps.fichiers.services import ServiceFichiers, _nettoyer_image
from apps.fichiers.stockage import StockageDisque
from commun.exceptions import ErreurMetier, NonTrouve, ValidationMetier
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

# En-tête MZ (exécutable Windows) déguisé en .jpg : le contenu, pas
# l'extension, décide du refus (règle Q-14).
_OCTETS_EXECUTABLE = b"MZ\x90\x00" + b"\x00" * 512


def _jpeg(bruit: bool = False, taille_px: int = 60, exif: bool = False) -> bytes:
    """JPEG réel généré par Pillow ; `exif=True` y inscrit un modèle d'appareil."""
    if bruit:
        # Bruit blanc : le JPEG résultant reste volumineux (test de taille).
        image = Image.frombytes(
            "RGB", (taille_px, taille_px), os.urandom(taille_px * taille_px * 3)
        )
    else:
        image = Image.new("RGB", (taille_px, taille_px))
    sortie = io.BytesIO()
    if exif:
        meta = image.getexif()
        meta[272] = "Appareil secret de test"  # Make
        image.save(sortie, format="JPEG", exif=meta.tobytes())
    else:
        image.save(sortie, format="JPEG")
    return sortie.getvalue()


@pytest.fixture
def stockage(tmp_path: Any) -> StockageDisque:
    return StockageDisque(tmp_path)


@pytest.fixture
def service(stockage: StockageDisque) -> ServiceFichiers:
    return ServiceFichiers(stockage=stockage)


@pytest.mark.unit
def test_faux_jpg_executable_refuse_sur_le_contenu(
    db: Any, service: ServiceFichiers, fabrique_utilisateur: Any
) -> None:
    """.jpg dont le contenu est un exécutable : refusé (le type vient des octets)."""
    paul = fabrique_utilisateur(role="agriculteur")
    with pytest.raises(ValidationMetier) as exc:
        service.televerser(
            proprietaire=paul,
            contexte=ContexteFichier.PRODUIT,
            fichier=SimpleUploadedFile("fautif.jpg", _OCTETS_EXECUTABLE, content_type="image/jpeg"),
        )
    assert exc.value.code == "type_fichier_non_autorise"


@pytest.mark.unit
def test_image_trop_volumineuse_refusee(
    db: Any, monkeypatch: pytest.MonkeyPatch, service: ServiceFichiers, fabrique_utilisateur: Any
) -> None:
    """Taille vérifiée sur le paramètre `fichiers.image_max_mo`, jamais en dur."""
    from apps.administration.services import parametres

    monkeypatch.setattr(parametres, "obtenir", lambda cle: 1)  # 1 Mo pour le test
    bruit = _jpeg(bruit=True, taille_px=1600)  # JPEG de bruit ~1,5 Mo > limite 1 Mo
    paul = fabrique_utilisateur(role="agriculteur")
    with pytest.raises(ValidationMetier) as exc:
        service.televerser(
            proprietaire=paul,
            contexte=ContexteFichier.PRODUIT,
            fichier=SimpleUploadedFile("lourde.jpg", bruit, content_type="image/jpeg"),
        )
    assert exc.value.code == "fichier_trop_volumineux"
    assert exc.value.details["limite_octets"] == 1024 * 1024


@pytest.mark.unit
def test_exif_supprime_des_images(
    db: Any, service: ServiceFichiers, stockage: StockageDisque, fabrique_utilisateur: Any
) -> None:
    """Les métadonnées EXIF (appareil, géoloc.) sont retirées au stockage."""
    amina = fabrique_utilisateur(role="acheteur")
    fichier = service.televerser(
        proprietaire=amina,
        contexte=ContexteFichier.PRODUIT,
        fichier=SimpleUploadedFile("avec-exif.jpg", _jpeg(exif=True), content_type="image/jpeg"),
    )
    contenu_stocke = stockage.ouvrir(fichier.cle_stockage).read()
    image = Image.open(io.BytesIO(contenu_stocke))
    assert "Appareil secret" not in str(image.getexif())


@pytest.mark.unit
def test_pdf_accepte_en_contexte_justificatif(
    db: Any, service: ServiceFichiers, fabrique_utilisateur: Any
) -> None:
    nadia = fabrique_utilisateur(role="ingenieur_agronome", statut_compte="en_attente_validation")
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    fichier = service.televerser(
        proprietaire=nadia,
        contexte=ContexteFichier.JUSTIFICATIF,
        fichier=SimpleUploadedFile("diplome.pdf", pdf, content_type="application/pdf"),
    )
    assert fichier.type_mime == "application/pdf"
    assert fichier.public is False
    assert fichier.cle_stockage != "diplome.pdf"  # nom aléatoire, jamais le nom client


@pytest.mark.unit
def test_url_signee_expiree_410_signature_invalide_404(
    db: Any, service: ServiceFichiers, fabrique_utilisateur: Any
) -> None:
    """URL HMAC : signature d'un lien expiré → 410 ; signature falsifiée → 404."""
    amina = fabrique_utilisateur(role="acheteur")
    fichier = service.televerser(
        proprietaire=amina,
        contexte=ContexteFichier.PRODUIT,
        fichier=SimpleUploadedFile("tomate.jpg", _jpeg(), content_type="image/jpeg"),
    )
    url = service.construire_url_signee(fichier)
    assert "expire=" in url and "signature=" in url

    # Lien expiré : on régénère une signature authentique pour un timestamp passé.
    passe = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())
    signature_passe = ServiceFichiers._signature(fichier.id, passe)
    with pytest.raises(ErreurMetier) as exc:
        service.valider_url_signee(fichier.id, str(passe), signature_passe)
    assert exc.value.code == "url_expiree"
    assert exc.value.statut_http == 410

    with pytest.raises(NonTrouve):
        service.valider_url_signee(fichier.id, str(passe), "0123deadbeef")


@pytest.mark.unit
def test_registre_acces_justificatif_proprietaire_et_admin_seuls(
    db: Any, service: ServiceFichiers, fabrique_utilisateur: Any
) -> None:
    nadia = fabrique_utilisateur(role="ingenieur_agronome", statut_compte="en_attente_validation")
    autre = fabrique_utilisateur(role="agriculteur")
    admin = fabrique_utilisateur(role="admin")
    fichier = service.televerser(
        proprietaire=nadia,
        contexte=ContexteFichier.JUSTIFICATIF,
        fichier=SimpleUploadedFile("cin.jpg", _jpeg(), content_type="image/jpeg"),
    )
    assert acces_autorise(fichier, nadia) is True
    assert acces_autorise(fichier, admin) is True
    assert acces_autorise(fichier, autre) is False
    assert acces_autorise(fichier, None) is False


@pytest.mark.unit
def test_nettoyer_image_corrompue_erreur_metier() -> None:
    """Une image déclarée jpg mais illisible est refusée avant stockage."""
    with pytest.raises(ValidationMetier) as exc:
        _nettoyer_image(b"\xff\xd8\xff" + b"corrompu", "image/jpeg", "casse.jpg")
    assert exc.value.code == "fichier_image_invalide"

"""Tests d'intégration API F-03 : fichiers, notifications, profils, admin.

Parcours HTTP complets (prompt F-03 backend point 6) :
- upload falsifié (exécutable déguisé en .jpg), taille dépassée ;
- justificatif d'autrui → 404 ; URL signée expirée → 410 ;
- validation réservée aux admins ; suspension qui révoque les sessions ;
- notification créée et parcours FE-05 (liste, compteur, lue, tout-lire) ;
- profil ingénieur, disponibilité livreur, mot de passe temporaire Q-23.
"""

import io
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import pytest
from apps.fichiers.services import ServiceFichiers
from apps.fichiers.stockage import StockageDisque
from apps.notifications.models import Notification
from apps.notifications.services import notifier
from apps.profils.models import IngenieurProfil, StatutValidation
from apps.utilisateurs.models import RoleUtilisateur, StatutCompte
from apps.utilisateurs.services import NOM_COOKIE_RAFRAICHISSEMENT
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

API = "/api/v1"
FICHIERS = f"{API}/fichiers"
NOTIFICATIONS = f"{API}/notifications"
CONNEXION = f"{API}/auth/connexion"
RAFRAICHIR = f"{API}/auth/rafraichir"

PDF_VALIDE = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _jpeg_valide() -> bytes:
    """Vrai JPEG décodable par Pillow (le service nettoie les images)."""
    tampon = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(tampon, format="JPEG")
    return tampon.getvalue()


JPEG_MINIMAL = _jpeg_valide()
POINT_DOUALA = {"type": "Point", "coordinates": [9.7679, 4.0483]}


@pytest.fixture
def service_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> ServiceFichiers:
    """Remplace le service des vues par un stockage temporaire (pas de pollution disque)."""
    service = ServiceFichiers(stockage=StockageDisque(tmp_path))
    monkeypatch.setattr("apps.fichiers.views.service_fichiers", service)
    return service


def _upload_jpeg(client: APIClient, contexte: str = "produit") -> dict[str, Any]:
    reponse = client.post(
        FICHIERS,
        {
            "fichier": SimpleUploadedFile("photo.jpg", JPEG_MINIMAL, content_type="image/jpeg"),
            "contexte": contexte,
        },
        format="multipart",
    )
    assert reponse.status_code == 201, reponse.data
    return reponse.json()


# --- Fichiers ----------------------------------------------------------------


@pytest.mark.integration
def test_upload_valide_et_metadonnées(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    client, amina = client_authentifie(role="acheteur")
    data = _upload_jpeg(client)
    assert data["contexte"] == "produit"
    assert data["type_mime"] == "image/jpeg"
    assert data["public"] is False
    assert "cle_stockage" not in data  # chemin interne jamais exposé
    # GET /fichiers/{id} → 302 vers l'URL signée, le contenu suit.
    reponse = client.get(f"{FICHIERS}/{data['id']}")
    assert reponse.status_code == 302
    location = reponse["Location"]
    assert "expire=" in location and "signature=" in location
    suite = client.get(urlparse(location).path + "?" + urlparse(location).query)
    assert suite.status_code == 200


@pytest.mark.integration
def test_faux_jpg_executable_refuse_en_api(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    client, _ = client_authentifie(role="agriculteur")
    reponse = client.post(
        FICHIERS,
        {
            "fichier": SimpleUploadedFile(
                "virus.jpg", b"MZ\x90\x00" + b"\x00" * 256, content_type="image/jpeg"
            ),
            "contexte": "produit",
        },
        format="multipart",
    )
    assert reponse.status_code == 422
    assert reponse.json()["erreur"]["code"] == "type_fichier_non_autorise"


@pytest.mark.integration
def test_justificatif_d_un_autre_invisible_404(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    """Un justificatif n'est visible que par son propriétaire et les admins."""
    nadia_client, _ = client_authentifie(
        role="ingenieur_agronome", statut_compte="en_attente_validation"
    )
    fichier = _upload_jpeg(nadia_client, contexte="justificatif")
    paul_client, _ = client_authentifie(role="agriculteur")
    reponse = paul_client.get(f"{FICHIERS}/{fichier['id']}")
    assert reponse.status_code == 404
    assert reponse.json()["erreur"]["code"] == "ressource_introuvable"
    # L'admin, lui, y accède (décision D-13).
    admin_client, _ = client_authentifie(role="admin")
    assert admin_client.get(f"{FICHIERS}/{fichier['id']}").status_code == 302


@pytest.mark.integration
def test_url_signee_expiree_410(client_authentifie: Any, service_tmp: ServiceFichiers) -> None:
    client, _ = client_authentifie(role="acheteur")
    fichier = _upload_jpeg(client)
    expire_ts = int((datetime.now(UTC) - timedelta(minutes=2)).timestamp())
    signature = ServiceFichiers._signature(fichier["id"], expire_ts)
    reponse = client.get(
        f"{FICHIERS}/{fichier['id']}/contenu?expire={expire_ts}&signature={signature}"
    )
    assert reponse.status_code == 410
    assert reponse.json()["erreur"]["code"] == "url_expiree"


# --- Notifications ------------------------------------------------------------


@pytest.mark.integration
def test_parcours_notifications_liste_compteur_lue_toutlire(
    client_authentifie: Any,
) -> None:
    client, amina = client_authentifie(role="acheteur")
    notifier(
        amina,
        "commande",
        "Commande confirmée",
        "Votre commande est payée.",
        lien="/acheteur/commandes",
    )
    notifier(amina, "paiement", "Paiement reçu")

    reponse = client.get(NOTIFICATIONS)
    assert reponse.status_code == 200
    assert reponse.json()["total"] == 2

    compteur = client.get(f"{NOTIFICATIONS}/compteur").json()
    assert compteur["non_lues"] == 2

    identifiants = [n["id"] for n in client.get(NOTIFICATIONS).json()["elements"]]
    lue = client.post(f"{NOTIFICATIONS}/{identifiants[0]}/lue")
    assert lue.status_code == 200 and lue.json()["lue_le"]
    assert client.get(f"{NOTIFICATIONS}/compteur").json()["non_lues"] == 1

    filtre = client.get(f"{NOTIFICATIONS}?non_lues=true").json()
    assert filtre["total"] == 1

    tout = client.post(f"{NOTIFICATIONS}/tout-lire")
    assert tout.status_code == 200 and tout.json()["modifiees"] == 1
    assert client.get(f"{NOTIFICATIONS}/compteur").json()["non_lues"] == 0


@pytest.mark.integration
def test_notification_d_autrui_invisible_404(client_authentifie: Any) -> None:
    client_a, amina = client_authentifie(role="acheteur")
    notification = notifier(amina, "commande", "Privée")
    client_b, _ = client_authentifie(role="agriculteur")
    assert client_b.post(f"{NOTIFICATIONS}/{notification.id}/lue").status_code == 404
    assert client_b.get(NOTIFICATIONS).json()["total"] == 0


# --- Profils et validation ------------------------------------------------------------


@pytest.mark.integration
def test_parcours_nadia_profil_justificatifs_validation(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    """E2E-02 côté API : Nadia complète son dossier, Mireille la valide, notification."""
    nadia_client, nadia = client_authentifie(
        role="ingenieur_agronome", statut_compte="en_attente_validation"
    )
    # ING-02 : profil professionnel.
    reponse = nadia_client.put(
        f"{API}/ingenieurs/moi/profil",
        {
            "specialites": ["maraichage"],
            "region": "littoral",
            "zone_centre": POINT_DOUALA,
            "zone_rayon_km": 30,
            "tarif_consultation_fcfa": 8_000,
            "biographie": "Ingénieure à Douala.",
        },
        format="json",
    )
    assert reponse.status_code == 200, reponse.data
    assert reponse.json()["statut_validation"] == "en_attente"

    # Justificatif : upload puis dépôt.
    fichier = _upload_jpeg(nadia_client, contexte="justificatif")
    depot = nadia_client.post(
        f"{API}/ingenieurs/moi/justificatifs",
        {"fichier_id": fichier["id"], "type": "piece_identite"},
        format="json",
    )
    assert depot.status_code == 201, depot.data

    # Un agriculteur ne peut pas valider (403, vérifié côté API).
    paul_client, _ = client_authentifie(role="agriculteur")
    refus = paul_client.post(f"{API}/admin/ingenieurs/{nadia.id}/valider", {}, format="json")
    assert refus.status_code == 403

    # Le refus sans motif est impossible.
    admin_client, _ = client_authentifie(role="admin")
    sans_motif = admin_client.post(
        f"{API}/admin/ingenieurs/{nadia.id}/refuser", {"motif": " "}, format="json"
    )
    assert sans_motif.status_code == 422
    assert sans_motif.json()["erreur"]["code"] == "motif_obligatoire"

    # Mireille valide → compte actif + notification reçue.
    validation = admin_client.post(f"{API}/admin/ingenieurs/{nadia.id}/valider", {}, format="json")
    assert validation.status_code == 200, validation.data
    nadia.refresh_from_db()
    assert nadia.statut_compte == StatutCompte.ACTIF
    profil = IngenieurProfil.objects.get(utilisateur_id=nadia.id)
    assert profil.statut_validation == StatutValidation.VALIDE
    assert Notification.objects.filter(destinataire=nadia, type="administration").count() == 1

    # La liste publique expose les profils validés et disponibles, sans
    # téléphone (03 SPEC-01).
    liste = admin_client.get(f"{API}/ingenieurs")
    assert liste.status_code == 200
    for ligne in liste.json()["elements"]:
        assert "telephone" not in ligne


@pytest.mark.integration
def test_tarif_hors_bornes_en_api(client_authentifie: Any) -> None:
    client, _ = client_authentifie(role="ingenieur_agronome")
    reponse = client.put(
        f"{API}/ingenieurs/moi/profil", {"tarif_consultation_fcfa": 999_999}, format="json"
    )
    assert reponse.status_code == 422
    assert reponse.json()["erreur"]["code"] == "tarif_hors_bornes"


@pytest.mark.integration
def test_disponibilite_livreur_avec_position(client_authentifie: Any) -> None:
    client, jean = client_authentifie(role="livreur")
    # Sans position : refus.
    refus = client.put(f"{API}/livreurs/moi/disponibilite", {"disponible": True}, format="json")
    assert refus.status_code == 422
    assert refus.json()["erreur"]["code"] == "position_requise"
    # Avec position : enregistré et relu après « rechargement ».
    OK = client.put(
        f"{API}/livreurs/moi/disponibilite",
        {"disponible": True, "position": POINT_DOUALA},
        format="json",
    )
    assert OK.status_code == 200
    relu = client.get(f"{API}/livreurs/moi/disponibilite").json()
    assert relu["disponible"] is True
    assert relu["position"]["coordinates"] == POINT_DOUALA["coordinates"]
    # Réservé aux livreurs : un agriculteur reçoit 403.
    client_p, _ = client_authentifie(role="agriculteur")
    assert (
        client_p.put(
            f"{API}/livreurs/moi/disponibilite", {"disponible": True}, format="json"
        ).status_code
        == 403
    )


# --- Administration -----------------------------------------------------------


@pytest.mark.integration
def test_suspension_revoque_les_sessions_et_reactivation(
    client_api: Any, fabrique_utilisateur: Any
) -> None:
    """Jean connecté est déconnecté à la prochaine action après suspension (F-03)."""
    jean = fabrique_utilisateur(role=RoleUtilisateur.LIVREUR.value, password="Jeanmotdepasse1!")
    client_jean = APIClient()
    connexion = client_jean.post(
        CONNEXION,
        {"identifiant": jean.telephone, "mot_de_passe": "Jeanmotdepasse1!"},
        format="json",
    )
    assert connexion.status_code == 200
    assert client_jean.cookies.get(NOM_COOKIE_RAFRAICHISSEMENT)

    admin = fabrique_utilisateur(role=RoleUtilisateur.ADMIN.value)
    from apps.utilisateurs.services import SessionService

    client_admin = APIClient()
    jeton, _ = SessionService.generer_jeton_acces(admin)
    client_admin.credentials(HTTP_AUTHORIZATION=f"Bearer {jeton}")

    suspension = client_admin.post(
        f"{API}/admin/utilisateurs/{jean.id}/suspendre", {"motif": "Litiges répétés"}, format="json"
    )
    assert suspension.status_code == 200, suspension.data
    assert suspension.json()["statut_compte"] == "suspendu"

    # La session en cours de Jean est morte : rafraîchir échoue (401).
    refus = client_jean.post(RAFRAICHIR, {}, format="json")
    assert refus.status_code == 401

    # Nouvelle connexion refusée tant que le compte est suspendu.
    connexion_refusee = client_api.post(
        CONNEXION,
        {"identifiant": jean.telephone, "mot_de_passe": "Jeanmotdepasse1!"},
        format="json",
    )
    assert connexion_refusee.status_code in (401, 403)

    # Réactivé, il se reconnecte.
    reactivation = client_admin.post(
        f"{API}/admin/utilisateurs/{jean.id}/reactiver", {}, format="json"
    )
    assert reactivation.status_code == 200
    connexion_ok = client_api.post(
        CONNEXION,
        {"identifiant": jean.telephone, "mot_de_passe": "Jeanmotdepasse1!"},
        format="json",
    )
    assert connexion_ok.status_code == 200


@pytest.mark.integration
def test_mot_de_passe_temporaire_affiche_une_seule_fois(
    client_authentifie: Any, fabrique_utilisateur: Any, client_api: Any
) -> None:
    """Q-23 : reset admin → mot de passe renvoyé une fois, changement obligatoire."""
    paul = fabrique_utilisateur(role=RoleUtilisateur.AGRICULTEUR.value, password="Paulmotdepasse1!")
    admin_client, _ = client_authentifie(role="admin")

    reponse = admin_client.post(
        f"{API}/admin/utilisateurs/{paul.id}/reinitialiser-mot-de-passe", {}, format="json"
    )
    assert reponse.status_code == 200, reponse.data
    mdp = reponse.json()["mot_de_passe_temporaire"]
    assert len(mdp) >= 8 and reponse.json()["doit_changer_mot_de_passe"] is True

    # Impossible d'obtenir le mot de passe une seconde fois (non persisté en clair),
    # et la nouvelle génération remplace la précédente.
    reponse2 = admin_client.post(
        f"{API}/admin/utilisateurs/{paul.id}/reinitialiser-mot-de-passe", {}, format="json"
    )
    mdp = reponse2.json()["mot_de_passe_temporaire"]
    assert mdp != reponse.json()["mot_de_passe_temporaire"]

    # Le nouveau mot de passe fonctionne, avec drapeau de changement (Q-23).
    connexion = client_api.post(
        CONNEXION, {"identifiant": paul.telephone, "mot_de_passe": mdp}, format="json"
    )
    assert connexion.status_code == 200
    assert connexion.json()["utilisateur"]["doit_changer_mot_de_passe"] is True


@pytest.mark.integration
def test_liste_admin_recherche_et_compteurs(
    client_authentifie: Any, fabrique_utilisateur: Any
) -> None:
    admin_client, _ = client_authentifie(role="admin")
    reponse = admin_client.get(f"{API}/admin/utilisateurs")
    assert reponse.status_code == 200
    assert "elements" in reponse.json()
    # Le téléphone est masqué en liste (conventions §6, minimisation).
    for ligne in reponse.json()["elements"]:
        assert "*" in ligne["telephone"]
    compteurs = admin_client.get(f"{API}/admin/utilisateurs/compteurs")
    assert compteurs.status_code == 200
    assert set(compteurs.json()) == {
        "ingenieurs_en_attente",
        "reinitialisations_en_attente",
        "comptes_suspendus",
    }


@pytest.mark.integration
def test_detail_admin_ingenieur_avec_justificatifs(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    """GET /admin/utilisateurs/{id} sérialise le dossier + pièces (ex-500 DRF)."""
    nadia_client, nadia = client_authentifie(
        role="ingenieur_agronome", statut_compte="en_attente_validation"
    )
    fichier = _upload_jpeg(nadia_client, contexte="justificatif")
    depot = nadia_client.post(
        f"{API}/ingenieurs/moi/justificatifs",
        {"fichier_id": fichier["id"], "type": "diplome"},
        format="json",
    )
    assert depot.status_code == 201, depot.data
    admin_client, _ = client_authentifie(role="admin")
    detail = admin_client.get(f"{API}/admin/utilisateurs/{nadia.id}")
    assert detail.status_code == 200, detail.data
    dossier = detail.json()["profil_ingenieur"]
    assert dossier["statut_validation"] == "en_attente"
    assert len(dossier["justificatifs"]) == 1
    assert dossier["justificatifs"][0]["fichier_id"] == fichier["id"]
    assert dossier["justificatifs"][0]["nom_original"] == "photo.jpg"


@pytest.mark.integration
def test_garde_routes_fichiers_notifications_sans_jeton(client_api: Any) -> None:
    assert client_api.post(FICHIERS, {}, format="multipart").status_code == 401
    assert client_api.get(NOTIFICATIONS).status_code == 401
    assert client_api.get(f"{NOTIFICATIONS}/compteur").status_code == 401
    assert client_api.get(f"{API}/ingenieurs").status_code == 401
    assert client_api.get(f"{API}/admin/utilisateurs").status_code == 401


@pytest.mark.integration
def test_compte_suspendu_ne_telecharge_plus_ses_fichiers(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    """Un compte suspendu reçoit 403 (pas de 302) sur ses propres fichiers."""
    client, utilisateur = client_authentifie(role="agriculteur")
    fichier = _upload_jpeg(client, contexte="produit")
    assert client.get(f"{FICHIERS}/{fichier['id']}", follow=False).status_code == 302
    utilisateur.statut_compte = StatutCompte.SUSPENDU
    utilisateur.save(update_fields=["statut_compte"])
    reponse = client.get(f"{FICHIERS}/{fichier['id']}", follow=False)
    assert reponse.status_code == 403
    assert reponse.json()["erreur"]["code"] == "compte_suspendu"


@pytest.mark.integration
def test_photo_profil_accepte_tout_fichier_image_du_proprietaire(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    """ING-02 : une image téléversée en contexte `produit` convient comme photo."""
    client, _ = client_authentifie(role="ingenieur_agronome")
    fichier = _upload_jpeg(client, contexte="produit")
    reponse = client.put(
        f"{API}/ingenieurs/moi/profil",
        {"photo_id": fichier["id"]},
        format="json",
    )
    assert reponse.status_code == 200, reponse.data


@pytest.mark.integration
def test_disponibilite_exige_des_justificatifs_deposes(
    client_authentifie: Any, service_tmp: ServiceFichiers
) -> None:
    """Disponible seulement avec dossier complet : profil + justificatifs + validation."""
    nadia_client, nadia = client_authentifie(
        role="ingenieur_agronome", statut_compte="en_attente_validation"
    )
    profil = nadia_client.put(
        f"{API}/ingenieurs/moi/profil",
        {
            "specialites": ["maraichage"],
            "region": "littoral",
            "zone_centre": POINT_DOUALA,
            "zone_rayon_km": 30,
            "tarif_consultation_fcfa": 8_000,
        },
        format="json",
    )
    assert profil.status_code == 200, profil.data
    admin_client, _ = client_authentifie(role="admin")
    validation = admin_client.post(f"{API}/admin/ingenieurs/{nadia.id}/valider", {}, format="json")
    assert validation.status_code == 200, validation.data

    refuse = nadia_client.put(f"{API}/ingenieurs/moi/profil", {"disponible": True}, format="json")
    assert refuse.status_code == 422
    assert refuse.json()["erreur"]["code"] == "profil_incomplet"
    assert "justificatifs" in refuse.json()["erreur"]["details"]["champs_manquants"]

    fichier = _upload_jpeg(nadia_client, contexte="justificatif")
    depot = nadia_client.post(
        f"{API}/ingenieurs/moi/justificatifs",
        {"fichier_id": fichier["id"], "type": "diplome"},
        format="json",
    )
    assert depot.status_code == 201, depot.data
    accepte = nadia_client.put(f"{API}/ingenieurs/moi/profil", {"disponible": True}, format="json")
    assert accepte.status_code == 200, accepte.data
    assert accepte.json()["disponible"] is True

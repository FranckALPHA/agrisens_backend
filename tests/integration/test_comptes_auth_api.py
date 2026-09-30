"""Tests d'intégration API SPEC-01 / F-02 : parcours auth complets par HTTP.

Couverture : inscription puis connexion, renouvellement de session,
modification du profil, suspension, validation du débit, permissions
testées avec tous les rôles (aide du prompt F-02 point 7).
"""

from typing import Any, cast

import jwt
import pytest
from apps.utilisateurs.services import NOM_COOKIE_RAFRAICHISSEMENT
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient

API = "/api/v1"
INSCRIPTION = f"{API}/auth/inscription"
CONNEXION = f"{API}/auth/connexion"
RAFRAICHIR = f"{API}/auth/rafraichir"
DECONNEXION = f"{API}/auth/deconnexion"
REINITIALISATION = f"{API}/auth/demande-reinitialisation"
MOI = f"{API}/utilisateurs/moi"
MOI_MDP = f"{API}/utilisateurs/moi/mot-de-passe"
ADRESSES = f"{API}/utilisateurs/moi/adresses"

MOT_DE_PASSE = "Motdepasse123!"
POINT_DOUALA = {"type": "Point", "coordinates": [9.7003, 4.0511]}


def _inscription(
    client: APIClient,
    telephone: str = "+237690000001",
    role: str = "agriculteur",
    **surcharge: Any,
) -> Any:
    donnees = {
        "nom": "Paul Mbianda",
        "telephone": telephone,
        "mot_de_passe": MOT_DE_PASSE,
        "role": role,
        **surcharge,
    }
    return client.post(INSCRIPTION, donnees, format="json")


@pytest.mark.integration
@pytest.mark.django_db
def test_inscription_puis_connexion_puis_moi(client_api: APIClient) -> None:
    reponse = _inscription(client_api, email="paul@agrisens.local")
    assert reponse.status_code == 201, reponse.content
    corps = reponse.json()
    assert corps["utilisateur"]["role"] == "agriculteur"
    assert corps["utilisateur"]["statut_compte"] == "actif"
    assert corps["jeton_acces"]
    # Cookie httpOnly posé (journal Q-25).
    assert client_api.cookies.get(NOM_COOKIE_RAFRAICHISSEMENT)

    client = APIClient()
    reponse = client.post(
        CONNEXION, {"identifiant": "+237690000001", "mot_de_passe": MOT_DE_PASSE}, format="json"
    )
    assert reponse.status_code == 200, reponse.content
    jeton = reponse.json()["jeton_acces"]

    client.credentials(HTTP_AUTHORIZATION=f"Bearer {jeton}")
    reponse = client.get(MOI)
    assert reponse.status_code == 200
    assert reponse.json()["telephone"] == "+237690000001"


@pytest.mark.integration
@pytest.mark.django_db
def test_inscription_doublon_et_mot_de_passe_court(client_api: APIClient) -> None:
    assert _inscription(client_api).status_code == 201

    reponse = _inscription(client_api, nom="Autre")
    assert reponse.status_code == 409
    assert reponse.json()["erreur"]["code"] == "compte_existant"
    assert reponse.json()["erreur"]["details"]["champ"] == "telephone"

    reponse = _inscription(client_api, telephone="+237690000002", mot_de_passe="court")
    assert reponse.status_code == 422
    assert "mot_de_passe" in reponse.json()["erreur"]["details"]

    reponse = _inscription(client_api, telephone="+237690000003", role="admin")
    assert reponse.status_code == 422


@pytest.mark.integration
@pytest.mark.django_db
def test_inscription_ingenieur_ecran_attente(client_api: APIClient) -> None:
    reponse = _inscription(client_api, role="ingenieur_agronome", telephone="+237690000010")
    assert reponse.status_code == 201
    assert reponse.json()["utilisateur"]["statut_compte"] == "en_attente_validation"

    client = APIClient()
    reponse = client.post(
        CONNEXION, {"identifiant": "+237690000010", "mot_de_passe": MOT_DE_PASSE}, format="json"
    )
    assert reponse.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {reponse.json()['jeton_acces']}")
    # Son profil reste accessible…
    assert client.get(MOI).status_code == 200
    # …mais pas les adresses (route réservé compte actif).
    reponse = client.get(ADRESSES)
    assert reponse.status_code == 403
    assert reponse.json()["erreur"]["code"] == "compte_en_attente"


@pytest.mark.integration
@pytest.mark.django_db
def test_connexion_anti_enumeration(client_api: APIClient) -> None:
    _inscription(client_api)
    inconnu = client_api.post(
        CONNEXION, {"identifiant": "+237600000000", "mot_de_passe": MOT_DE_PASSE}, format="json"
    )
    mauvais = client_api.post(
        CONNEXION, {"identifiant": "+237690000001", "mot_de_passe": "mauvais12345"}, format="json"
    )
    assert inconnu.status_code == mauvais.status_code == 401
    assert inconnu.json() == mauvais.json()
    assert inconnu.json()["erreur"]["code"] == "identifiants_invalides"


@pytest.mark.integration
@pytest.mark.django_db
def test_connexion_par_email_valide(client_api: APIClient) -> None:
    _inscription(client_api, email="paul@agrisens.local")
    reponse = client_api.post(
        CONNEXION,
        {"identifiant": "paul@AGRISENS.local", "mot_de_passe": MOT_DE_PASSE},
        format="json",
    )
    assert reponse.status_code == 200


@pytest.mark.integration
@pytest.mark.django_db
def test_compte_suspendu_refuse_connexion_et_appels(
    client_api: APIClient, fabrique_utilisateur: Any, generer_jeton_acces: Any
) -> None:
    suspendu = fabrique_utilisateur(
        statut_compte="suspendu", telephone="+237690000099", password=MOT_DE_PASSE
    )
    reponse = client_api.post(
        CONNEXION, {"identifiant": "+237690000099", "mot_de_passe": MOT_DE_PASSE}, format="json"
    )
    assert reponse.status_code == 403
    assert reponse.json()["erreur"]["code"] == "compte_suspendu"

    # Même avec un jeton émis avant la suspension → 403 compte_suspendu.
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {generer_jeton_acces(suspendu)}")
    for url in (MOI, ADRESSES):
        reponse = client.get(url)
        assert reponse.status_code == 403, url
        assert reponse.json()["erreur"]["code"] == "compte_suspendu", url


@pytest.mark.integration
@pytest.mark.django_db
def test_jeton_acces_expire_ou_falsifie(client_api: APIClient, fabrique_utilisateur: Any) -> None:
    import time

    utilisateur = fabrique_utilisateur()
    expire = jwt.encode(
        {
            "sub": str(utilisateur.id),
            "type": "acces",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        "secret-de-test-pour-jetons-00000000",
        algorithm="HS256",
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {expire}")
    assert client.get(MOI).status_code == 401

    falsifie = jwt.encode(
        {"sub": str(utilisateur.id), "type": "acces", "exp": int(time.time()) + 900},
        "tout-autre-secret-0000000000000000",
        algorithm="HS256",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {falsifie}")
    assert client.get(MOI).status_code == 401


@pytest.mark.integration
@pytest.mark.django_db
def test_refresh_rotation_et_reutilisation(client_api: APIClient) -> None:
    _inscription(client_api)
    jeton_cookie = client_api.cookies[NOM_COOKIE_RAFRAICHISSEMENT].value

    reponse = client_api.post(RAFRAICHIR)
    assert reponse.status_code == 200
    nouveau = client_api.cookies[NOM_COOKIE_RAFRAICHISSEMENT].value
    assert nouveau != jeton_cookie
    assert reponse.json()["jeton_acces"]

    # Le serveur de test rejoue tous les cookies : on force l'ancien jeton.
    client_api.cookies[NOM_COOKIE_RAFRAICHISSEMENT] = jeton_cookie
    reponse = client_api.post(RAFRAICHIR)
    assert reponse.status_code == 401
    assert reponse.json()["erreur"]["code"] == "sessions_revoquees"

    # La session tournante a été coupée aussi (révocation de toutes).
    client_api.cookies[NOM_COOKIE_RAFRAICHISSEMENT] = nouveau
    reponse = client_api.post(RAFRAICHIR)
    assert reponse.status_code == 401


@pytest.mark.integration
@pytest.mark.django_db
def test_refresh_sans_cookie_refuse(client_api: APIClient) -> None:
    reponse = client_api.post(RAFRAICHIR)
    assert reponse.status_code == 401
    assert reponse.json()["erreur"]["code"] == "session_invalide"


@pytest.mark.integration
@pytest.mark.django_db
def test_deconnexion_revoque_la_session(client_api: APIClient) -> None:
    _inscription(client_api)
    reponse = client_api.post(DECONNEXION)
    assert reponse.status_code == 204
    reponse = client_api.post(RAFRAICHIR)
    assert reponse.status_code == 401


@pytest.mark.integration
@pytest.mark.django_db
def test_modifier_profil_et_doublon_email(client_authentifie: Any, client_api: APIClient) -> None:
    client, paul = client_authentifie(role="agriculteur")
    reponse = client.patch(
        MOI,
        {"nom": "Paul Mbianda Ngono", "region": "Ouest", "email": "paul@agrisens.local"},
        format="json",
    )
    assert reponse.status_code == 200
    assert reponse.json()["nom"] == "Paul Mbianda Ngono"

    # Un email déjà pris par un autre compte → 409.
    client2, _ = client_authentifie(role="acheteur", email="pris@agrisens.local")
    reponse = client.patch(MOI, {"email": "pris@agrisens.local"}, format="json")
    assert reponse.status_code == 409
    assert reponse.json()["erreur"]["details"]["champ"] == "email"
    assert client2.get(MOI).status_code == 200

    # Le rôle n'est jamais modifiable via moi.
    reponse = client.patch(MOI, {"role": "super_admin"}, format="json")
    assert reponse.json()["role"] == "agriculteur"


@pytest.mark.integration
@pytest.mark.django_db
def test_changer_mot_de_passe_remet_le_drapeau(client_authentifie: Any) -> None:
    client, utilisateur = client_authentifie(
        role="acheteur", doit_changer_mot_de_passe=True, password=MOT_DE_PASSE
    )
    reponse = client.put(
        MOI_MDP,
        {"mot_de_passe_actuel": MOT_DE_PASSE, "nouveau_mot_de_passe": "Nouveau12345!"},
        format="json",
    )
    assert reponse.status_code == 200
    assert reponse.json()["doit_changer_mot_de_passe"] is False

    reponse = client.put(
        MOI_MDP,
        {"mot_de_passe_actuel": "faux123456!", "nouveau_mot_de_passe": "Encore12345!"},
        format="json",
    )
    assert reponse.status_code == 422
    assert reponse.json()["erreur"]["code"] == "mot_de_passe_incorrect"
    utilisateur.refresh_from_db()
    assert utilisateur.check_password("Nouveau12345!")


@pytest.mark.integration
@pytest.mark.django_db
def test_adresses_crud_et_geojson(client_authentifie: Any) -> None:
    client, _ = client_authentifie(role="agriculteur")
    reponse = client.post(
        ADRESSES,
        {
            "libelle": "Champ Nord",
            "description": "Après le carrefour",
            "position": POINT_DOUALA,
            "ville": "Douala",
            "region": "Littoral",
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.content
    adresse = reponse.json()
    assert adresse["position"]["type"] == "Point"
    assert adresse["position"]["coordinates"] == pytest.approx(POINT_DOUALA["coordinates"])

    url_detail = f"{ADRESSES}/{adresse['id']}/"
    reponse = client.get(ADRESSES)
    assert reponse.status_code == 200
    assert len(reponse.json()) == 1

    reponse = client.patch(url_detail, {"libelle": "Domicile"}, format="json")
    assert reponse.status_code == 200
    assert reponse.json()["libelle"] == "Domicile"

    # Position non Point → 422.
    reponse = client.patch(
        url_detail,
        {"position": {"type": "LineString", "coordinates": [[9.7, 4.0], [9.8, 4.1]]}},
        format="json",
    )
    assert reponse.status_code == 422

    reponse = client.delete(url_detail)
    assert reponse.status_code == 204
    assert client.get(ADRESSES).json() == []


@pytest.mark.integration
@pytest.mark.django_db
def test_adresse_d_autrui_renvoie_404(client_authentifie: Any) -> None:
    client_paul, _ = client_authentifie(role="agriculteur")
    client_amina, _ = client_authentifie(role="acheteur")

    reponse = client_paul.post(
        ADRESSES,
        {"libelle": "Champ", "position": POINT_DOUALA, "ville": "Douala", "region": "Littoral"},
        format="json",
    )
    adresse_id = reponse.json()["id"]

    for methode in ("patch", "delete"):
        appel = getattr(client_amina, methode)
        reponse = appel(f"{ADRESSES}/{adresse_id}/", {}, format="json")
        # Règle AGENTS : ressource privée d'autrui → 404, jamais 403.
        assert reponse.status_code == 404, f"{methode} → {reponse.status_code}"


@pytest.mark.integration
@pytest.mark.django_db
def test_demande_reinitialisation_reponse_neutre(
    client_api: APIClient, fabrique_utilisateur: Any
) -> None:
    utilisateur = fabrique_utilisateur(telephone="+237690000020")
    reponse_connue = client_api.post(
        REINITIALISATION, {"telephone": "+237690000020"}, format="json"
    )
    reponse_inconnue = client_api.post(
        REINITIALISATION, {"telephone": "+237600000000"}, format="json"
    )
    assert reponse_connue.status_code == reponse_inconnue.status_code == 200
    assert reponse_connue.json() == reponse_inconnue.json()
    utilisateur.refresh_from_db()
    assert utilisateur.reinitialisation_demandee_le is not None


@pytest.mark.integration
@pytest.mark.django_db
def test_limitation_debit_connexion(client_api: APIClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """La limitation de débit de /auth/connexion renvoie 429 limite_debit_atteinte.

    Note : DRF fige `THROTTLE_RATES` à la définition de la classe de throttle ;
    un override_settings(REST_FRAMEWORK=…) ne l'atteint pas, d'où le monkeypatch.
    """
    from rest_framework.throttling import ScopedRateThrottle

    cache.clear()
    taux_defaut = cast("dict[str, str]", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {**taux_defaut, "connexion": "2/min"},
    )
    for _ in range(2):
        client_api.post(
            CONNEXION,
            {"identifiant": "+237600000000", "mot_de_passe": "x" * 8},
            format="json",
        )
    reponse = client_api.post(
        CONNEXION, {"identifiant": "+237600000000", "mot_de_passe": "x" * 8}, format="json"
    )
    assert reponse.status_code == 429
    assert reponse.json()["erreur"]["code"] == "limite_debit_atteinte"
    cache.clear()


@pytest.mark.integration
@pytest.mark.django_db
def test_garde_permissions_routes_perso(
    verifier_garde_route: Any,
) -> None:
    """Aide F-02 point 7 : 401 sans jeton, succès pour tous les rôles autorisés.

    Les routes « moi » sont ouvertes à tous les rôles (données personnelles),
    avec l'admissibilité en attente pour /utilisateurs/moi.
    """
    verifier_garde_route(
        MOI,
        roles_autorises=[
            "agriculteur",
            "acheteur",
            "ingenieur_agronome",
            "livreur",
            "admin",
            "super_admin",
        ],
    )
    verifier_garde_route(
        ADRESSES,
        roles_autorises=[
            "agriculteur",
            "acheteur",
            "ingenieur_agronome",
            "livreur",
            "admin",
            "super_admin",
        ],
    )

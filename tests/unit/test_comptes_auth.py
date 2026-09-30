"""Tests unitaires SPEC-01 périmètre F-02 : téléphones, hachage, JWT, sessions, règles.

Prompt F-02 point 9 : hachage, JWT valide/expiré/falsifié, rotation,
réutilisation, suspendu, doublons téléphone/email, propriété des adresses.
"""

from datetime import timedelta
from typing import Any

import jwt
import pytest
from apps.utilisateurs.authentification import AuthentificationJWT
from apps.utilisateurs.serializers import InscriptionSerializer, MotDePasseSerializer
from apps.utilisateurs.services import (
    AdresseService,
    CompteService,
    SessionService,
    hacher_jeton,
    normaliser_telephone,
)
from commun import horloge
from commun.exceptions import Conflit, ErreurMetier, NonTrouve, ValidationMetier
from django.conf import settings
from django.test import RequestFactory
from rest_framework import exceptions as drf_exceptions
from rest_framework.request import Request

MOT_DE_PASSE = "Motdepasse123!"


@pytest.fixture(autouse=True)
def _horloge_reelle() -> Any:
    """Garantit que chaque test repart d'une horloge propre."""
    horloge.reinitialiser()
    yield
    horloge.reinitialiser()


# ── Téléphone E.164 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("+237690000001", "+237690000001"),
        ("237690000001", "+237690000001"),
        ("690000001", "+237690000001"),
        ("0690000001", "+237690000001"),
        ("+237 6 90 00 00 01", "+237690000001"),
        ("690-000-001", "+237690000001"),
    ],
)
def test_normaliser_telephone_accepte_les_formats_courants(saisie: str, attendu: str) -> None:
    assert normaliser_telephone(saisie) == attendu


@pytest.mark.parametrize("saisie", ["", "abc", "69000001", "790000001", "+23712345"])
def test_normaliser_telephone_refuse_invalides(saisie: str) -> None:
    with pytest.raises(ValidationMetier) as exc:
        normaliser_telephone(saisie)
    assert exc.value.code == "telephone_invalide"


# ── Mots de passe et hachage ───────────────────────────────────────────────


def test_argon2_premier_hacher_dans_reglages_de_base() -> None:
    """base.py doit lister argon2 en premier (conventions §6, prompt F-02 point 2)."""
    from config.settings import base

    assert "argon2" in base.PASSWORD_HASHERS[0].lower()


def test_mot_de_passe_trop_court_refuse() -> None:
    serializer = InscriptionSerializer(
        data={
            "nom": "Paul",
            "telephone": "+237690000001",
            "mot_de_passe": "court",
            "role": "agriculteur",
        }
    )
    assert not serializer.is_valid()
    assert "mot_de_passe" in serializer.errors

    mauvais = MotDePasseSerializer(
        data={"mot_de_passe_actuel": MOT_DE_PASSE, "nouveau_mot_de_passe": "123"}
    )
    assert not mauvais.is_valid()
    assert "nouveau_mot_de_passe" in mauvais.errors


def test_hachage_different_du_mot_de_passe_original(
    fabrique_utilisateur: Any,
) -> None:
    utilisateur = fabrique_utilisateur(password=MOT_DE_PASSE)
    assert utilisateur.password != MOT_DE_PASSE
    assert MOT_DE_PASSE not in utilisateur.password
    assert utilisateur.check_password(MOT_DE_PASSE)
    assert not utilisateur.check_password("mauvais mot de passe")


# ── JWT d'accès : valide, expiré, falsifié ─────────────────────────────────


def test_jwt_acces_valide_et_claims(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur(role="agriculteur")
    jeton, expire_le = SessionService.generer_jeton_acces(utilisateur)
    payload = jwt.decode(jeton, settings.JWT_SECRET, algorithms=["HS256"])
    assert payload["sub"] == str(utilisateur.id)
    assert payload["role"] == "agriculteur"
    assert payload["statut_compte"] == "actif"
    assert payload["type"] == "acces"
    assert payload["exp"] - payload["iat"] == settings.JWT_DUREE_ACCES_MIN * 60
    assert expire_le > horloge.maintenant()


def _authentifie(jeton: str) -> Any:
    requete = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {jeton}")
    return AuthentificationJWT().authenticate(Request(requete))


def test_jwt_expire_refuse(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    horloge.decaler(timedelta(minutes=-(settings.JWT_DUREE_ACCES_MIN + 1)))
    jeton, _ = SessionService.generer_jeton_acces(utilisateur)
    horloge.decaler(timedelta(minutes=settings.JWT_DUREE_ACCES_MIN + 1))
    with pytest.raises(drf_exceptions.AuthenticationFailed, match="expiré"):
        _authentifie(jeton)


def test_jwt_falsifie_refuse(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    # Signature avec un mauvais secret.
    faux = jwt.encode(
        {"sub": str(utilisateur.id)}, "autre-secret-de-32-caracteres-minimum!!", algorithm="HS256"
    )
    with pytest.raises(drf_exceptions.AuthenticationFailed):
        _authentifie(faux)
    # Un jeton d'un autre type (rafraîchissement) ne vaut pas jeton d'accès.
    jeton, _ = SessionService.generer_jeton_acces(utilisateur)
    payload = jwt.decode(jeton, settings.JWT_SECRET, algorithms=["HS256"])
    payload["type"] = "rafraichir"
    avec_mauvais_type = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
    with pytest.raises(drf_exceptions.AuthenticationFailed):
        _authentifie(avec_mauvais_type)
    # Signature valide mais utilisateur inexistant.
    orphelin = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "type": "acces",
            "exp": horloge.maintenant().timestamp() + 60,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(drf_exceptions.AuthenticationFailed, match="introuvable"):
        _authentifie(orphelin)


# ── Inscription : rôles, doublons, statut ingénieur ────────────────────────


@pytest.mark.django_db
def test_role_inconnu_refuse_a_l_inscription() -> None:
    with pytest.raises(ValidationMetier) as exc:
        CompteService.inscrire(
            nom="Sneaky",
            telephone="+237691111111",
            mot_de_passe=MOT_DE_PASSE,
            role="super_admin",
        )
    assert exc.value.code == "role_inconnu"


@pytest.mark.django_db
def test_inscription_ingenieur_en_attente_validation() -> None:
    utilisateur, jeton_acces, jeton_rafraichir, expire_le = CompteService.inscrire(
        nom="Nadia",
        telephone="+237692222222",
        mot_de_passe=MOT_DE_PASSE,
        role="ingenieur_agronome",
    )
    assert utilisateur.statut_compte == "en_attente_validation"
    assert jeton_acces and jeton_rafraichir and expire_le > horloge.maintenant()


@pytest.mark.django_db
def test_inscription_doublon_telephone_puis_email(
    fabrique_utilisateur: Any,
) -> None:
    fabrique_utilisateur(telephone="+237693333333", email="duplo@agrisens.local")

    with pytest.raises(Conflit) as exc_tel:
        CompteService.inscrire(
            nom="Doublon",
            telephone="693333333",  # même numéro, format non normalisé
            mot_de_passe=MOT_DE_PASSE,
            role="acheteur",
        )
    assert exc_tel.value.code == "compte_existant"
    assert exc_tel.value.details["champ"] == "telephone"

    with pytest.raises(Conflit) as exc_mail:
        CompteService.inscrire(
            nom="Doublon",
            telephone="+237694444444",
            mot_de_passe=MOT_DE_PASSE,
            role="acheteur",
            email="DUPLO@agrisens.local",  # insensible à la casse
        )
    assert exc_mail.value.code == "compte_existant"
    assert exc_mail.value.details["champ"] == "email"


# ── Sessions : stockage haché, rotation, réutilisation, expiration ─────────


@pytest.mark.django_db
def test_session_stockee_hachee(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    session, jeton_brut = SessionService.creer_session(utilisateur, agent="PyTest")
    assert session.jeton_hash == hacher_jeton(jeton_brut)
    assert jeton_brut not in session.jeton_hash
    assert session.est_valide


@pytest.mark.django_db
def test_rotation_du_jeton_de_rafraichissement(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    _, jeton_1 = SessionService.creer_session(utilisateur)
    utilisateur_, session_2, jeton_2 = SessionService.rafraichir(jeton_1)
    assert utilisateur_.id == utilisateur.id
    assert jeton_2 != jeton_1
    assert session_2.est_valide
    # L'ancien jeton est consommé, le nouveau fonctionne.
    _, session_3, _ = SessionService.rafraichir(jeton_2)
    assert session_3.est_valide


@pytest.mark.django_db
def test_reutilisation_revoque_toutes_les_sessions(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    _, jeton_vol_1 = SessionService.creer_session(utilisateur)
    _, jeton_appareil_2 = SessionService.creer_session(utilisateur)

    # Vol : l'attaquant présente le jeton 1 une deuxième fois après rotation.
    _, _, jeton_vol_2 = SessionService.rafraichir(jeton_vol_1)
    with pytest.raises(ErreurMetier) as exc:
        SessionService.rafraichir(jeton_vol_1)
    assert exc.value.code == "sessions_revoquees"
    assert exc.value.statut_http == 401

    # Toutes les sessions sont coupées, y compris celle de l'appareil légitime.
    for jeton in (jeton_vol_2, jeton_appareil_2):
        with pytest.raises(ErreurMetier) as exc2:
            SessionService.rafraichir(jeton)
        assert exc2.value.statut_http == 401


@pytest.mark.django_db
def test_session_expiree(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    _, jeton = SessionService.creer_session(utilisateur)
    horloge.decaler(timedelta(days=settings.JWT_DUREE_RAFRAICHISSEMENT_JOURS + 1))
    with pytest.raises(ErreurMetier) as exc:
        SessionService.rafraichir(jeton)
    assert exc.value.code == "session_expiree"


@pytest.mark.django_db
def test_jeton_inconnu_refuse() -> None:
    with pytest.raises(ErreurMetier) as exc:
        SessionService.rafraichir("jeton-jamais-emit")
    assert exc.value.code == "session_invalide"


@pytest.mark.django_db
def test_deconnexion_idempotente(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    session, jeton = SessionService.creer_session(utilisateur)
    SessionService.deconnecter(None)  # jamais d'erreur
    SessionService.deconnecter("pas-un-jeton-known")
    SessionService.deconnecter(jeton)
    session.refresh_from_db()
    assert session.revoque_le is not None


# ── Compte suspendu ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_connexion_compte_suspendu_refusee(fabrique_utilisateur: Any) -> None:
    fabrique_utilisateur(telephone="+237695555555", statut_compte="suspendu", password=MOT_DE_PASSE)
    with pytest.raises(ErreurMetier) as exc:
        CompteService.connecter(identifiant="+237695555555", mot_de_passe=MOT_DE_PASSE)
    assert exc.value.code == "compte_suspendu"
    assert exc.value.statut_http == 403


@pytest.mark.django_db
def test_rafraichir_compte_suspendu_refuse(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur()
    _, jeton = SessionService.creer_session(utilisateur)
    utilisateur.statut_compte = "suspendu"
    utilisateur.save()
    with pytest.raises(ErreurMetier) as exc:
        SessionService.rafraichir(jeton)
    assert exc.value.code == "compte_suspendu"


# ── Connexion : anti-énumération ───────────────────────────────────────────


@pytest.mark.django_db
def test_connexion_message_identique_compte_inexistant(fabrique_utilisateur: Any) -> None:
    fabrique_utilisateur(telephone="+237696666666", password=MOT_DE_PASSE)

    with pytest.raises(ErreurMetier) as exc_inexistant:
        CompteService.connecter(identifiant="+237600000000", mot_de_passe=MOT_DE_PASSE)
    with pytest.raises(ErreurMetier) as exc_mauvais:
        CompteService.connecter(identifiant="+237696666666", mot_de_passe="mauvais123!")
    assert exc_inexistant.value.message == exc_mauvais.value.message
    assert exc_inexistant.value.code == "identifiants_invalides" == exc_mauvais.value.code
    assert exc_inexistant.value.statut_http == 401


@pytest.mark.django_db
def test_connexion_par_email(fabrique_utilisateur: Any) -> None:
    fabrique_utilisateur(
        telephone="+237697777777", email="paul@agrisens.local", password=MOT_DE_PASSE
    )
    utilisateur, jeton_acces, _, _ = CompteService.connecter(
        identifiant="PAUL@agrisens.local", mot_de_passe=MOT_DE_PASSE
    )
    assert utilisateur.telephone == "+237697777777"
    assert jeton_acces


# ── Mot de passe : changement et drapeau ───────────────────────────────────


@pytest.mark.django_db
def test_changer_mot_de_passe_remet_le_drapeau(fabrique_utilisateur: Any) -> None:
    utilisateur = fabrique_utilisateur(password=MOT_DE_PASSE, doit_changer_mot_de_passe=True)
    CompteService.changer_mot_de_passe(utilisateur, MOT_DE_PASSE, "NouveauPass123!")
    utilisateur.refresh_from_db()
    assert not utilisateur.doit_changer_mot_de_passe
    assert utilisateur.check_password("NouveauPass123!")

    with pytest.raises(ErreurMetier) as exc:
        CompteService.changer_mot_de_passe(utilisateur, "ancien-mauvais", "EncoreAutre123!")
    assert exc.value.code == "mot_de_passe_incorrect"
    assert exc.value.statut_http == 422


# ── Adresses : propriété et géométrie ──────────────────────────────────────


POINT_DOUALA = {"type": "Point", "coordinates": [9.7, 4.05]}


@pytest.mark.django_db
def test_adresse_creee_listee_modifiee_supprimee(fabrique_utilisateur: Any) -> None:
    paul = fabrique_utilisateur(role="agriculteur")
    amina = fabrique_utilisateur(role="acheteur")

    adresse = AdresseService.creer(
        paul, libelle="Champ", position=POINT_DOUALA, ville="Douala", region="Littoral"
    )
    assert adresse.position is not None
    assert AdresseService.lister(paul) == [adresse]
    assert AdresseService.lister(amina) == []

    # Propriété : un autre utilisateur ne voit ni ne touche l'adresse (404).
    with pytest.raises(NonTrouve):
        AdresseService.obtenir(amina, str(adresse.id))
    with pytest.raises(NonTrouve):
        AdresseService.modifier(amina, str(adresse.id), {"libelle": "Volée"})
    with pytest.raises(NonTrouve):
        AdresseService.supprimer(amina, str(adresse.id))

    adresse_modifiee = AdresseService.modifier(paul, str(adresse.id), {"libelle": "Domicile"})
    assert adresse_modifiee.libelle == "Domicile"
    AdresseService.supprimer(paul, str(adresse.id))
    assert AdresseService.lister(paul) == []


@pytest.mark.django_db
def test_adresse_position_doit_etre_un_point(fabrique_utilisateur: Any) -> None:
    paul = fabrique_utilisateur(role="agriculteur")
    ligne = {"type": "LineString", "coordinates": [[9.7, 4.0], [9.8, 4.1]]}
    with pytest.raises(ValidationMetier) as exc:
        AdresseService.creer(
            paul, libelle="Erreur", position=ligne, ville="Douala", region="Littoral"
        )
    assert exc.value.code in ("geometrie_invalide", "validation_invalide")

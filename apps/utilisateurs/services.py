"""Logique métier du module utilisateurs (prompt F-02).

Règles :
- inscription, connexion, sessions à rotation, profil, mot de passe, adresses ;
- tout le métier est ici, les vues DRF restent minces ;
- téléphone normalisé E.164 (+237), email facultatif et unique ;
- jeton de rafraîchissement opaque, stocké haché dans SESSION, cookie httpOnly
  (journal Q-25), rotation à chaque usage, réutilisation → révoke tout.
"""

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any

import jwt
from commun.exceptions import Conflit, ErreurMetier, NonTrouve, ValidationMetier
from commun.geo import valider_geojson
from commun.horloge import maintenant
from django.conf import settings
from django.db import transaction
from rest_framework import status

from apps.administration.services import audit
from apps.utilisateurs.models import Adresse, Session, StatutCompte, Utilisateur

# Rôles autorisés à s'inscrire librement (admin/super_admin : scripts ou super-admin).
ROLES_INSCRIVABLES = frozenset({"agriculteur", "acheteur", "ingenieur_agronome", "livreur"})

# Nom et chemin du cookie de rafraîchissement (journal Q-25).
NOM_COOKIE_RAFRAICHISSEMENT = "agrisens_rafraichissement"
CHEMIN_COOKIE_RAFRAICHISSEMENT = "/api/v1/auth"

_TELEPHONE_NUMERES = re.compile(r"^\+?\d[\d \t.-]*$")


def normaliser_telephone(valeur: str) -> str:
    """Normalise un numéro camerounais en E.164 (+237…).

    Accepte « 6XX… », « 06XX… », « 237… », « +237… » avec espaces/tirets.
    Lève ValidationMetier(telephone_invalide) sinon.
    """
    if not valeur or not _TELEPHONE_NUMERES.match(valeur):
        raise ValidationMetier(
            code="telephone_invalide",
            message="Numéro de téléphone invalide.",
            details={"valeur": "format non reconnu"},
        )
    chiffres = re.sub(r"\D", "", valeur)
    if chiffres.startswith("0"):
        chiffres = chiffres[1:]
    if chiffres.startswith("237"):
        chiffres = chiffres[3:]
    if len(chiffres) != 9 or not chiffres.startswith("6"):
        raise ValidationMetier(
            code="telephone_invalide",
            message="Numéro de téléphone camerounais invalide (9 chiffres commençant par 6).",
            details={"champ": "telephone"},
        )
    return f"+237{chiffres}"


def hacher_jeton(jeton_brut: str) -> str:
    """Hachage SHA-256 hexadécimal du jeton de rafraîchissement (jamais en clair)."""
    return hashlib.sha256(jeton_brut.encode("utf-8")).hexdigest()


class SessionService:
    """Sessions = jetons de rafraîchissement opaques avec rotation (Q-25)."""

    @staticmethod
    def generer_jeton_acces(utilisateur: Utilisateur) -> tuple[str, Any]:
        """JWT d'accès 15 min : sub, role, statut_compte, type, iat, exp, jti."""
        expire = maintenant() + timedelta(minutes=settings.JWT_DUREE_ACCES_MIN)
        payload = {
            "sub": str(utilisateur.id),
            "role": utilisateur.role,
            "statut_compte": utilisateur.statut_compte,
            "type": "acces",
            "jti": secrets.token_hex(8),
            "iat": int(maintenant().timestamp()),
            "exp": int(expire.timestamp()),
        }
        jeton = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
        return jeton, expire

    @staticmethod
    def creer_session(utilisateur: Utilisateur, agent: str | None = None) -> tuple[Session, str]:
        """Crée une session et renvoie (session, jeton_de_rafraîchissement_en_clair)."""
        jeton_brut = secrets.token_urlsafe(48)
        session = Session.objects.create(
            utilisateur=utilisateur,
            jeton_hash=hacher_jeton(jeton_brut),
            expire_le=maintenant() + timedelta(days=settings.JWT_DUREE_RAFRAICHISSEMENT_JOURS),
            agent_utilisateur=(agent or "")[:255] or None,
        )
        return session, jeton_brut

    @staticmethod
    def revoquer_toutes_sessions(utilisateur: Utilisateur) -> None:
        """Révoque toutes les sessions valides d'un utilisateur.

        Usages : détection de réutilisation (rotation Q-25), suspension et
        réinitialisation de mot de passe par un admin (prompt F-03 point 4).
        """
        Session.objects.filter(utilisateur=utilisateur, revoque_le__isnull=True).update(
            revoque_le=maintenant()
        )

    # Alias privé historique conservé en interne.
    _revoquer_toutes = revoquer_toutes_sessions

    @classmethod
    def rafraichir(cls, jeton_brut: str) -> tuple[Utilisateur, Session, str]:
        """Valide et fait tourner le jeton de rafraîchissement.

        - jeton inconnu → 401 session_invalide ;
        - jeton déjà consommé ou révoqué → réutilisation : révocation de
          toutes les sessions de l'utilisateur → 401 sessions_revoquees ;
        - session expirée → 401 session_expiree ;
        - compte suspendu → 403 compte_suspendu.
        Renvoie (utilisateur, nouvelle session, nouveau jeton en clair).
        """
        session = (
            Session.objects.select_related("utilisateur").filter(
                jeton_hash=hacher_jeton(jeton_brut)
            )
        ).first()
        if session is None:
            raise ErreurMetier(
                code="session_invalide",
                message="Session invalide. Veuillez vous reconnecter.",
                statut_http=status.HTTP_401_UNAUTHORIZED,
            )

        utilisateur = session.utilisateur
        if session.revoque_le is not None:
            # Réutilisation d'un jeton déjà tourné : on coupe tout (Q-25).
            cls._revoquer_toutes(utilisateur)
            raise ErreurMetier(
                code="sessions_revoquees",
                message="Session expirée pour raison de sécurité. Veuillez vous reconnecter.",
                statut_http=status.HTTP_401_UNAUTHORIZED,
            )
        if session.expire_le <= maintenant():
            session.revoque_le = maintenant()
            session.save(update_fields=["revoque_le", "maj_le"])
            raise ErreurMetier(
                code="session_expiree",
                message="Session expirée. Veuillez vous reconnecter.",
                statut_http=status.HTTP_401_UNAUTHORIZED,
            )
        if utilisateur.statut_compte == StatutCompte.SUSPENDU:
            raise ErreurMetier(
                code="compte_suspendu",
                message="Votre compte est suspendu. Contactez l'administrateur d'AgriSens.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )

        with transaction.atomic():
            session.revoque_le = maintenant()
            session.save(update_fields=["revoque_le", "maj_le"])
            nouvelle_session, nouveau_jeton = cls.creer_session(
                utilisateur, agent=session.agent_utilisateur
            )
        return utilisateur, nouvelle_session, nouveau_jeton

    @classmethod
    def deconnecter(cls, jeton_brut: str | None) -> None:
        """Révoque la session correspondant au jeton fourni (idempotent, jamais d'erreur)."""
        if not jeton_brut:
            return
        Session.objects.filter(jeton_hash=hacher_jeton(jeton_brut), revoque_le__isnull=True).update(
            revoque_le=maintenant()
        )


class CompteService:
    """Inscription, connexion et réinitialisation assistée (Q-23)."""

    @classmethod
    @transaction.atomic
    def inscrire(
        cls,
        *,
        nom: str,
        telephone: str,
        mot_de_passe: str,
        role: str,
        email: str | None = None,
        region: str | None = None,
        agent: str | None = None,
    ) -> tuple[Utilisateur, str, str, Any]:
        """Crée le compte et sa première session.

        Renvoie (utilisateur, jeton d'accès, jeton de rafraîchissement, expiration).
        L'ingénieur agronome est créé en `en_attente_validation` (D-13).
        Téléphone ou email déjà utilisé → 409 compte_existant.
        """
        if role not in ROLES_INSCRIVABLES:
            raise ValidationMetier(
                code="role_inconnu",
                message="Rôle autorisé à l'inscription : agriculteur, acheteur, "
                "ingenieur_agronome ou livreur.",
                details={"champ": "role", "valeurs_autorisees": sorted(ROLES_INSCRIVABLES)},
            )

        telephone_e164 = normaliser_telephone(telephone)
        email_normalise = email.strip().lower() if email and email.strip() else None

        if Utilisateur.objects.filter(telephone=telephone_e164).exists():
            raise Conflit(
                code="compte_existant",
                message="Un compte existe déjà avec ce numéro de téléphone.",
                details={"champ": "telephone"},
            )
        if email_normalise and Utilisateur.objects.filter(email=email_normalise).exists():
            raise Conflit(
                code="compte_existant",
                message="Un compte existe déjà avec cette adresse email.",
                details={"champ": "email"},
            )

        statut = (
            StatutCompte.EN_ATTENTE_VALIDATION
            if role == "ingenieur_agronome"
            else StatutCompte.ACTIF
        )
        utilisateur = Utilisateur.objects.create_user(
            telephone=telephone_e164,
            password=mot_de_passe,
            nom=nom,
            role=role,
            email=email_normalise,
            region=region,
            statut_compte=statut,
        )
        audit.journaliser(
            action="inscription",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=utilisateur,
            nouvelle_valeur={"role": role, "statut_compte": statut},
        )
        jeton_acces, _ = SessionService.generer_jeton_acces(utilisateur)
        session, jeton_rafraichir = SessionService.creer_session(utilisateur, agent=agent)
        return utilisateur, jeton_acces, jeton_rafraichir, session.expire_le

    @classmethod
    @transaction.atomic
    def connecter(
        cls, *, identifiant: str, mot_de_passe: str, agent: str | None = None
    ) -> tuple[Utilisateur, str, str, Any]:
        """Connexion par téléphone ou email ; message identique si l'identifiant
        n'existe pas (pas d'énumération de comptes)."""
        identifiant = (identifiant or "").strip()
        utilisateur: Utilisateur | None = None
        if "@" in identifiant:
            utilisateur = (
                Utilisateur.objects.filter(email__iexact=identifiant)
                .exclude(statut_compte=StatutCompte.ANONYMISE)
                .first()
            )
        else:
            try:
                telephone = normaliser_telephone(identifiant)
            except ValidationMetier:
                telephone = identifiant
            if telephone:
                utilisateur = (
                    Utilisateur.objects.filter(telephone=telephone)
                    .exclude(statut_compte=StatutCompte.ANONYMISE)
                    .first()
                )

        # Message générique identique que le compte existe ou non (sécurité).
        if utilisateur is None or not utilisateur.check_password(mot_de_passe):
            raise ErreurMetier(
                code="identifiants_invalides",
                message="Téléphone (ou email) ou mot de passe incorrect.",
                statut_http=status.HTTP_401_UNAUTHORIZED,
            )

        if utilisateur.statut_compte == StatutCompte.SUSPENDU:
            raise ErreurMetier(
                code="compte_suspendu",
                message="Votre compte est suspendu. Contactez l'administrateur d'AgriSens.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )
        if utilisateur.statut_compte == StatutCompte.ANONYMISE:
            raise ErreurMetier(
                code="compte_inactif",
                message="Ce compte n'est plus actif.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )

        utilisateur.derniere_connexion_le = maintenant()
        utilisateur.save(update_fields=["derniere_connexion_le", "maj_le"])
        jeton_acces, _ = SessionService.generer_jeton_acces(utilisateur)
        session, jeton_rafraichir = SessionService.creer_session(utilisateur, agent=agent)
        return utilisateur, jeton_acces, jeton_rafraichir, session.expire_le

    @staticmethod
    def demander_reinitialisation(telephone: str) -> None:
        """Demande « mot de passe oublié » (Q-23) ; réponse neutre côté vue.

        Le numéro inconnu ne lève aucune erreur : la réponse est identique
        que le compte existe ou non (anti-énumération).
        """
        try:
            telephone_e164 = normaliser_telephone(telephone)
        except ValidationMetier:
            return
        utilisateur = (
            Utilisateur.objects.filter(telephone=telephone_e164)
            .exclude(statut_compte=StatutCompte.ANONYMISE)
            .first()
        )
        if utilisateur is None:
            return
        utilisateur.reinitialisation_demandee_le = maintenant()
        utilisateur.save(update_fields=["reinitialisation_demandee_le", "maj_le"])
        audit.journaliser(
            action="demande_reinitialisation",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=utilisateur,
        )

    @staticmethod
    @transaction.atomic
    def modifier_profil(utilisateur: Utilisateur, donnees: dict[str, Any]) -> Utilisateur:
        """Mise à jour du profil par son propriétaire : nom, email, région, téléphone.

        Le rôle et le statut ne sont jamais modifiables ici.
        # Hypothèse Q-33 : le propriétaire peut changer son propre téléphone
        (avec contrôle d'unicité) ; en attendant validation client.
        """
        if "nom" in donnees and donnees["nom"] is not None:
            utilisateur.nom = str(donnees["nom"]).strip()
        if "region" in donnees:
            utilisateur.region = donnees["region"] or None

        if "email" in donnees:
            email = (donnees["email"] or "").strip().lower() or None
            if email:
                conflit = (
                    Utilisateur.objects.filter(email=email).exclude(id=utilisateur.id).exists()
                )
                if conflit:
                    raise Conflit(
                        code="compte_existant",
                        message="Un compte existe déjà avec cette adresse email.",
                        details={"champ": "email"},
                    )
            utilisateur.email = email

        if "telephone" in donnees and donnees["telephone"] is not None:
            telephone_e164 = normaliser_telephone(str(donnees["telephone"]))
            if telephone_e164 != utilisateur.telephone:
                if Utilisateur.objects.filter(telephone=telephone_e164).exists():
                    raise Conflit(
                        code="compte_existant",
                        message="Un compte existe déjà avec ce numéro de téléphone.",
                        details={"champ": "telephone"},
                    )
                utilisateur.telephone = telephone_e164

        utilisateur.save()
        return utilisateur

    @staticmethod
    @transaction.atomic
    def changer_mot_de_passe(
        utilisateur: Utilisateur, ancien: str, nouveau: str, acteur: Utilisateur | None = None
    ) -> Utilisateur:
        """Change le mot de passe et remet doit_changer_mot_de_passe à false.

        L'ancien mot de passe est exigé, y compris pour un mot de passe
        temporaire (l'utilisateur le connaît).
        """
        if not utilisateur.check_password(ancien):
            raise ErreurMetier(
                code="mot_de_passe_incorrect",
                message="Le mot de passe actuel est incorrect.",
                statut_http=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        utilisateur.set_password(nouveau)
        utilisateur.doit_changer_mot_de_passe = False
        utilisateur.save(update_fields=["password", "doit_changer_mot_de_passe", "maj_le"])
        audit.journaliser(
            action="changement_mot_de_passe",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=acteur or utilisateur,
        )
        return utilisateur


class AdresseService:
    """CRUD des adresses ; une ressource d'un autre utilisateur renvoie 404."""

    @staticmethod
    def _position_or_valide(geojson: Any) -> Any:
        valider_geojson(geojson)
        if geojson.get("type") != "Point":
            raise ValidationMetier(
                code="geometrie_invalide",
                message="La position d'une adresse doit être un GeoJSON Point.",
            )
        return geojson

    @staticmethod
    def lister(utilisateur: Utilisateur) -> list[Adresse]:
        return list(Adresse.objects.filter(utilisateur=utilisateur))

    @staticmethod
    @transaction.atomic
    def creer(
        utilisateur: Utilisateur,
        *,
        libelle: str,
        position: Any,
        ville: str,
        region: str,
        description: str = "",
    ) -> Adresse:
        return Adresse.objects.create(
            utilisateur=utilisateur,
            libelle=libelle,
            description=description or "",
            position=AdresseService._position_or_valide(position),
            ville=ville,
            region=region,
        )

    @classmethod
    def obtenir(cls, utilisateur: Utilisateur, adresse_id: str) -> Adresse:
        adresse = Adresse.objects.filter(id=adresse_id, utilisateur=utilisateur).first()
        if adresse is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Adresse introuvable.",
            )
        return adresse

    @classmethod
    @transaction.atomic
    def modifier(
        cls, utilisateur: Utilisateur, adresse_id: str, donnees: dict[str, Any]
    ) -> Adresse:
        adresse = cls.obtenir(utilisateur, adresse_id)
        for champ in ("libelle", "description", "ville", "region"):
            if champ in donnees and donnees[champ] is not None:
                setattr(adresse, champ, str(donnees[champ]).strip())
        if donnees.get("position") is not None:
            adresse.position = cls._position_or_valide(donnees["position"])
        adresse.save()
        return adresse

    @classmethod
    @transaction.atomic
    def supprimer(cls, utilisateur: Utilisateur, adresse_id: str) -> None:
        adresse = cls.obtenir(utilisateur, adresse_id)
        adresse.delete()


def duree_cookie_secondes(expire_le: Any) -> int:
    """Durée de vie du cookie de rafraîchissement en secondes."""
    delta = expire_le - maintenant()
    return max(int(delta.total_seconds()), 1)

"""Services d'administration des utilisateurs (prompt F-03 backend point 4).

- valider/refuser un ingénieur → module `profils` (ServiceProfilIngenieur) ;
- suspendre (révoque les sessions) / réactiver ;
- demandes de réinitialisation et mot de passe temporaire affiché une
  seule fois (journal Q-23) ;
- création d'admin par super-admin ; compteurs ADM-01.
Tout est journalisé dans JOURNAL_AUDIT ; aucun mot de passe, même
temporaire, n'est écrit dans l'audit ou les logs (AGENTS.md).
"""

import secrets

from commun.exceptions import ErreurMetier, NonTrouve, TransitionInvalide, ValidationMetier
from django.db import transaction
from rest_framework import status

from apps.administration.services import audit, historique
from apps.notifications.services import notifier
from apps.profils.models import IngenieurProfil, StatutValidation
from apps.utilisateurs.models import RoleUtilisateur, StatutCompte, Utilisateur
from apps.utilisateurs.services import SessionService, normaliser_telephone

ROLES_ADMIN = ("admin", "super_admin")

# Jeu de caractères sans homonymes visuels (0/O, 1/l/I) pour la dictée orale.
_ALPHA_MDP = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!#%&*+"
_LONGUEUR_MDP_TEMPORAIRE = 12


def generer_mot_de_passe_temporaire() -> str:
    """Mot de passe aléatoire lisible, conforme à la longueur minimale (≥ 8)."""
    while True:
        mdp = "".join(secrets.choice(_ALPHA_MDP) for _ in range(_LONGUEUR_MDP_TEMPORAIRE))
        if any(c.isdigit() for c in mdp) and any(c in "!#%&*+" for c in mdp):
            return mdp


class ServiceAdminUtilisateurs:
    """Gestion des comptes par un administrateur."""

    # --- Liste et détail -----------------------------------------------------

    @staticmethod
    def lister(
        *,
        role: str | None,
        statut: str | None,
        recherche: str | None,
    ):
        queryset = Utilisateur.objects.exclude(statut_compte=StatutCompte.ANONYMISE)
        if role:
            if role not in RoleUtilisateur.values:
                raise ValidationMetier(
                    code="role_inconnu",
                    message=f"Rôle inconnu : « {role} ».",
                    details={"champ": "role"},
                )
            queryset = queryset.filter(role=role)
        if statut:
            if statut not in StatutCompte.values:
                raise ValidationMetier(
                    code="statut_inconnu",
                    message=f"Statut de compte inconnu : « {statut} ».",
                    details={"champ": "statut"},
                )
            queryset = queryset.filter(statut_compte=statut)
        if recherche:
            terme = recherche.strip()
            queryset = queryset.filter(nom__icontains=terme) | queryset.filter(
                telephone__contains=terme
            )
            queryset = queryset.distinct()
        return queryset.order_by("-cree_le")

    @staticmethod
    def obtenir(id_utilisateur: str) -> Utilisateur:
        utilisateur = (
            Utilisateur.objects.exclude(statut_compte=StatutCompte.ANONYMISE)
            .filter(id=id_utilisateur)
            .select_related("profil_ingenieur")
            .first()
        )
        if utilisateur is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Utilisateur introuvable.",
            )
        return utilisateur

    # --- Suspension / réactivation -------------------------------------------

    @classmethod
    @transaction.atomic
    def suspendre(
        cls,
        admin: Utilisateur,
        id_utilisateur: str,
        motif: str,
        *,
        adresse_ip: str | None = None,
    ) -> Utilisateur:
        """Suspend un compte : statut + révocation de TOUTES les sessions (F-03)."""
        cls._exiger_droit_admin(admin, id_utilisateur)
        utilisateur = cls.obtenir(id_utilisateur)
        if utilisateur.statut_compte == StatutCompte.SUSPENDU:
            raise TransitionInvalide(
                ancien_statut=StatutCompte.SUSPENDU,
                nouveau_statut=StatutCompte.SUSPENDU,
                entite="utilisateur",
                message="Ce compte est déjà suspendu.",
            )
        if utilisateur.statut_compte == StatutCompte.ANONYMISE:
            raise TransitionInvalide(
                ancien_statut=utilisateur.statut_compte,
                nouveau_statut=StatutCompte.SUSPENDU,
                entite="utilisateur",
            )

        ancien = utilisateur.statut_compte
        utilisateur.statut_compte = StatutCompte.SUSPENDU
        utilisateur.save(update_fields=["statut_compte", "maj_le"])
        # Coupure immédiate : plus aucune session ni rafraîchissement possible.
        SessionService.revoquer_toutes_sessions(utilisateur)

        historique.enregistrer_transition(
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            ancien=ancien,
            nouveau=StatutCompte.SUSPENDU,
            acteur=admin,
            motif=motif,
        )
        audit.journaliser(
            action="suspendre_utilisateur",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=admin,
            ancienne_valeur={"statut_compte": ancien},
            nouvelle_valeur={"statut_compte": StatutCompte.SUSPENDU, "motif": motif},
            adresse_ip=adresse_ip,
        )
        notifier(
            utilisateur,
            "administration",
            "Votre compte est suspendu",
            motif,
            lien="/aide/suspension",
        )
        return utilisateur

    @classmethod
    @transaction.atomic
    def reactiver(
        cls,
        admin: Utilisateur,
        id_utilisateur: str,
        *,
        adresse_ip: str | None = None,
    ) -> Utilisateur:
        cls._exiger_droit_admin(admin, id_utilisateur)
        utilisateur = cls.obtenir(id_utilisateur)
        if utilisateur.statut_compte != StatutCompte.SUSPENDU:
            raise TransitionInvalide(
                ancien_statut=utilisateur.statut_compte,
                nouveau_statut=StatutCompte.ACTIF,
                entite="utilisateur",
                message="Seul un compte suspendu peut être réactivé.",
            )

        # Un ingénieur revient au statut dicté par son profil (D-13).
        nouveau = cls._statut_apres_reactivation(utilisateur)
        ancien = utilisateur.statut_compte
        utilisateur.statut_compte = nouveau
        utilisateur.save(update_fields=["statut_compte", "maj_le"])

        historique.enregistrer_transition(
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            ancien=ancien,
            nouveau=nouveau,
            acteur=admin,
        )
        audit.journaliser(
            action="reactiver_utilisateur",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=admin,
            ancienne_valeur={"statut_compte": ancien},
            nouvelle_valeur={"statut_compte": nouveau},
            adresse_ip=adresse_ip,
        )
        notifier(
            utilisateur,
            "administration",
            "Votre compte est réactivé",
            "Vous pouvez de nouveau utiliser AgriSens.",
            lien=None,
        )
        return utilisateur

    @staticmethod
    def _statut_apres_reactivation(utilisateur: Utilisateur) -> str:
        if utilisateur.role == RoleUtilisateur.INGENIEUR_AGRONOME:
            profil = IngenieurProfil.objects.filter(utilisateur_id=utilisateur.id).first()
            if profil is None or profil.statut_validation != StatutValidation.VALIDE:
                return StatutCompte.EN_ATTENTE_VALIDATION
        return StatutCompte.ACTIF

    @staticmethod
    def _exiger_droit_admin(admin: Utilisateur, id_utilisateur: str) -> None:
        """Un admin ne touche pas un compte admin/super_admin ; seul sadm le peut.

        # Hypothèse Q-37 : protection anti-escalade, non précisée par la spec.
        """
        if id_utilisateur == admin.id:
            raise ErreurMetier(
                code="permission_refusee",
                message="Un administrateur ne peut pas agir sur son propre compte.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )
        cible = Utilisateur.objects.filter(id=id_utilisateur).first()
        if (
            cible is not None
            and cible.role in ROLES_ADMIN
            and admin.role != RoleUtilisateur.SUPER_ADMIN
        ):
            raise ErreurMetier(
                code="permission_refusee",
                message="Seul un super-administrateur agit sur un compte d'administration.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )

    # --- Réinitialisation Q-23 ------------------------------------------------

    @classmethod
    @transaction.atomic
    def reinitialiser_mot_de_passe(
        cls,
        admin: Utilisateur,
        id_utilisateur: str,
        *,
        adresse_ip: str | None = None,
    ) -> tuple[Utilisateur, str]:
        """Émet un mot de passe temporaire affiché une seule fois (Q-23).

        Renvoie (utilisateur, mot_de_passe_en_clair) — jamais journalisé,
        jamais notifié : l'admin le transmet hors plateforme.
        """
        cls._exiger_droit_admin(admin, id_utilisateur)
        utilisateur = cls.obtenir(id_utilisateur)

        mdp_temporaire = generer_mot_de_passe_temporaire()
        utilisateur.set_password(mdp_temporaire)
        utilisateur.doit_changer_mot_de_passe = True
        utilisateur.reinitialisation_demandee_le = None
        utilisateur.save(
            update_fields=[
                "password",
                "doit_changer_mot_de_passe",
                "reinitialisation_demandee_le",
                "maj_le",
            ]
        )
        # L'ancien rafraîchissement ne doit plus donner accès à la session.
        SessionService.revoquer_toutes_sessions(utilisateur)

        audit.journaliser(
            action="reinitialisation_mot_de_passe",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=admin,
            nouvelle_valeur={"doit_changer_mot_de_passe": True},
            adresse_ip=adresse_ip,
        )
        notifier(
            utilisateur,
            "administration",
            "Mot de passe réinitialisé",
            "Un mot de passe temporaire vous a été communiqué ; changez-le à la "
            "prochaine connexion.",
            lien="/connexion",
        )
        return utilisateur, mdp_temporaire

    @staticmethod
    def lister_demandes_reinitialisation():
        """Comptes avec une demande « mot de passe oublié » en attente (ADM-01/02)."""
        return (
            Utilisateur.objects.filter(reinitialisation_demandee_le__isnull=False)
            .exclude(statut_compte=StatutCompte.ANONYMISE)
            .order_by("reinitialisation_demandee_le")
        )

    # --- Création d'admin (super-admin) ---------------------------------------

    @classmethod
    @transaction.atomic
    def creer_admin(
        cls,
        super_admin: Utilisateur,
        *,
        nom: str,
        telephone: str,
        role: str,
        email: str | None = None,
        region: str | None = None,
        adresse_ip: str | None = None,
    ) -> tuple[Utilisateur, str]:
        """POST /admin/utilisateurs — réservé super-admin ; mot de passe une fois."""
        if super_admin.role != RoleUtilisateur.SUPER_ADMIN:
            raise ErreurMetier(
                code="permission_refusee",
                message="Création d'un compte d'administration réservée au super-admin.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )
        telephone_e164 = normaliser_telephone(telephone)
        if Utilisateur.objects.filter(telephone=telephone_e164).exists():
            raise ValidationMetier(
                code="compte_existant",
                message="Un compte existe déjà avec ce numéro de téléphone.",
                details={"champ": "telephone"},
            )

        mdp_temporaire = generer_mot_de_passe_temporaire()
        utilisateur = Utilisateur.objects.create_user(
            telephone=telephone_e164,
            password=mdp_temporaire,
            nom=nom,
            role=role,
            email=(email or "").strip() or None,
            region=region or None,
            statut_compte=StatutCompte.ACTIF,
            doit_changer_mot_de_passe=True,
        )
        audit.journaliser(
            action="creation_admin",
            entite_type="utilisateur",
            entite_id=utilisateur.id,
            acteur=super_admin,
            nouvelle_valeur={"role": role},
            adresse_ip=adresse_ip,
        )
        return utilisateur, mdp_temporaire

    # --- Compteurs ADM-01 ------------------------------------------------------

    @staticmethod
    def compteurs() -> dict[str, int]:
        return {
            "ingenieurs_en_attente": IngenieurProfil.objects.filter(
                statut_validation=StatutValidation.EN_ATTENTE
            ).count(),
            "reinitialisations_en_attente": Utilisateur.objects.filter(
                reinitialisation_demandee_le__isnull=False
            )
            .exclude(statut_compte=StatutCompte.ANONYMISE)
            .count(),
            "comptes_suspendus": Utilisateur.objects.filter(
                statut_compte=StatutCompte.SUSPENDU
            ).count(),
        }


# Alias d'instance réutilisable (même style que parametres/audit).
admin_utilisateurs = ServiceAdminUtilisateurs()

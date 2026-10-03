"""Logique métier des litiges (SPEC-05 remboursements, SPEC-10 signalements).

Machine à états du remboursement (02 §3.5) avec exécution **manuelle** en V1
(Q-26) : l'admin examine, approuve (total ou partiel) puis enregistre la
référence du renvoi Mobile Money effectué hors plateforme. Aucun appel réseau.

Les transitions de commande restent la propriété du module `commandes` :
ce service appelle les méthodes publiques `ServiceCommande.*` correspondantes.
"""

from datetime import datetime, timedelta
from typing import Any

from commun.exceptions import Conflit, NonTrouve, ValidationMetier
from commun.horloge import maintenant
from django.db import transaction

from apps.administration.models import HistoriqueStatut
from apps.administration.services import audit, parametres
from apps.commandes.models import Commande, StatutCommande
from apps.commandes.services import ServiceCommande
from apps.fichiers.models import Fichier
from apps.litiges.models import (
    MOTIFS_JUSTIFICATIF_OBLIGATOIRE,
    STATUTS_ACTIFS_REMBOURSEMENT,
    Evaluation,
    Remboursement,
    Signalement,
    StatutRemboursement,
    StatutSignalement,
)
from apps.notifications.services import notifier
from apps.paiements.models import Paiement, StatutPaiement
from apps.utilisateurs.models import RoleUtilisateur, Utilisateur

# Événements générateurs du délai de demande (07 §15.3).
STATUTS_DECLENCHEURS = (
    StatutCommande.LIVREE,
    StatutCommande.CLIENT_ABSENT,
    StatutCommande.ECHEC_LIVRAISON,
)

TRANSITIONS_REMBOURSEMENT: dict[str, frozenset[str]] = {
    StatutRemboursement.DEMANDE: frozenset({StatutRemboursement.EN_EXAMEN}),
    StatutRemboursement.EN_EXAMEN: frozenset(
        {StatutRemboursement.APPROUVE, StatutRemboursement.REFUSE}
    ),
    StatutRemboursement.APPROUVE: frozenset({StatutRemboursement.EFFECTUE}),
    StatutRemboursement.EN_COURS: frozenset(
        {StatutRemboursement.EFFECTUE, StatutRemboursement.ECHOUE}
    ),
    StatutRemboursement.ECHOUE: frozenset({StatutRemboursement.EN_COURS}),
}


def _historiser(
    remboursement: Remboursement, ancien: str | None, nouveau: str, acteur: Any, motif: str | None
) -> None:
    HistoriqueStatut.objects.create(
        entite_type="remboursement",
        entite_id=remboursement.id,
        ancien_statut=ancien,
        nouveau_statut=nouveau,
        acteur=acteur,
        motif=motif,
    )


def _transition(remboursement: Remboursement, nouveau: str, acteur: Any, motif: str | None) -> None:
    autorises = TRANSITIONS_REMBOURSEMENT.get(remboursement.statut, frozenset())
    if nouveau not in autorises:
        from commun.exceptions import TransitionInvalide

        raise TransitionInvalide(remboursement.statut, nouveau, "remboursement")
    ancien = remboursement.statut
    remboursement.statut = nouveau
    remboursement.save(update_fields=["statut", "maj_le"])
    _historiser(remboursement, ancien, nouveau, acteur, motif)


def _administrateurs() -> list[Utilisateur]:
    return list(
        Utilisateur.objects.filter(
            role__in=(RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN),
            statut_compte="actif",
        )
    )


def _liens_justificatifs(remboursement: Remboursement, fichiers: list[Fichier]) -> None:
    for fichier in fichiers:
        remboursement.justificatifs.add(fichier)


class ServiceRemboursement:
    """Demande, examen, approbation/refus et exécution manuelle (Q-26)."""

    @staticmethod
    def _paiement_reussi(commande: Commande) -> Paiement | None:
        return (
            Paiement.objects.filter(commande=commande, statut=StatutPaiement.REUSSI)
            .order_by("-cree_le")
            .first()
        )

    @staticmethod
    def _moment_eligibilite(commande: Commande) -> datetime | None:
        """Dernier passage en `livree`/`client_absent`/`echec_livraison`."""
        ligne = (
            HistoriqueStatut.objects.filter(
                entite_type="commande",
                entite_id=commande.id,
                nouveau_statut__in=STATUTS_DECLENCHEURS,
            )
            .order_by("-cree_le")
            .first()
        )
        return ligne.cree_le if ligne is not None else None

    # --- Création d'une demande ---------------------------------------------

    @classmethod
    @transaction.atomic
    def demander(
        cls,
        acheteur: Any,
        commande_id: str,
        motif: str,
        montant_demande_fcfa: int,
        justificatif_ids: list[str] | None = None,
        signalement_id: str | None = None,
    ) -> Remboursement:
        commande = (
            Commande.objects.select_for_update().filter(id=commande_id, acheteur=acheteur).first()
        )
        if commande is None:
            raise NonTrouve()
        if Remboursement.objects.filter(
            commande=commande, statut__in=STATUTS_ACTIFS_REMBOURSEMENT
        ).exists():
            raise Conflit(
                code="remboursement_deja_demande",
                message="Une demande de remboursement est déjà en cours pour cette commande.",
            )
        if commande.statut not in STATUTS_DECLENCHEURS:
            raise ValidationMetier(
                "commande_non_remboursable",
                "Un remboursement ne peut être demandé qu'après livraison ou incident.",
                {"statut": commande.statut},
            )
        paiement = cls._paiement_reussi(commande)
        if paiement is None:
            raise ValidationMetier(
                "paiement_introuvable", "Aucun paiement réussi pour cette commande."
            )
        if montant_demande_fcfa <= 0 or montant_demande_fcfa > paiement.montant_fcfa:
            raise ValidationMetier(
                "montant_invalide",
                "Le montant demandé doit être compris entre 1 FCFA et le montant payé.",
                {"montant_paye_fcfa": paiement.montant_fcfa},
            )

        ids = list(dict.fromkeys(justificatif_ids or []))
        if motif in MOTIFS_JUSTIFICATIF_OBLIGATOIRE and not ids:
            raise ValidationMetier(
                "justificatif_requis",
                "Un justificatif (photo ou fichier) est obligatoire pour ce motif.",
                {"champ": "justificatifs", "motif": motif},
            )
        fichiers: list[Fichier] = []
        if ids:
            fichiers = list(Fichier.objects.filter(id__in=ids, proprietaire=acheteur))
            if len(fichiers) != len(ids):
                raise NonTrouve()

        delai_h = int(parametres.obtenir("remboursement.delai_demande_h"))
        moment = cls._moment_eligibilite(commande)
        if moment is None or maintenant() > moment + timedelta(hours=delai_h):
            raise ValidationMetier(
                "delai_depasse",
                f"Le délai de demande de remboursement ({delai_h} h) est dépassé.",
                {"delai_h": delai_h},
            )

        signalement: Signalement | None = None
        if signalement_id:
            signalement = ServiceSignalement.obtenir_visible(acheteur, signalement_id)

        origine = commande.statut
        remboursement = Remboursement.objects.create(
            paiement=paiement,
            commande=commande,
            demandeur=acheteur,
            signalement=signalement,
            motif=motif,
            montant_demande_fcfa=montant_demande_fcfa,
            statut=StatutRemboursement.DEMANDE,
            statut_commande_origine=origine,
        )
        _liens_justificatifs(remboursement, fichiers)
        _historiser(remboursement, None, StatutRemboursement.DEMANDE, acheteur, motif)
        ServiceCommande.marquer_remboursement_en_attente(
            str(commande.id), acheteur, "Demande de remboursement de l'acheteur."
        )
        for admin in _administrateurs():
            notifier(
                admin,
                "commande",
                f"Nouvelle demande de remboursement {commande.reference}",
                f"{montant_demande_fcfa} FCFA — motif {motif}.",
                lien="/admin/litiges",
            )
        return remboursement

    @classmethod
    @transaction.atomic
    def depuis_annulation(cls, commande: Commande, acteur: Any) -> Remboursement:
        """Annulation après paiement : remboursement intégral à examiner (§3.1)."""
        paiement = cls._paiement_reussi(commande)
        if paiement is None:
            raise ValidationMetier(
                "paiement_introuvable", "Aucun paiement réussi pour cette commande."
            )
        remboursement = Remboursement.objects.create(
            paiement=paiement,
            commande=commande,
            demandeur=commande.acheteur,
            motif="annulation",
            montant_demande_fcfa=paiement.montant_fcfa,
            statut=StatutRemboursement.DEMANDE,
            statut_commande_origine=commande.statut,
        )
        _historiser(
            remboursement, None, StatutRemboursement.DEMANDE, acteur, "Annulation après paiement."
        )
        for admin in _administrateurs():
            notifier(
                admin,
                "commande",
                f"Remboursement intégral — {commande.reference}",
                f"Commande annulée après paiement : {paiement.montant_fcfa} FCFA à examiner.",
                lien="/admin/litiges",
            )
        return remboursement

    # --- Décisions admin -----------------------------------------------------

    @classmethod
    @transaction.atomic
    def examiner(cls, admin: Any, identifiant: str) -> Remboursement:
        remboursement = cls._obtenir_admin(identifiant)
        _transition(remboursement, StatutRemboursement.EN_EXAMEN, admin, None)
        return remboursement

    @classmethod
    @transaction.atomic
    def approuver(cls, admin: Any, identifiant: str, montant_accorde_fcfa: int) -> Remboursement:
        remboursement = cls._obtenir_admin(identifiant)
        if montant_accorde_fcfa <= 0 or montant_accorde_fcfa > remboursement.paiement.montant_fcfa:
            raise ValidationMetier(
                "montant_accorde_invalide",
                "Le montant accordé doit être compris entre 1 FCFA et le montant payé.",
                {"montant_paye_fcfa": remboursement.paiement.montant_fcfa},
            )
        if remboursement.statut == StatutRemboursement.DEMANDE:
            _transition(remboursement, StatutRemboursement.EN_EXAMEN, admin, None)
        _transition(remboursement, StatutRemboursement.APPROUVE, admin, None)
        remboursement.montant_accorde_fcfa = montant_accorde_fcfa
        remboursement.decide_par = admin
        remboursement.decide_le = maintenant()
        remboursement.save(
            update_fields=["montant_accorde_fcfa", "decide_par", "decide_le", "maj_le"]
        )
        notifier(
            remboursement.demandeur,
            "commande",
            f"Remboursement approuvé {remboursement.commande.reference}",
            f"{montant_accorde_fcfa} FCFA accordés, en cours d'exécution.",
            lien=f"/commandes/{remboursement.commande_id}",
        )
        return remboursement

    @classmethod
    @transaction.atomic
    def refuser(cls, admin: Any, identifiant: str, motif_decision: str) -> Remboursement:
        remboursement = cls._obtenir_admin(identifiant)
        motif_propre = (motif_decision or "").strip()
        if not motif_propre:
            raise ValidationMetier(
                "motif_obligatoire", "Le motif du refus est obligatoire.", {"champ": "motif"}
            )
        if remboursement.statut == StatutRemboursement.DEMANDE:
            _transition(remboursement, StatutRemboursement.EN_EXAMEN, admin, None)
        _transition(remboursement, StatutRemboursement.REFUSE, admin, motif_propre)
        remboursement.motif_decision = motif_propre
        remboursement.decide_par = admin
        remboursement.decide_le = maintenant()
        remboursement.save(update_fields=["motif_decision", "decide_par", "decide_le", "maj_le"])
        # La commande retrouve son statut d'avant la demande (§3.1).
        ServiceCommande.retour_apres_refus_remboursement(
            str(remboursement.commande_id),
            remboursement.statut_commande_origine,
            admin,
            "Remboursement refusé.",
        )
        notifier(
            remboursement.demandeur,
            "commande",
            f"Remboursement refusé {remboursement.commande.reference}",
            motif_propre,
            lien=f"/commandes/{remboursement.commande_id}",
        )
        return remboursement

    @classmethod
    @transaction.atomic
    def executer(cls, admin: Any, identifiant: str, reference_operateur: str) -> Remboursement:
        remboursement = cls._obtenir_admin(identifiant)
        if remboursement.statut != StatutRemboursement.APPROUVE:
            from commun.exceptions import TransitionInvalide

            raise TransitionInvalide(
                remboursement.statut, StatutRemboursement.EFFECTUE, "remboursement"
            )
        reference = (reference_operateur or "").strip()
        if not reference:
            raise ValidationMetier(
                "reference_obligatoire",
                "La référence du renvoi est obligatoire.",
                {"champ": "reference_operateur"},
            )
        _transition(remboursement, StatutRemboursement.EFFECTUE, admin, reference)
        remboursement.reference_operateur = reference
        remboursement.save(update_fields=["reference_operateur", "maj_le"])
        ServiceCommande.marquer_remboursee(
            str(remboursement.commande_id), admin, "Remboursement exécuté."
        )
        notifier(
            remboursement.demandeur,
            "commande",
            f"Commande {remboursement.commande.reference} remboursée",
            f"{remboursement.montant_accorde_fcfa} FCFA remboursés (réf. {reference}).",
            lien=f"/commandes/{remboursement.commande_id}",
        )
        return remboursement

    # --- Lecture -------------------------------------------------------------

    @staticmethod
    def mes_demandes(acheteur: Any) -> list[Remboursement]:
        return list(
            Remboursement.objects.filter(demandeur=acheteur).select_related("commande", "paiement")
        )

    @staticmethod
    def file_admin(statut: str | None = None) -> list[Remboursement]:
        requete = Remboursement.objects.select_related("commande", "paiement", "demandeur")
        if statut:
            requete = requete.filter(statut=statut)
        return list(requete)

    @classmethod
    def obtenir_visible(cls, utilisateur: Any, identifiant: str) -> Remboursement:
        remboursement = (
            Remboursement.objects.filter(id=identifiant)
            .select_related("commande", "paiement", "signalement")
            .first()
        )
        if remboursement is None:
            raise NonTrouve()
        role = getattr(utilisateur, "role", None)
        if role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            return remboursement
        if remboursement.demandeur_id == getattr(utilisateur, "id", None):
            return remboursement
        raise NonTrouve()

    @classmethod
    def _obtenir_admin(cls, identifiant: str) -> Remboursement:
        remboursement = (
            Remboursement.objects.select_related("commande", "paiement", "demandeur")
            .filter(id=identifiant)
            .first()
        )
        if remboursement is None:
            raise NonTrouve()
        return remboursement


class ServiceSignalement:
    """Signalements sur utilisateur / produit / commande / livraison (+ audit)."""

    @classmethod
    @transaction.atomic
    def creer(
        cls,
        auteur: Any,
        cible_type: str,
        cible_id: str,
        motif: str,
        description: str,
        fichier_ids: list[str] | None = None,
    ) -> Signalement:
        description_propre = (description or "").strip()
        if not description_propre:
            raise ValidationMetier(
                "description_requise", "La description du signalement est obligatoire."
            )
        ids = list(dict.fromkeys(fichier_ids or []))
        fichiers: list[Fichier] = []
        if ids:
            fichiers = list(Fichier.objects.filter(id__in=ids, proprietaire=auteur))
            if len(fichiers) != len(ids):
                raise NonTrouve()
        signalement = Signalement.objects.create(
            auteur=auteur,
            cible_type=cible_type,
            cible_id=cible_id,
            motif=(motif or "").strip()[:50],
            description=description_propre,
            statut=StatutSignalement.OUVERT,
        )
        for fichier in fichiers:
            signalement.fichiers.add(fichier)
        audit.journaliser(
            action="creer_signalement",
            entite_type="signalement",
            entite_id=signalement.id,
            acteur=auteur,
            nouvelle_valeur={"cible_type": cible_type, "cible_id": cible_id, "motif": motif},
        )
        for admin in _administrateurs():
            notifier(
                admin,
                "signalement",
                "Nouveau signalement à examiner",
                f"{cible_type} — {motif}.",
                lien="/admin/litiges",
            )
        return signalement

    @staticmethod
    def mes_signalements(auteur: Any) -> list[Signalement]:
        return list(Signalement.objects.filter(auteur=auteur).prefetch_related("fichiers"))

    @staticmethod
    def file_admin(statut: str | None = None) -> list[Signalement]:
        requete = Signalement.objects.select_related("auteur").prefetch_related("fichiers")
        if statut:
            requete = requete.filter(statut=statut)
        return list(requete)

    @classmethod
    @transaction.atomic
    def traiter(
        cls,
        admin: Any,
        identifiant: str,
        statut_cible: str,
        decision: str,
    ) -> Signalement:
        signalement = cls.obtenir_visible(admin, identifiant)
        if statut_cible not in StatutSignalement.values:
            raise ValidationMetier("statut_inconnu", "Statut de décision inconnu.")
        if statut_cible == StatutSignalement.OUVERT:
            raise ValidationMetier(
                "statut_invalide", "Un signalement ne peut revenir à « ouvert »."
            )
        decision_propre = (decision or "").strip()
        cloture = statut_cible in (
            StatutSignalement.CLOS_FONDE,
            StatutSignalement.CLOS_NON_FONDE,
        )
        if cloture and not decision_propre:
            raise ValidationMetier(
                "decision_requise", "Une décision est obligatoire pour clôturer."
            )
        ancien = signalement.statut
        signalement.statut = statut_cible
        signalement.traite_par = admin
        if decision_propre:
            signalement.decision = decision_propre
        signalement.save(update_fields=["statut", "traite_par", "decision", "maj_le"])
        audit.journaliser(
            action="traiter_signalement",
            entite_type="signalement",
            entite_id=signalement.id,
            acteur=admin,
            ancienne_valeur={"statut": ancien},
            nouvelle_valeur={"statut": statut_cible, "decision": decision_propre},
        )
        notifier(
            signalement.auteur,
            "signalement",
            "Votre signalement a été traité",
            decision_propre or statut_cible,
            lien="/signalements",
        )
        return signalement

    @classmethod
    def obtenir_visible(cls, utilisateur: Any, identifiant: str) -> Signalement:
        signalement = Signalement.objects.filter(id=identifiant).select_related("auteur").first()
        if signalement is None:
            raise NonTrouve()
        role = getattr(utilisateur, "role", None)
        if role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            return signalement
        if signalement.auteur_id == getattr(utilisateur, "id", None):
            return signalement
        raise NonTrouve()


class ServiceEvaluation:
    """Évaluation d'une commande livrée (02 §2.3, D-31 / Q-12).

    Seulement si la commande est `livree`, une seule évaluation par
    commande, écrite par l'acheteur. La note du livreur n'est possible
    que si une livraison a été affectée.
    """

    @classmethod
    @transaction.atomic
    def creer(
        cls,
        acheteur: Any,
        commande_id: str,
        note_agriculteur: int,
        note_livreur: int | None = None,
        commentaire: str | None = None,
    ) -> Evaluation:
        commande = (
            Commande.objects.select_for_update().filter(id=commande_id, acheteur=acheteur).first()
        )
        if commande is None:
            raise NonTrouve()
        if commande.statut != StatutCommande.LIVREE:
            raise ValidationMetier(
                "commande_non_livree",
                "Une commande ne peut être évaluée qu'après livraison.",
                {"statut": commande.statut},
            )
        if Evaluation.objects.filter(commande=commande).exists():
            raise Conflit(
                code="evaluation_deja_faite",
                message="Cette commande a déjà été évaluée.",
            )
        livraison = getattr(commande, "livraison", None)
        if note_livreur is not None and livraison is None:
            raise ValidationMetier(
                "livreur_absent",
                "Aucun livreur n'a été affecté à cette commande.",
                {"champ": "note_livreur"},
            )
        commentaire_propre = (commentaire or "").strip() or None
        evaluation = Evaluation.objects.create(
            commande=commande,
            acheteur=acheteur,
            note_agriculteur=note_agriculteur,
            note_livreur=note_livreur,
            commentaire=commentaire_propre,
        )
        audit.journaliser(
            action="creer_evaluation",
            entite_type="evaluation",
            entite_id=evaluation.id,
            acteur=acheteur,
            nouvelle_valeur={
                "commande_id": str(commande.id),
                "note_agriculteur": note_agriculteur,
                "note_livreur": note_livreur,
            },
        )
        notifier(
            commande.agriculteur,
            "commande",
            f"Commande {commande.reference} évaluée",
            f"Note reçue : {note_agriculteur}/5.",
            lien=f"/agriculteur/commandes/{commande.id}",
        )
        if livraison is not None and note_livreur is not None:
            notifier(
                livraison.livreur,
                "livraison",
                f"Livraison {commande.reference} évaluée",
                f"Note reçue : {note_livreur}/5.",
                lien=f"/livreur/livraisons/{commande.id}",
            )
        return evaluation

    @staticmethod
    def evaluation_de(acheteur: Any, commande_id: str) -> Evaluation | None:
        """Évaluation écrite par cet acheteur pour cette commande (404 côté vue)."""
        return (
            Evaluation.objects.filter(commande_id=commande_id, acheteur=acheteur)
            .select_related("commande")
            .first()
        )

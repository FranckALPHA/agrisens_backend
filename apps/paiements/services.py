"""Logique métier des paiements de commandes (SPEC-05, partie paiement).

Confirmation UNIQUEMENT par webhook signé et idempotent : le retour
navigateur ne change jamais aucun statut. Les transitions de commandes
restent dans `apps/commandes/services.py` (pas de cycle d'imports).
"""

import hashlib
import json
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

from commun.evenements import publier
from commun.exceptions import (
    Conflit,
    ErreurFournisseur,
    NonTrouve,
    TransitionInvalide,
    ValidationMetier,
)
from commun.horloge import maintenant
from django.db import IntegrityError, transaction

from apps.administration.services import audit, parametres
from apps.commandes.models import Commande, CompteurReference, StatutCommande
from apps.commandes.services import ServiceCommande
from apps.commandes.tarification import arrondir_fcfa
from apps.paiements.evenements import PaiementReussi
from apps.paiements.models import (
    EvenementWebhook,
    ObjetPaiement,
    OperateurPaiement,
    Paiement,
    Reversement,
    StatutPaiement,
)
from apps.paiements.passerelle import passerelle_paiement
from apps.utilisateurs.models import RoleUtilisateur, Utilisateur
from apps.utilisateurs.services import normaliser_telephone

OPERATEURS_VALIDES = tuple(OperateurPaiement.values)

# Webhook NotchPay (contrat vérifié sur projet sandbox-testé).
TYPES_SUCCES = {"payment.complete"}
TYPES_ECHEC = {"payment.failed", "payment.canceled"}
TYPES_EXPIRE = {"payment.expired"}


def _nouvelle_reference_interne() -> str:
    annee = maintenant().year
    with transaction.atomic():
        compteur, _ = CompteurReference.objects.select_for_update().get_or_create(
            prefixe="PAY", annee=annee, defaults={"dernier_numero": 0}
        )
        compteur.dernier_numero += 1
        compteur.save(update_fields=["dernier_numero", "maj_le"])
        return f"PAY-{annee}-{compteur.dernier_numero:06d}"


def _nettoyer_charge(charge: Any) -> Any:
    """Retire les données personnelles de la charge stockée (conventions §6)."""
    if isinstance(charge, dict):
        propre = {}
        for cle, valeur in charge.items():
            if cle.lower() in ("phone", "telephone", "email", "customer"):
                propre[cle] = "***"
            else:
                propre[cle] = _nettoyer_charge(valeur)
        return propre
    if isinstance(charge, list):
        return [_nettoyer_charge(v) for v in charge]
    return charge


def _commission_figee(commande: Commande) -> tuple[int, int]:
    """Commission sur le sous-total seul, taux figé à la création (P-01)."""
    taux = int(
        (commande.parametres_appliques or {}).get(
            "commission.taux_pb", parametres.obtenir("commission.taux_pb")
        )
    )
    montant = arrondir_fcfa(Decimal(commande.sous_total_fcfa) * taux / 10_000)
    return taux, montant


class ServicePaiement:
    """Initialisation, consultation, webhooks et vérification."""

    # --- Initialisation ----------------------------------------------------

    @staticmethod
    @transaction.atomic
    def initier_paiement_commande(
        acheteur: Any,
        commande_id: str,
        operateur: str,
        telephone: str | None = None,
        cle_idempotence: str | None = None,
        url_base_pwa: str = "",
    ) -> tuple[Paiement, str, bool]:
        """Crée le paiement, bascule la commande, renvoie (paiement, url, rejoue)."""
        commande = (
            Commande.objects.select_for_update().filter(id=commande_id, acheteur=acheteur).first()
        )
        if commande is None:
            raise NonTrouve()
        if cle_idempotence:
            existant = Paiement.objects.filter(cle_idempotence=cle_idempotence).first()
            if existant is not None:
                if (
                    existant.commande_id != commande.id
                    or existant.montant_fcfa != commande.total_fcfa
                ):
                    raise Conflit(
                        code="cle_idempotence_differente",
                        message="Clé d'idempotence déjà utilisée pour un autre paiement.",
                    )
                return existant, existant.url_paiement or "", True
        if commande.statut != StatutCommande.ACCEPTEE:
            raise TransitionInvalide(commande.statut, "paiement_en_attente", "commande")
        if operateur not in OPERATEURS_VALIDES:
            raise ValidationMetier(
                "operateur_inconnu",
                "Opérateur inconnu (mtn_momo, orange_money).",
                {"operateur": operateur},
            )
        numero = None
        if telephone:
            try:
                numero = normaliser_telephone(telephone)
            except ValidationMetier:
                raise ValidationMetier(
                    "telephone_invalide",
                    "Numéro de téléphone invalide (format E.164, ex. +2376XXXXXXXX).",
                ) from None

        if cle_idempotence:
            existant = Paiement.objects.filter(cle_idempotence=cle_idempotence).first()
            if existant is not None:
                if (
                    existant.commande_id != commande.id
                    or existant.montant_fcfa != commande.total_fcfa
                ):
                    raise Conflit(
                        code="cle_idempotence_differente",
                        message="Clé d'idempotence déjà utilisée pour un autre paiement.",
                    )
                return existant, existant.url_paiement or "", True

        paiement = Paiement.objects.create(
            objet=ObjetPaiement.COMMANDE,
            commande=commande,
            payeur=acheteur,
            montant_fcfa=commande.total_fcfa,
            operateur=operateur,
            reference_interne=_nouvelle_reference_interne(),
            cle_idempotence=cle_idempotence or secrets.token_urlsafe(24),
            statut=StatutPaiement.INITIE,
        )
        # Échec d'initialisation : la transaction englobante est annulée, comme
        # si rien n'avait été créé — la même clé peut réessayer sans résidu.
        resultat = passerelle_paiement().initialiser(
            montant_fcfa=commande.total_fcfa,
            reference_interne=paiement.reference_interne,
            description=f"Commande {commande.reference} — AgriSens",
            retour_url=f"{url_base_pwa}/paiements/retour?reference={paiement.reference_interne}",
            telephone=numero,
            email=getattr(acheteur, "email", None),
            nom_client=getattr(acheteur, "nom", None),
        )
        paiement.reference_notchpay = resultat["reference_externe"]
        paiement.url_paiement = resultat["url_paiement"]
        paiement.statut = StatutPaiement.EN_ATTENTE
        paiement.save()
        ServiceCommande.passer_en_paiement(str(commande.id))
        return paiement, resultat["url_paiement"], False

    @staticmethod
    @transaction.atomic
    def initier_reprogrammation(
        commande: Commande,
        montant_fcfa: int,
        operateur: str | None,
        telephone: str | None,
        cle_idempotence: str | None,
        url_base_pwa: str = "",
    ) -> tuple[Paiement, str, bool]:
        """Crée le paiement des nouveaux frais (client absent, Q-04).

        Sans transition de commande : la re-proposition a lieu au webhook
        de succès (`finaliser_reprogrammation_payee`).
        """
        if operateur not in OPERATEURS_VALIDES:
            raise ValidationMetier(
                "operateur_inconnu",
                "Opérateur inconnu (mtn_momo, orange_money).",
                {"operateur": operateur},
            )
        numero = None
        if telephone:
            try:
                numero = normaliser_telephone(telephone)
            except ValidationMetier:
                raise ValidationMetier(
                    "telephone_invalide",
                    "Numéro de téléphone invalide (format E.164, ex. +2376XXXXXXXX).",
                ) from None
        if cle_idempotence:
            existant = Paiement.objects.filter(cle_idempotence=cle_idempotence).first()
            if existant is not None:
                if existant.commande_id != commande.id or existant.montant_fcfa != montant_fcfa:
                    raise Conflit(
                        code="cle_idempotence_differente",
                        message="Clé d'idempotence déjà utilisée pour un autre paiement.",
                    )
                return existant, existant.url_paiement or "", True
        paiement = Paiement.objects.create(
            objet=ObjetPaiement.COMMANDE,
            commande=commande,
            payeur=commande.acheteur,
            montant_fcfa=montant_fcfa,
            operateur=operateur,
            reference_interne=_nouvelle_reference_interne(),
            cle_idempotence=cle_idempotence or secrets.token_urlsafe(24),
            statut=StatutPaiement.INITIE,
        )
        resultat = passerelle_paiement().initialiser(
            montant_fcfa=montant_fcfa,
            reference_interne=paiement.reference_interne,
            description=f"Nouveaux frais de livraison {commande.reference} — AgriSens",
            retour_url=f"{url_base_pwa}/paiements/retour?reference={paiement.reference_interne}",
            telephone=numero,
            email=getattr(commande.acheteur, "email", None),
            nom_client=getattr(commande.acheteur, "nom", None),
        )
        paiement.reference_notchpay = resultat["reference_externe"]
        paiement.url_paiement = resultat["url_paiement"]
        paiement.statut = StatutPaiement.EN_ATTENTE
        paiement.save()
        return paiement, resultat["url_paiement"], False

    # --- Consultation ------------------------------------------------------

    @staticmethod
    def obtenir_paiement(utilisateur: Any, paiement_id: str) -> Paiement:
        paiement = Paiement.objects.filter(id=paiement_id).first()
        if paiement is None:
            raise NonTrouve()
        if paiement.payeur_id != getattr(utilisateur, "id", None) and getattr(
            utilisateur, "role", None
        ) not in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            raise NonTrouve()
        return paiement

    # --- Webhooks ----------------------------------------------------------

    @staticmethod
    def traiter_webhook(entetes: Any, corps: bytes) -> tuple[int, dict[str, Any]]:
        """Point d'entrée unique des webhooks (route + simulateur).

        Renvoie (statut_http, corps_json). 401 si signature invalide ;
        400 si JSON illisible ou événement inexploitable ; 200 sinon
        (traité, déjà traité, ignoré ou enregistré en erreur — sans
        nouvel essai forcé côté fournisseur).
        """
        try:
            evenement_recu = json.loads(corps.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400, {
                "erreur": {
                    "code": "webhook_illisible",
                    "message": "Corps du webhook illisible.",
                    "details": {},
                }
            }
        if not isinstance(evenement_recu, dict):
            return 400, {
                "erreur": {
                    "code": "webhook_illisible",
                    "message": "Corps du webhook illisible.",
                    "details": {},
                }
            }
        if not passerelle_paiement().verifier_signature(entetes, corps):
            return 401, {
                "erreur": {
                    "code": "signature_invalide",
                    "message": "Signature du webhook invalide.",
                    "details": {},
                }
            }
        identifiant = evenement_recu.get("id")
        type_evt = evenement_recu.get("type", "")
        donnees = evenement_recu.get("data") or {}
        if not identifiant or not type_evt or not isinstance(donnees, dict):
            return 400, {
                "erreur": {
                    "code": "webhook_incomplet",
                    "message": "Événement incomplet (id, type et data requis).",
                    "details": {},
                }
            }
        # Deux envois simultanés du même événement (fournisseur, réessais)
        # se sérialisent sur un verrou nommé MySQL : le second constate le
        # traitement du premier au lieu d'attendre un verrou de ligne
        # (timeout 1205) puis d'échouer en 500.
        # GET_LOCK est limité à 64 caractères : l'identifiant d'événement
        # complet peut être long (préfixe + référence + UUID côté tests ou
        # fournisseur) et dépasserait la borne, ce qui lève une erreur MySQL
        # 4163 non gérée. On dérive une clé courte et stable (SHA-256).
        from django.db import connection as _connexion

        nom_verrou = f"agrisens:wh:{hashlib.sha256(str(identifiant).encode()).hexdigest()[:44]}"
        with _connexion.cursor() as _curseur:
            _curseur.execute("SELECT GET_LOCK(%s, 15)", [nom_verrou])
            verrou = (_curseur.fetchone() or [0])[0]
        if verrou != 1:
            return 200, {"statut": "recu"}
        try:
            return ServicePaiement._traiter_sous_verrou(
                str(identifiant), str(type_evt), evenement_recu, donnees
            )
        finally:
            with _connexion.cursor() as _curseur:
                _curseur.execute("SELECT RELEASE_LOCK(%s)", [nom_verrou])

    @staticmethod
    def _traiter_sous_verrou(
        identifiant: str,
        type_evt: str,
        evenement_recu: dict[str, Any],
        donnees: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        for _tentative in range(2):
            try:
                with transaction.atomic():
                    evenement, cree = EvenementWebhook.objects.get_or_create(
                        identifiant_externe=str(identifiant),
                        defaults={
                            "fournisseur": "notchpay",
                            "type_evenement": str(type_evt),
                            "signature_valide": True,
                            "charge_utile": _nettoyer_charge(evenement_recu),
                        },
                    )
                    if not cree and evenement.traite_le is not None:
                        return 200, {"statut": "deja_traite"}
                    resultat = ServicePaiement._appliquer_evenement(evenement, donnees)
                    evenement.traite_le = maintenant()
                    evenement.save(update_fields=["traite_le", "maj_le"])
                    return 200, resultat
            except IntegrityError:
                continue  # un traitement concurrent a tranché ; on relit.
        return 200, {"statut": "recu"}

    @staticmethod
    def _appliquer_evenement(
        evenement: EvenementWebhook, donnees: dict[str, Any]
    ) -> dict[str, Any]:
        if evenement.type_evenement in TYPES_SUCCES:
            return ServicePaiement._appliquer_succes(evenement, donnees)
        if evenement.type_evenement in TYPES_ECHEC:
            return ServicePaiement._appliquer_echec(evenement, donnees, expire=False)
        if evenement.type_evenement in TYPES_EXPIRE:
            return ServicePaiement._appliquer_echec(evenement, donnees, expire=True)
        evenement.erreur = f"Type d'événement ignoré : {evenement.type_evenement}."
        evenement.save(update_fields=["erreur", "maj_le"])
        return {"statut": "ignore"}

    @staticmethod
    def _retrouver_paiement(
        evenement: EvenementWebhook, donnees: dict[str, Any]
    ) -> Paiement | None:
        reference_interne = donnees.get("merchant_reference") or donnees.get("trxref")
        if not reference_interne:
            evenement.erreur = "Référence interne absente (merchant_reference/trxref)."
            evenement.save(update_fields=["erreur", "maj_le"])
            return None
        paiement = (
            Paiement.objects.select_for_update().filter(reference_interne=reference_interne).first()
        )
        if paiement is None:
            evenement.erreur = f"Paiement introuvable : {reference_interne}."
            evenement.save(update_fields=["erreur", "maj_le"])
            return None
        montant = donnees.get("amount")
        if montant is not None:
            try:
                montant_recu = int(montant)
            except (TypeError, ValueError):
                montant_recu = None
            if montant_recu != paiement.montant_fcfa:
                evenement.erreur = (
                    f"Montant incohérent : reçu {montant}, attendu {paiement.montant_fcfa}."
                )
                evenement.save(update_fields=["erreur", "maj_le"])
                return None
        return paiement

    @staticmethod
    def _appliquer_succes(evenement: EvenementWebhook, donnees: dict[str, Any]) -> dict[str, Any]:
        paiement = ServicePaiement._retrouver_paiement(evenement, donnees)
        if paiement is None:
            return {"statut": "erreur"}
        reference_externe = donnees.get("reference")
        if reference_externe and not paiement.reference_notchpay:
            paiement.reference_notchpay = str(reference_externe)
        if paiement.statut == StatutPaiement.REUSSI:
            paiement.save()
            return {"statut": "deja_traite"}
        paiement.statut = StatutPaiement.REUSSI
        paiement.confirme_le = maintenant()
        paiement.save()
        # F-11 : paiement d'un accompagnement, `acceptee -> planifiee` via le
        # bus d'événements (`apps/accompagnements/services.py::sur_paiement_reussi`).
        if paiement.objet == ObjetPaiement.ACCOMPAGNEMENT and paiement.accompagnement_id:
            publier(
                PaiementReussi(
                    paiement_id=str(paiement.id),
                    commande_id="",
                    accompagnement_id=str(paiement.accompagnement_id),
                    montant_fcfa=paiement.montant_fcfa,
                )
            )
            return {"statut": "reussi", "paiement_id": str(paiement.id)}
        commande = Commande.objects.select_for_update().filter(id=paiement.commande_id).first()
        note = None
        if commande is not None and commande.statut in (
            StatutCommande.ACCEPTEE,
            StatutCommande.PAIEMENT_EN_ATTENTE,
        ):
            taux, commission = _commission_figee(commande)
            try:
                ServiceCommande.noter_paiement(str(commande.id), taux, commission)
            except TransitionInvalide:
                # Course entre deux webhooks : la commande a déjà avancé.
                note = "commande_deja_avancee"
            else:
                publier(
                    PaiementReussi(
                        paiement_id=str(paiement.id),
                        commande_id=str(commande.id),
                        montant_fcfa=paiement.montant_fcfa,
                    )
                )
        elif commande is not None and commande.statut in (
            StatutCommande.CLIENT_ABSENT,
            StatutCommande.ECHEC_LIVRAISON,
        ):
            # Paiement des nouveaux frais (Q-04) : re-propose la livraison.
            from apps.livraisons.services import ServiceLivraison

            if ServiceLivraison.finaliser_reprogrammation_payee(str(commande.id)) is None:
                note = "reprogrammation_deja_traitee"
            publier(
                PaiementReussi(
                    paiement_id=str(paiement.id),
                    commande_id=str(commande.id),
                    montant_fcfa=paiement.montant_fcfa,
                )
            )
        else:
            # Hypothèse Q-41 : argent réellement reçu, commande non payable —
            # le paiement est confirmé, la commande ne change pas d'état.
            note = "commande_non_payable"
        resultat: dict[str, Any] = {"statut": "reussi", "paiement_id": str(paiement.id)}
        if note:
            resultat["note"] = note
        return resultat

    @staticmethod
    def _appliquer_echec(
        evenement: EvenementWebhook, donnees: dict[str, Any], *, expire: bool
    ) -> dict[str, Any]:
        paiement = ServicePaiement._retrouver_paiement(evenement, donnees)
        if paiement is None:
            return {"statut": "erreur"}
        if paiement.statut == StatutPaiement.REUSSI:
            paiement.save()
            return {"statut": "deja_traite"}
        paiement.statut = StatutPaiement.EXPIRE if expire else StatutPaiement.ECHOUE
        paiement.save()
        commande = Commande.objects.filter(id=paiement.commande_id).first()
        if commande is not None and commande.statut == StatutCommande.PAIEMENT_EN_ATTENTE:
            ServiceCommande.revenir_acceptee(
                str(commande.id),
                "Délai de paiement dépassé."
                if expire
                else "Paiement échoué, nouvel essai possible.",
            )
        return {"statut": "expire" if expire else "echoue", "paiement_id": str(paiement.id)}

    # --- Vérification de secours -------------------------------------------

    @staticmethod
    def controler_en_attente(delai_minutes: int) -> int:
        """Vérifie les paiements en attente trop anciens via la passerelle."""
        seuil = maintenant() - timedelta(minutes=delai_minutes)
        selection = Paiement.objects.filter(
            statut=StatutPaiement.EN_ATTENTE, maj_le__lte=seuil
        ).exclude(reference_notchpay__isnull=True)
        passerelle = passerelle_paiement()
        traites = 0
        for paiement in selection.iterator():
            try:
                statut = passerelle.verifier_statut(str(paiement.reference_notchpay))
            except ErreurFournisseur:
                continue
            if statut == "reussi":
                with transaction.atomic():
                    frais = Paiement.objects.select_for_update().filter(id=paiement.id).first()
                    if frais is None or frais.statut != StatutPaiement.EN_ATTENTE:
                        continue
                    commande = Commande.objects.filter(id=frais.commande_id).first()
                    if commande is None or commande.statut not in (
                        StatutCommande.ACCEPTEE,
                        StatutCommande.PAIEMENT_EN_ATTENTE,
                    ):
                        continue
                    taux, commission = _commission_figee(commande)
                    ServiceCommande.noter_paiement(str(commande.id), taux, commission)
                    frais.statut = StatutPaiement.REUSSI
                    frais.confirme_le = maintenant()
                    frais.save()
                    publier(
                        PaiementReussi(
                            paiement_id=str(frais.id),
                            commande_id=str(commande.id),
                            montant_fcfa=frais.montant_fcfa,
                        )
                    )
                    traites += 1
            elif statut in ("echoue", "expire"):
                with transaction.atomic():
                    frais = Paiement.objects.select_for_update().filter(id=paiement.id).first()
                    if frais is None or frais.statut != StatutPaiement.EN_ATTENTE:
                        continue
                    frais.statut = (
                        StatutPaiement.EXPIRE if statut == "expire" else StatutPaiement.ECHOUE
                    )
                    frais.save()
                    commande = Commande.objects.filter(id=frais.commande_id).first()
                    if (
                        commande is not None
                        and commande.statut == StatutCommande.PAIEMENT_EN_ATTENTE
                    ):
                        ServiceCommande.revenir_acceptee(
                            str(commande.id), "Vérification du paiement."
                        )
                    traites += 1
        return traites


class ServiceSoldes:
    """Calcul des soldes bénéficiaires (Q-11) — jamais stocké, toujours recalculé.

    `solde = gains_acquis − remboursements_imputés − deja_reverse`.

    Gains acquis à la livraison (02 §2.4) :
      - agriculteur : sous-total − commission des commandes effectivement
        livrées (statut `livree`, `remboursement_en_attente` ou `remboursee` :
        la commande a été livrée puis possiblement remboursée) ;
      - livreur : frais de livraison des livraisons `livree`, sans commission
        (journal Q-09) ;
      - ingénieur : consultation − commission + déplacement des accompagnements
        `terminee` — l'accompagnement arrive en F-11, solde 0 en V1.

    # Hypothèse Q-43 : l'imputation d'un remboursement est portée sur le solde de
    # l'agriculteur vendeur de la commande remboursée (les frais de livraison déjà
    # gagnés par le livreur ne sont pas repris). À confirmer avec le client.
    """

    # Statuts de commande où l'agriculteur a effectivement livré (donc gagné).
    _STATUTS_LIVRES = (
        StatutCommande.LIVREE,
        StatutCommande.REMBOURSEMENT_EN_ATTENTE,
        StatutCommande.REMBOURSEE,
    )

    @staticmethod
    def _reverse_cumule(beneficiaire_id: str) -> int:
        from django.db.models import Sum

        agrégat = Reversement.objects.filter(beneficiaire_id=beneficiaire_id).aggregate(
            total=Sum("montant_fcfa")
        )
        return int(agrégat["total"] or 0)

    @classmethod
    def details(cls, utilisateur: Utilisateur) -> dict[str, int]:
        """Décompose le solde d'un bénéficiaire (agriculteur, livreur ou ingénieur).

        Suit la formule 02 §2.4 : `solde = gains_acquis − remboursements_imputés
        − deja_reverse`. Les gains sont bruts, les remboursements retranchés à part.
        """
        from django.db.models import Sum

        gains_bruts = 0
        remboursements = 0
        if utilisateur.role == RoleUtilisateur.AGRICULTEUR:
            agrégat = Commande.objects.filter(
                agriculteur_id=utilisateur.id, statut__in=cls._STATUTS_LIVRES
            ).aggregate(total=Sum("sous_total_fcfa") - Sum("commission_fcfa", default=0))
            gains_bruts = int(agrégat["total"] or 0)
            # Import local : litiges dépend de paiements.models, pas de services.
            from apps.litiges.models import Remboursement, StatutRemboursement

            agrégat_r = Remboursement.objects.filter(
                commande__agriculteur_id=utilisateur.id,
                statut=StatutRemboursement.EFFECTUE,
            ).aggregate(total=Sum("montant_accorde_fcfa"))
            remboursements = int(agrégat_r["total"] or 0)
        elif utilisateur.role == RoleUtilisateur.LIVREUR:
            from apps.livraisons.models import Livraison, StatutLivraison

            agrégat = Livraison.objects.filter(
                livreur_id=utilisateur.id, statut=StatutLivraison.LIVREE
            ).aggregate(total=Sum("commande__frais_livraison_fcfa"))
            gains_bruts = int(agrégat["total"] or 0)
        elif utilisateur.role == RoleUtilisateur.INGENIEUR_AGRONOME:
            # F-11 : gains ingénieur = consultation − commission + déplacement
            # des accompagnements `terminee` (02 §2.4, Q-06). Les RDV manqués
            # ou annulés ne rentrent pas ; un remboursement postérieur (Q-30)
            # est porté sur le solde de l'agriculteur qui a payé, pas sur
            # celui de l'ingénieur qui n'a rien gagné.
            from apps.accompagnements.models import Accompagnement, StatutAccompagnement

            agrégat = Accompagnement.objects.filter(
                ingenieur_id=utilisateur.id, statut=StatutAccompagnement.TERMINEE
            ).aggregate(
                consultation=Sum("prix_consultation_fcfa", default=0),
                deplacement=Sum("frais_deplacement_fcfa", default=0),
                commission=Sum("commission_consultation_fcfa", default=0),
            )
            gains_bruts = int(
                (agrégat["consultation"] or 0)
                + (agrégat["deplacement"] or 0)
                - (agrégat["commission"] or 0)
            )
        # Soldes des autres roles restent à zéro.

        deja_reverse = cls._reverse_cumule(utilisateur.id)
        return {
            "gains_acquis": gains_bruts,
            "remboursements_imputes": remboursements,
            "deja_reverse": deja_reverse,
            "solde": gains_bruts - remboursements - deja_reverse,
        }

    @classmethod
    def soldes_positifs(cls) -> list[dict[str, Any]]:
        """Bénéficiaires dont le solde est strictement positif (ADM file des reversements)."""
        roles = (
            RoleUtilisateur.AGRICULTEUR,
            RoleUtilisateur.LIVREUR,
            RoleUtilisateur.INGENIEUR_AGRONOME,
        )
        resultats: list[dict[str, Any]] = []
        for utilisateur in Utilisateur.objects.filter(role__in=roles).order_by("nom"):
            details = cls.details(utilisateur)
            if details["solde"] > 0:
                resultats.append(
                    {
                        "beneficiaire_id": utilisateur.id,
                        "nom": utilisateur.nom,
                        "role": utilisateur.role,
                        **details,
                    }
                )
        return resultats


class ServiceReversements:
    """Enregistrement des reversements manuels (Q-11) et historique."""

    ROLES_BENEFICIAIRES = (
        RoleUtilisateur.AGRICULTEUR,
        RoleUtilisateur.LIVREUR,
        RoleUtilisateur.INGENIEUR_AGRONOME,
    )

    @classmethod
    @transaction.atomic
    def enregistrer(
        cls,
        admin: Utilisateur,
        *,
        beneficiaire_id: str,
        montant_fcfa: int,
        operateur: str,
        reference_operateur: str,
        note: str | None = None,
        adresse_ip: str | None = None,
    ) -> Reversement:
        """POST /admin/reversements — montant ≤ solde, référence obligatoire, audit."""
        beneficiaire = (
            Utilisateur.objects.filter(id=beneficiaire_id)
            .exclude(statut_compte="anonymise")
            .first()
        )
        if beneficiaire is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Bénéficiaire introuvable.",
            )
        if beneficiaire.role not in cls.ROLES_BENEFICIAIRES:
            raise ValidationMetier(
                code="beneficiaire_invalide",
                message="Le reversement concerne un agriculteur, un livreur ou un ingénieur.",
                details={"champ": "beneficiaire_id"},
            )
        if montant_fcfa <= 0:
            raise ValidationMetier(
                code="montant_invalide",
                message="Le montant du reversement doit être strictement positif.",
                details={"champ": "montant_fcfa"},
            )
        if not (reference_operateur or "").strip():
            raise ValidationMetier(
                code="reference_requise",
                message="La référence du virement opérateur est obligatoire.",
                details={"champ": "reference_operateur"},
            )

        solde = ServiceSoldes.details(beneficiaire)["solde"]
        if montant_fcfa > solde:
            raise ValidationMetier(
                code="montant_superieur_solde",
                message=(
                    f"Le montant demandé ({montant_fcfa} FCFA) dépasse le solde "
                    f"disponible ({solde} FCFA)."
                ),
                details={"montant": montant_fcfa, "solde": solde},
            )

        reversement = Reversement.objects.create(
            beneficiaire=beneficiaire,
            montant_fcfa=montant_fcfa,
            operateur=operateur,
            reference_operateur=reference_operateur.strip(),
            effectue_par=admin,
            effectue_le=maintenant(),
            note=(note or "").strip() or None,
        )
        audit.journaliser(
            action="enregistrer_reversement",
            entite_type="reversement",
            entite_id=reversement.id,
            acteur=admin,
            nouvelle_valeur={
                "beneficiaire_id": beneficiaire.id,
                "montant_fcfa": montant_fcfa,
                "operateur": operateur,
                "reference_operateur": reversement.reference_operateur,
            },
            adresse_ip=adresse_ip,
        )
        return reversement

    @staticmethod
    def historique(beneficiaire_id: str | None = None):
        queryset = Reversement.objects.select_related("beneficiaire", "effectue_par")
        if beneficiaire_id:
            queryset = queryset.filter(beneficiaire_id=beneficiaire_id)
        return queryset.order_by("-effectue_le")


# Alias d'instance réutilisable (même style que parametres/audit).
soldes = ServiceSoldes()
reversements = ServiceReversements()

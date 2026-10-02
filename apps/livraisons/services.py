"""Logique métier des livraisons (SPEC-06, modèle 02 §2.3 et §3.2).

Création sur `PaiementReussi`, acceptation verrouillée, démarrage (stock
consommé), remise par code chiffré, incidents, reprogrammation (Q-04).
Le code de remise ne transite jamais en clair hors de ce module et n'est
jamais journalisé (Q-27, conventions §6).
"""

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64encode
from decimal import Decimal
from typing import Any

from commun.exceptions import Conflit, NonTrouve, TransitionInvalide, ValidationMetier
from commun.geo import distance_km as _distance_km
from commun.geo import valider_geojson
from commun.horloge import maintenant
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models, transaction

from apps.administration.models import HistoriqueStatut
from apps.administration.services import parametres
from apps.commandes.models import Commande, StatutCommande
from apps.commandes.services import ServiceCommande
from apps.fichiers.models import ContexteFichier, Fichier
from apps.livraisons.models import (
    IncidentLivraison,
    Itineraire,
    Livraison,
    SourceItineraire,
    StatutLivraison,
    TypeIncident,
)
from apps.notifications.services import notifier
from apps.produits.services import consommer as _consommer
from apps.utilisateurs.models import RoleUtilisateur

STATUTS_COURSE_LIVREUR = frozenset({StatutLivraison.ACCEPTEE, StatutLivraison.EN_COURS})

# Incidents qui changent l'état (les autres sont seulement enregistrés).
INCIDENTS_AVEC_EFFET = frozenset({TypeIncident.CLIENT_ABSENT, TypeIncident.ECHEC_LIVRAISON})


def _cle_chiffrement() -> bytes:
    """Clé Fernet dérivée du secret (accepte toute chaîne, environnements
    local/test/CI sans clé au format Fernet strict)."""
    return urlsafe_b64encode(hashlib.sha256(settings.CODE_REMISE_CLE.encode()).digest())


def generer_code_remise() -> str:
    """Code à 6 chiffres non devinable (`secrets`, Q-13)."""
    return f"{secrets.randbelow(900_000) + 100_000}"


def chiffrer_code(code: str) -> str:
    return Fernet(_cle_chiffrement()).encrypt(code.encode()).decode()


def dechiffrer_code(chiffre: str) -> str:
    try:
        return Fernet(_cle_chiffrement()).decrypt(chiffre.encode()).decode()
    except InvalidToken:
        raise ValidationMetier("code_illisible", "Code de remise illisible.") from None


def comparer_code(saisi: str, chiffre: str) -> bool:
    """Comparaison en temps constant (Q-27) sur le code déchiffré."""
    try:
        attendu = dechiffrer_code(chiffre)
    except ValidationMetier:
        return False
    return hmac.compare_digest(saisi.strip(), attendu)


def _historiser(
    livraison: Livraison, ancien: str | None, nouveau: str, acteur: Any, motif: str | None
) -> None:
    HistoriqueStatut.objects.create(
        entite_type="livraison",
        entite_id=livraison.id,
        ancien_statut=ancien,
        nouveau_statut=nouveau,
        acteur=acteur,
        motif=motif,
    )


def _transition(
    livraison: Livraison, nouveau: str, autorises: tuple[str, ...], acteur: Any, motif: str | None
) -> None:
    if livraison.statut not in autorises:
        raise TransitionInvalide(livraison.statut, nouveau, "livraison")
    ancien = livraison.statut
    livraison.statut = nouveau
    livraison.save(update_fields=["statut", "maj_le"])
    _historiser(livraison, ancien, nouveau, acteur, motif)


def _duree_minutes(distance_exacte_km: float) -> int:
    vitesse = float(parametres.obtenir("livraison.vitesse_moyenne_kmh"))
    return max(1, round(distance_exacte_km / vitesse * 60))


def creer_apres_paiement(evenement: Any) -> None:
    """Abonné `PaiementReussi` : livraison `proposee` + itinéraire + notifs.

    Exécuté dans la transaction du webhook (bus synchrone) : tout échec
    annule l'ensemble, le webhook sera rejoué par le fournisseur.
    """

    commande = Commande.objects.filter(id=evenement.commande_id).first()
    if commande is None or commande.statut != StatutCommande.PAYEE:
        return
    if Livraison.objects.filter(commande=commande).exists():
        return
    livraison = Livraison.objects.create(
        commande=commande,
        statut=StatutLivraison.PROPOSEE,
        numero_tentative=1,
        code_remise_chiffre=chiffrer_code(generer_code_remise()),
    )
    _historiser(livraison, None, StatutLivraison.PROPOSEE, None, "Paiement confirmé.")
    depart = commande.position_recuperation
    arrivee = commande.position_livraison
    depart_geo = depart.__geo_interface__ if hasattr(depart, "__geo_interface__") else depart
    arrivee_geo = arrivee.__geo_interface__ if hasattr(arrivee, "__geo_interface__") else arrivee
    distance_exacte = _distance_km(depart_geo, arrivee_geo)
    Itineraire.objects.create(
        livraison=livraison,
        point_depart=depart_geo,
        point_arrivee=arrivee_geo,
        trajet=None,
        distance_km=round(Decimal(str(distance_exacte)), 2),
        duree_estimee_min=_duree_minutes(distance_exacte),
        source=SourceItineraire.LIGNE_DROITE,
    )
    from apps.profils.models import LivreurProfil

    for profil in LivreurProfil.objects.filter(disponible=True).select_related("utilisateur"):
        notifier(
            profil.utilisateur,
            "livraison",
            f"Nouvelle livraison {commande.reference}",
            f"{commande.frais_livraison_fcfa} FCFA — {commande.zone_livraison}.",
            lien="/livreur/courses",
        )


class ServiceLivraison:
    """Disponibles, acceptation, course, code, incidents, reprogrammation."""

    # --- Listes et détail ---------------------------------------------------

    @staticmethod
    def _profil_livreur(utilisateur: Any):
        from apps.profils.models import LivreurProfil

        return LivreurProfil.objects.filter(utilisateur=utilisateur).first()

    @staticmethod
    def _exiger_profil_livreur(utilisateur: Any):
        profil = ServiceLivraison._profil_livreur(utilisateur)
        if profil is None:
            raise NonTrouve()
        return profil

    @staticmethod
    def disponibles(livreur: Any) -> list[Livraison]:
        """Livraisons `proposee` triées par distance au livreur.

        Livreur indisponible (ou sans profil) → liste vide (PW-F08-07),
        jamais 403. Sans adresse exacte ni téléphone (D-21).
        """
        profil = ServiceLivraison._profil_livreur(livreur)
        if profil is None or not profil.disponible or profil.position is None:
            return []
        position = profil.position
        position_geo = (
            position.__geo_interface__ if hasattr(position, "__geo_interface__") else position
        )
        livraisons = list(
            Livraison.objects.filter(statut=StatutLivraison.PROPOSEE).select_related(
                "commande", "itineraire"
            )
        )

        def _distance(livraison: Livraison) -> float:
            depart = livraison.commande.position_recuperation
            depart_geo = (
                depart.__geo_interface__ if hasattr(depart, "__geo_interface__") else depart
            )
            return _distance_km(position_geo, depart_geo)

        return sorted(livraisons, key=_distance)

    @staticmethod
    def mes_livraisons(livreur: Any) -> list[Livraison]:
        return list(
            Livraison.objects.filter(livreur=livreur)
            .exclude(statut=StatutLivraison.PROPOSEE)
            .select_related("commande", "itineraire")
            .order_by("-maj_le")
        )

    @staticmethod
    def obtenir_visible(utilisateur: Any, identifiant: str) -> Livraison:
        livraison = (
            Livraison.objects.filter(id=identifiant)
            .select_related("commande", "commande__acheteur", "commande__agriculteur", "livreur")
            .first()
        )
        if livraison is None:
            raise NonTrouve()
        role = getattr(utilisateur, "role", None)
        if role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            return livraison
        if livraison.commande.acheteur_id == utilisateur.id:
            return livraison
        if livraison.commande.agriculteur_id == utilisateur.id:
            return livraison
        if livraison.livreur_id is not None and livraison.livreur_id == utilisateur.id:
            return livraison
        raise NonTrouve()

    @staticmethod
    def obtenir_par_commande(utilisateur: Any, commande_id: str) -> Livraison:
        """Livraison liée à une commande, si l'utilisateur voit la commande."""
        ServiceCommande.obtenir_visible(utilisateur, commande_id)
        livraison = Livraison.objects.filter(commande_id=commande_id).first()
        if livraison is None:
            raise NonTrouve(
                code="livraison_introuvable",
                message="Aucune livraison pour cette commande.",
            )
        return ServiceLivraison.obtenir_visible(utilisateur, str(livraison.id))

    @staticmethod
    def donnees_sensibles(livraison: Livraison, utilisateur: Any) -> dict[str, Any]:
        """Adresse exacte, téléphone acheteur, position et téléphone livreur
        selon le rôle et l'étape (D-20, D-21, PW-F08-01/06)."""
        commande = livraison.commande
        role = getattr(utilisateur, "role", None)
        est_admin = role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN)
        est_acheteur = commande.acheteur_id == getattr(utilisateur, "id", None)
        est_livreur_affecte = livraison.livreur_id is not None and livraison.livreur_id == getattr(
            utilisateur, "id", None
        )
        en_cours = livraison.statut == StatutLivraison.EN_COURS
        livreur = livraison.livreur
        position_livreur = None
        if livreur is not None and (en_cours and (est_acheteur or est_admin)):
            from apps.profils.models import LivreurProfil

            profil = LivreurProfil.objects.filter(utilisateur=livreur).first()
            brute = getattr(profil, "position", None) if profil else None
            if brute is not None:
                position_livreur = (
                    brute.__geo_interface__ if hasattr(brute, "__geo_interface__") else brute
                )
        return {
            # Adresse exacte + téléphone acheteur : livreur affecté, admin, acheteur.
            "adresse_exacte": (
                commande.adresse_livraison_texte
                if (est_livreur_affecte or est_admin or est_acheteur)
                else commande.zone_livraison
            ),
            "adresse_exacte_visible": bool(est_livreur_affecte or est_admin or est_acheteur),
            "telephone_acheteur": (
                commande.acheteur.telephone if (est_livreur_affecte or est_admin) else None
            ),
            # Position + téléphone livreur : acheteur seulement pendant en_cours.
            "position_livreur": position_livreur
            if (en_cours and (est_acheteur or est_admin))
            else None,
            "telephone_livreur": (
                livreur.telephone
                if (en_cours and (est_acheteur or est_admin) and livreur is not None)
                else None
            ),
            "livreur_nom": livreur.nom if livreur is not None else None,
        }

    @staticmethod
    def code_pour_acheteur(acheteur: Any, commande_id: str) -> str:
        """Code de remise en clair, acheteur de la commande uniquement (404 sinon)."""
        livraison = Livraison.objects.filter(commande_id=commande_id).first()
        if livraison is None or livraison.commande.acheteur_id != getattr(acheteur, "id", None):
            raise NonTrouve(
                code="ressource_introuvable",
                message="Code indisponible.",
            )
        return dechiffrer_code(livraison.code_remise_chiffre)

    # --- Acceptation et course ----------------------------------------------

    @staticmethod
    @transaction.atomic
    def accepter(livreur: Any, identifiant: str) -> Livraison:
        profil = ServiceLivraison._exiger_profil_livreur(livreur)
        if not profil.disponible:
            raise ValidationMetier(
                "livreur_indisponible",
                "Passez disponible pour accepter une livraison.",
            )
        livraison = Livraison.objects.select_for_update().filter(id=identifiant).first()
        if livraison is None:
            raise NonTrouve()
        if livraison.statut != StatutLivraison.PROPOSEE:
            # Un seul livreur gagne la course (409), les autres voient « déjà prise ».
            raise Conflit(
                code="livraison_deja_prise",
                message="Cette livraison vient d'être acceptée par un autre livreur.",
            )
        livraison.livreur = livreur
        livraison.acceptee_le = maintenant()
        livraison.save(update_fields=["livreur", "acceptee_le", "maj_le"])
        _transition(
            livraison,
            StatutLivraison.ACCEPTEE,
            (StatutLivraison.PROPOSEE,),
            livreur,
            None,
        )
        commande = livraison.commande
        notifier(
            commande.acheteur,
            "livraison",
            f"Livraison {commande.reference} prise en charge",
            f"{livreur.nom} récupère vos produits.",
            lien=f"/commandes/{commande.id}",
        )
        return livraison

    @staticmethod
    @transaction.atomic
    def se_desister(livreur: Any, identifiant: str) -> Livraison:
        livraison = Livraison.objects.filter(id=identifiant, livreur=livreur).first()
        if livraison is None:
            raise NonTrouve()
        _transition(
            livraison,
            StatutLivraison.PROPOSEE,
            (StatutLivraison.ACCEPTEE,),
            livreur,
            "Désistement.",
        )
        livraison.livreur = None
        livraison.save(update_fields=["livreur", "maj_le"])
        return livraison

    @staticmethod
    @transaction.atomic
    def demarrer(livreur: Any, identifiant: str) -> Livraison:
        """Produit récupéré : livraison `en_cours`, commande `remise_au_livreur`,
        stock physique décrémenté (02 §3.1). Exige la préparation (diagrammes §3)."""
        livraison = (
            Livraison.objects.select_for_update().filter(id=identifiant, livreur=livreur).first()
        )
        if livraison is None:
            raise NonTrouve()
        commande = Commande.objects.select_for_update().filter(id=livraison.commande_id).first()
        if commande is None or commande.statut != StatutCommande.PREPARATION:
            raise TransitionInvalide(
                commande.statut if commande else "?",
                StatutCommande.REMISE_AU_LIVREUR,
                "commande",
            )
        _transition(livraison, StatutLivraison.EN_COURS, (StatutLivraison.ACCEPTEE,), livreur, None)
        livraison.demarree_le = maintenant()
        livraison.save(update_fields=["demarree_le", "maj_le"])
        for ligne in commande.lignes.all():
            _consommer(str(ligne.produit_id), ligne.quantite)
        ancien = commande.statut
        commande.statut = StatutCommande.REMISE_AU_LIVREUR
        commande.save(update_fields=["statut", "maj_le"])
        HistoriqueStatut.objects.create(
            entite_type="commande",
            entite_id=commande.id,
            ancien_statut=ancien,
            nouveau_statut=StatutCommande.REMISE_AU_LIVREUR,
            acteur=livreur,
            motif="Produit récupéré par le livreur.",
        )
        notifier(
            commande.acheteur,
            "livraison",
            f"Livraison {commande.reference} en cours",
            "Le livreur a récupéré vos produits.",
            lien=f"/commandes/{commande.id}",
        )
        return livraison

    @staticmethod
    def confirmer_remise(livreur: Any, identifiant: str, code: str) -> Livraison:
        """Valide le code : commande et livraison `livree`. Sinon 422, et
        blocage après `livraison.tentatives_code_max` échecs (Q-13).

        L'échec est compté en autocommit (hors transaction) : le `raise`
        qui suit ne doit pas annuler le compteur.
        """
        from apps.administration.services import parametres as _parametres

        livraison = Livraison.objects.filter(id=identifiant, livreur=livreur).first()
        if livraison is None:
            raise NonTrouve()
        if livraison.statut != StatutLivraison.EN_COURS:
            raise TransitionInvalide(livraison.statut, StatutLivraison.LIVREE, "livraison")
        maximum = int(_parametres.obtenir("livraison.tentatives_code_max"))
        if livraison.tentatives_code_echouees >= maximum:
            raise ValidationMetier(
                "code_bloque",
                "Saisie du code bloquée après trop d'échecs : contactez un administrateur.",
            )
        if not comparer_code(code or "", livraison.code_remise_chiffre):
            Livraison.objects.filter(id=livraison.id).update(
                tentatives_code_echouees=models.F("tentatives_code_echouees") + 1
            )
            restants = maximum - int(livraison.tentatives_code_echouees) - 1
            raise ValidationMetier(
                "code_incorrect",
                "Code de remise incorrect.",
                {"essais_restants": max(0, restants)},
            )
        with transaction.atomic():
            fraiche = Livraison.objects.select_for_update().filter(id=livraison.id).first()
            if fraiche is None or fraiche.statut != StatutLivraison.EN_COURS:
                raise TransitionInvalide(
                    fraiche.statut if fraiche else "?",
                    StatutLivraison.LIVREE,
                    "livraison",
                )
            if fraiche.tentatives_code_echouees >= maximum:
                raise ValidationMetier(
                    "code_bloque",
                    "Saisie du code bloquée après trop d'échecs : contactez un administrateur.",
                )
            _transition(fraiche, StatutLivraison.LIVREE, (StatutLivraison.EN_COURS,), livreur, None)
            fraiche.livree_le = maintenant()
            fraiche.save(update_fields=["livree_le", "maj_le"])
            commande = Commande.objects.select_for_update().filter(id=fraiche.commande_id).first()
            if commande is not None and commande.statut == StatutCommande.REMISE_AU_LIVREUR:
                ancien = commande.statut
                commande.statut = StatutCommande.LIVREE
                commande.save(update_fields=["statut", "maj_le"])
                HistoriqueStatut.objects.create(
                    entite_type="commande",
                    entite_id=commande.id,
                    ancien_statut=ancien,
                    nouveau_statut=StatutCommande.LIVREE,
                    acteur=livreur,
                    motif="Code de remise validé.",
                )
                notifier(
                    commande.acheteur,
                    "commande",
                    f"Commande {commande.reference} livrée",
                    "Merci de votre achat ! Vous pouvez évaluer.",
                    lien=f"/commandes/{commande.id}",
                )
                notifier(
                    commande.agriculteur,
                    "commande",
                    f"Commande {commande.reference} livrée",
                    "Livraison confirmée par le code.",
                    lien=f"/agriculteur/commandes/{commande.id}",
                )
            return fraiche

    # --- Position du livreur (course en cours) --------------------------------

    @staticmethod
    @transaction.atomic
    def mettre_a_jour_position(livreur: Any, identifiant: str, position: dict[str, Any]) -> Any:
        """Le livreur publie sa position pendant `en_cours` : elle devient la
        dernière position visible par l'acheteur et l'admin (D-20, prompt point 5)."""
        profil = ServiceLivraison._profil_livreur(livreur)
        livraison = Livraison.objects.filter(id=identifiant, livreur=livreur).first()
        if livraison is None:
            raise NonTrouve()
        if livraison.statut != StatutLivraison.EN_COURS:
            raise TransitionInvalide(livraison.statut, StatutLivraison.EN_COURS, "livraison")
        valider_geojson(position)
        profil.position = position
        profil.position_maj_le = maintenant()
        profil.save(update_fields=["position", "position_maj_le", "maj_le"])
        return profil

    # --- Incidents ------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def declarer_incident(
        livreur: Any,
        identifiant: str,
        type_incident: str,
        description: str = "",
        fichier_id: str | None = None,
    ) -> IncidentLivraison:
        if type_incident not in TypeIncident.values:
            raise ValidationMetier(
                "type_incident_inconnu",
                "Type d'incident inconnu.",
                {"type": type_incident},
            )
        livraison = (
            Livraison.objects.select_for_update().filter(id=identifiant, livreur=livreur).first()
        )
        if livraison is None:
            raise NonTrouve()
        if livraison.statut != StatutLivraison.EN_COURS:
            raise TransitionInvalide(livraison.statut, type_incident, "livraison")
        fichier = None
        if fichier_id:
            fichier = Fichier.objects.filter(
                id=fichier_id, proprietaire_id=livreur.id, contexte=ContexteFichier.INCIDENT
            ).first()
            if fichier is None:
                raise ValidationMetier(
                    "photo_invalide",
                    "La photo d'incident doit être une image téléversée en contexte « incident ».",
                )
        incident = IncidentLivraison.objects.create(
            livraison=livraison,
            declare_par=livreur,
            type=type_incident,
            description=(description or "").strip(),
            fichier=fichier,
        )
        if type_incident in INCIDENTS_AVEC_EFFET:
            nouveau = (
                StatutLivraison.CLIENT_ABSENT
                if type_incident == TypeIncident.CLIENT_ABSENT
                else StatutLivraison.ECHEC_LIVRAISON
            )
            _transition(
                livraison,
                nouveau,
                (StatutLivraison.EN_COURS,),
                livreur,
                incident.description or None,
            )
            commande = Commande.objects.select_for_update().filter(id=livraison.commande_id).first()
            if commande is not None and commande.statut == StatutCommande.REMISE_AU_LIVREUR:
                ancien = commande.statut
                commande.statut = (
                    StatutCommande.CLIENT_ABSENT
                    if type_incident == TypeIncident.CLIENT_ABSENT
                    else StatutCommande.ECHEC_LIVRAISON
                )
                commande.save(update_fields=["statut", "maj_le"])
                HistoriqueStatut.objects.create(
                    entite_type="commande",
                    entite_id=commande.id,
                    ancien_statut=ancien,
                    nouveau_statut=commande.statut,
                    acteur=livreur,
                    motif=incident.description or None,
                )
            if commande is not None:
                notifier(
                    commande.acheteur,
                    "livraison",
                    f"Incident sur la livraison {commande.reference}",
                    incident.description or type_incident,
                    lien=f"/commandes/{commande.id}",
                )
        return incident

    # --- Reprogrammation (Q-04) ----------------------------------------------

    @staticmethod
    @transaction.atomic
    def reprogrammer(
        acteur: Any,
        identifiant: str,
        adresse_id: str | None = None,
        operateur: str | None = None,
        telephone: str | None = None,
        cle_idempotence: str | None = None,
        url_base_pwa: str = "",
    ) -> dict[str, Any]:
        """Repropose la livraison après incident.

        `echec_livraison` : gratuit. `client_absent` : nouveaux frais payés
        par l'acheteur (nouveau paiement F-07 lié à la commande) AVANT
        re-proposition. Au-delà de `livraison.reprogrammations_max` :
        remboursement proposé (409). Nouvelle adresse possible.
        """
        from apps.paiements.services import ServicePaiement
        from apps.utilisateurs.models import Adresse

        livraison = Livraison.objects.select_for_update().filter(id=identifiant).first()
        if livraison is None:
            raise NonTrouve()
        commande = Commande.objects.select_for_update().filter(id=livraison.commande_id).first()
        if commande is None:
            raise NonTrouve()
        role = getattr(acteur, "role", None)
        if not (
            commande.acheteur_id == getattr(acteur, "id", None)
            or role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN)
        ):
            raise NonTrouve()
        if livraison.statut not in (
            StatutLivraison.CLIENT_ABSENT,
            StatutLivraison.ECHEC_LIVRAISON,
        ):
            raise TransitionInvalide(livraison.statut, StatutLivraison.PROPOSEE, "livraison")
        maximum = int(parametres.obtenir("livraison.reprogrammations_max"))
        if livraison.numero_tentative - 1 >= maximum:
            raise Conflit(
                code="reprogrammations_epuisees",
                message=(
                    "Nombre maximal de reprogrammations atteint : un remboursement est proposé."
                ),
            )
        if adresse_id:
            adresse = Adresse.objects.filter(
                id=adresse_id, utilisateur_id=commande.acheteur_id
            ).first()
            if adresse is None:
                raise NonTrouve()
            position = adresse.position
            commande.adresse_livraison_texte = (
                f"{adresse.libelle} — {adresse.description}, {adresse.ville} ({adresse.region})"
            )
            commande.zone_livraison = adresse.ville or adresse.region or commande.zone_livraison
            commande.position_livraison = (
                position.__geo_interface__ if hasattr(position, "__geo_interface__") else position
            )
            commande.save()

        if livraison.statut == StatutLivraison.ECHEC_LIVRAISON:
            ServiceLivraison._reproposer(livraison, commande, acteur, sans_frais=True)
            return {"sans_frais": True, "livraison_id": str(livraison.id)}

        # Client absent : nouveaux frais à payer d'abord (Q-04).
        from commun.geo import distance_km as _distance

        from apps.commandes.tarification import frais_livraison as _frais

        depart = commande.position_recuperation
        arrivee = commande.position_livraison
        depart_geo = depart.__geo_interface__ if hasattr(depart, "__geo_interface__") else depart
        arrivee_geo = (
            arrivee.__geo_interface__ if hasattr(arrivee, "__geo_interface__") else arrivee
        )
        distance_exacte = _distance(depart_geo, arrivee_geo)
        _, frais = _frais(
            distance_exacte,
            base_fcfa=int(parametres.obtenir("livraison.base_fcfa")),
            tarif_km_fcfa=int(parametres.obtenir("livraison.tarif_km_fcfa")),
            minimum_fcfa=int(parametres.obtenir("livraison.minimum_fcfa")),
            plafond_fcfa=int(parametres.obtenir("livraison.plafond_fcfa")),
        )
        paiement, url, rejoue = ServicePaiement.initier_reprogrammation(
            commande, frais, operateur, telephone, cle_idempotence, url_base_pwa
        )
        return {
            "sans_frais": False,
            "paiement_id": str(paiement.id),
            "url_paiement": url,
            "montant_fcfa": frais,
            "rejoue": rejoue,
        }

    @staticmethod
    def _reproposer(
        livraison: Livraison, commande: Commande, acteur: Any, *, sans_frais: bool
    ) -> None:
        livraison.numero_tentative += 1
        livraison.livreur = None
        livraison.tentatives_code_echouees = 0
        livraison.save(
            update_fields=["numero_tentative", "livreur", "tentatives_code_echouees", "maj_le"]
        )
        _transition(
            livraison,
            StatutLivraison.PROPOSEE,
            (StatutLivraison.CLIENT_ABSENT, StatutLivraison.ECHEC_LIVRAISON),
            acteur,
            "Reprogrammation" + (" sans frais." if sans_frais else "."),
        )
        if commande.statut in (StatutCommande.CLIENT_ABSENT, StatutCommande.ECHEC_LIVRAISON):
            ancien = commande.statut
            commande.statut = StatutCommande.REMISE_AU_LIVREUR
            commande.save(update_fields=["statut", "maj_le"])
            HistoriqueStatut.objects.create(
                entite_type="commande",
                entite_id=commande.id,
                ancien_statut=ancien,
                nouveau_statut=StatutCommande.REMISE_AU_LIVREUR,
                acteur=acteur if getattr(acteur, "id", None) else None,
                motif="Livraison reprogrammée.",
            )
        notifier(
            commande.acheteur,
            "livraison",
            f"Livraison {commande.reference} reprogrammée",
            "Un livreur va de nouveau se proposer.",
            lien=f"/commandes/{commande.id}",
        )
        notifier(
            commande.agriculteur,
            "livraison",
            f"Livraison {commande.reference} reprogrammée",
            "",
            lien=f"/agriculteur/commandes/{commande.id}",
        )

    @staticmethod
    def finaliser_reprogrammation_payee(commande_id: str) -> Livraison | None:
        """Webhook du paiement des nouveaux frais : re-propose la livraison.

        Appelée par `apps/paiements/services.py` (import différé, pas de cycle).
        Renvoie None si plus rien à reprogrammer (déjà traitée).
        """
        livraison = (
            Livraison.objects.filter(
                commande_id=commande_id,
                statut__in=(StatutLivraison.CLIENT_ABSENT, StatutLivraison.ECHEC_LIVRAISON),
            )
            .select_related("commande")
            .first()
        )
        if livraison is None:
            return None
        with transaction.atomic():
            livraison = Livraison.objects.select_for_update().filter(id=livraison.id).first()
            if livraison is None or livraison.statut not in (
                StatutLivraison.CLIENT_ABSENT,
                StatutLivraison.ECHEC_LIVRAISON,
            ):
                return None
            commande = Commande.objects.filter(id=livraison.commande_id).first()
            if commande is None:
                return None
            ServiceLivraison._reproposer(livraison, commande, None, sans_frais=False)
            return livraison

    @staticmethod
    def historique_visible(utilisateur: Any, identifiant: str) -> list[HistoriqueStatut]:
        livraison = ServiceLivraison.obtenir_visible(utilisateur, identifiant)
        return list(
            HistoriqueStatut.objects.filter(
                entite_type="livraison", entite_id=livraison.id
            ).order_by("cree_le")
        )

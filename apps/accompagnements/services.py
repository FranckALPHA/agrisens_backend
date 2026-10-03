"""Logique métier de l'accompagnement agronomique (SPEC-07, modèle 02 §2.5 et §3.3).

Machine à états (§3.3) : demandee → acceptee|refusee|annulee ; acceptee →
planifiee (webhook paiement réussi, journal Q-05) | demandee (changement
d'ingénieur, Q-29) ; planifiee → terminee|manquee|demandee (changement admin
après paiement, Q-29) ; manquee → planifiee (reprogrammation) | annulee.

Règles appliquées ici :
- devis = consultation (profil ingénieur) + déplacement (P-06, 07 §15.6) ;
  exemple de référence : 12 km aller → 24 km × 200 = 4 800 FCFA ;
- distance maximale (`deplacement.distance_max_km`) dépassée → présentiel
  refusé (`distance_max_depasee`), proposition « en ligne » ;
- commission uniquement sur la consultation (`commission.consultation_taux_pb`,
  Q-06), jamais sur le déplacement ;
- conflit de créneau : verrou `SELECT FOR UPDATE` sur les lignes du même
  ingénieur dont l'intervalle [debut, fin] chevauche le nouveau ; statut en
  (`planifiee`, `manquee`) → 409 (`conflit_creneau`) ;
- montant figé à la planification (07 §15.6 : l'ingénieur ne modifie pas
  après confirmation, la spec §2.5 dit « figé à la confirmation » — ici
  c'est à la planification que le prix est calculé et figé, avant paiement) ;
- paiement à la confirmation par l'agriculteur, `planifiee` seulement après
  webhook NotchPay réussi (Q-05) ;
- changement d'ingénieur (Q-29) : avant paiement par l'agriculteur ; après
  paiement par l'admin uniquement (le montant payé est conservé, pas de
  supplément en V1) ;
- RDV manqué (Q-30) : ingénieur absent → remboursement intégral créé et
  approuvé automatiquement ; agriculteur absent → pas de remboursement,
  une reprogrammation gratuite puis nouvelle demande ;
- clôture : observation obligatoire (02 §2.5, 07 §15.6) ; gain soldes =
  consultation − commission + déplacement ;
- accès en lecture exploitation (F-04 registre) : ingénieur affecté uniquement
  pendant accompagnement actif (statuts `acceptee`, `planifiee`, `manquee`).

Aucun tarif en dur : tout passe par `apps.administration.services.parametres`.
"""

from datetime import datetime
from typing import Any

from commun.evenements import Evenement
from commun.exceptions import (
    Conflit,
    NonTrouve,
    TransitionInvalide,
    ValidationMetier,
)
from commun.geo import distance_km, valider_geojson
from commun.horloge import maintenant
from django.db import transaction
from shapely.geometry.base import BaseGeometry

from apps.accompagnements.models import (
    AbsentRDV,
    Accompagnement,
    AffectationIngenieur,
    Modalite,
    MotifFinAffectation,
    StatutAccompagnement,
)
from apps.accompagnements.tarification import (
    commission_consultation,
    frais_deplacement,
)
from apps.administration.models import HistoriqueStatut
from apps.administration.services import parametres
from apps.exploitations.models import AccesExploitation, Exploitation
from apps.notifications.services import notifier
from apps.paiements.models import ObjetPaiement, Paiement
from apps.paiements.passerelle import passerelle_paiement
from apps.profils.models import IngenieurProfil, StatutValidation
from apps.utilisateurs.models import RoleUtilisateur, Utilisateur

# --- Machine à états (§3.3) --------------------------------------------------

TRANSITIONS: dict[str, frozenset[str]] = {
    StatutAccompagnement.DEMANDEE: frozenset(
        {
            StatutAccompagnement.ACCEPTEE,
            StatutAccompagnement.REFUSEE,
            StatutAccompagnement.ANNULEE,
        }
    ),
    StatutAccompagnement.ACCEPTEE: frozenset(
        {
            StatutAccompagnement.PLANIFIEE,
            StatutAccompagnement.DEMANDEE,
            StatutAccompagnement.ANNULEE,
        }
    ),
    StatutAccompagnement.PLANIFIEE: frozenset(
        {
            StatutAccompagnement.TERMINEE,
            StatutAccompagnement.MANQUEE,
            StatutAccompagnement.DEMANDEE,
            StatutAccompagnement.ANNULEE,
        }
    ),
    StatutAccompagnement.MANQUEE: frozenset(
        {
            StatutAccompagnement.PLANIFIEE,
            StatutAccompagnement.ANNULEE,
        }
    ),
    StatutAccompagnement.REFUSEE: frozenset(),
    StatutAccompagnement.TERMINEE: frozenset(),
    StatutAccompagnement.ANNULEE: frozenset(),
}

# Statuts pendant lesquels l'ingénieur affecté peut lire l'exploitation (F-11).
STATUTS_ACCES_EXPLOITATION = frozenset(
    {
        StatutAccompagnement.ACCEPTEE,
        StatutAccompagnement.PLANIFIEE,
        StatutAccompagnement.MANQUEE,
    }
)

# Clés paramètre lues une seule fois au devis et à la planification.
CLES_TARIFS = (
    "deplacement.tarif_km_fcfa",
    "deplacement.minimum_fcfa",
    "deplacement.plafond_fcfa",
    "deplacement.distance_max_km",
    "commission.consultation_taux_pb",
)


class EvenementAccompagnementPlanifie(Evenement):
    """Signal interne (publié par `sur_paiement_reussi` après `planifiee`)."""

    accompagnement_id: str = ""


class EvenementAccompagnementTermine(Evenement):
    accompagnement_id: str = ""
    gain_ingénieur_fcfa: int = 0


def _lire_tarifs() -> dict[str, int]:
    return {cle: int(parametres.obtenir(cle)) for cle in CLES_TARIFS}


def _historiser(
    accompagnement: Accompagnement,
    ancien: str | None,
    nouveau: str,
    acteur: Any,
    motif: str | None,
) -> None:
    HistoriqueStatut.objects.create(
        entite_type="accompagnement",
        entite_id=accompagnement.id,
        ancien_statut=ancien,
        nouveau_statut=nouveau,
        acteur=acteur,
        motif=motif,
    )


def _transition(
    accompagnement: Accompagnement,
    nouveau: str,
    acteur: Any,
    motif: str | None,
) -> None:
    autorises = TRANSITIONS.get(accompagnement.statut, frozenset())
    if nouveau not in autorises:
        raise TransitionInvalide(accompagnement.statut, nouveau, "accompagnement")
    ancien = accompagnement.statut
    accompagnement.statut = nouveau
    accompagnement.save(update_fields=["statut", "maj_le"])
    _historiser(accompagnement, ancien, nouveau, acteur, motif)


def _geojson_de(geom: Any) -> dict[str, Any] | None:
    """Convertit une géométrie Shapely lue de MySQL (x=lon, y=lat) en GeoJSON dict."""
    if geom is None:
        return None
    if isinstance(geom, dict):
        return geom
    if isinstance(geom, BaseGeometry):
        return geom.__geo_interface__
    return None


def _verifier_ingénieur_valide_disponible(ingenieur_id: str) -> IngenieurProfil:
    profil = (
        IngenieurProfil.objects.select_related("utilisateur").filter(
            utilisateur_id=ingenieur_id,
            statut_validation=StatutValidation.VALIDE,
            disponible=True,
        )
    ).first()
    if profil is None:
        raise ValidationMetier(
            code="ingenieur_indisponible",
            message="Ingénieur introuvable, non validé ou indisponible.",
            details={"champ": "ingenieur_id"},
        )
    return profil


def _verifier_zone_couvre_exploitation(
    profil: IngenieurProfil,
    exploitation: Exploitation,
) -> tuple[bool, float | None]:
    """Vérifie que la zone de l'ingénieur couvre le centre de l'exploitation.

    Retourne `(dans_zone, distance_km_exacte)`. Si `zone_centre` ou
    `zone_rayon_km` absent : `(True, None)` — l'ingénieur n'a pas configuré
    de zone, on ne peut pas refuser sur ce critère ; le déplacement sera
    refusé sur la distance_max si présentiel.
    """
    zone = _geojson_de(profil.zone_centre)
    centre = _geojson_de(exploitation.centre)
    if zone is None or centre is None or profil.zone_rayon_km is None:
        return True, None
    valider_geojson(zone)
    valider_geojson(centre)
    d = distance_km(zone, centre)
    return d <= float(profil.zone_rayon_km), d


def _calculer_devis(
    profil: IngenieurProfil,
    exploitation: Exploitation,
    modalite: str,
) -> dict[str, Any]:
    """Consultation + déplacement (P-06, 07 §15.6), sans rien écrire."""
    if profil.tarif_consultation_fcfa is None:
        raise ValidationMetier(
            code="tarif_ingenieur_manquant",
            message="L'ingénieur n'a pas encore fixé son tarif de consultation.",
        )
    consultation = int(profil.tarif_consultation_fcfa)
    tarifs = _lire_tarifs()
    distance_exacte: float | None = None
    km_aller: int | None = None
    deplacement = 0
    if modalite == Modalite.PRESENTIEL:
        zone = _geojson_de(profil.zone_centre)
        centre = _geojson_de(exploitation.centre)
        if zone is None or centre is None:
            raise ValidationMetier(
                code="zone_ou_centre_manquant",
                message=(
                    "Zone de l'ingénieur ou centre de l'exploitation manquant : "
                    "impossible de calculer le déplacement."
                ),
            )
        valider_geojson(zone)
        valider_geojson(centre)
        distance_exacte = distance_km(zone, centre)
        if distance_exacte > float(tarifs["deplacement.distance_max_km"]):
            raise ValidationMetier(
                code="distance_max_depasee",
                message=(
                    f"La distance ({round(distance_exacte, 1)} km) dépasse la "
                    f"distance maximale autorisée en présentiel "
                    f"({tarifs['deplacement.distance_max_km']} km). "
                    "Proposez un accompagnement en ligne."
                ),
                details={
                    "distance_km": round(distance_exacte, 2),
                    "distance_max_km": tarifs["deplacement.distance_max_km"],
                    "modalite_suggeree": Modalite.EN_LIGNE,
                },
            )
        km_aller, deplacement = frais_deplacement(
            distance_exacte,
            tarif_km_fcfa=tarifs["deplacement.tarif_km_fcfa"],
            minimum_fcfa=tarifs["deplacement.minimum_fcfa"],
            plafond_fcfa=tarifs["deplacement.plafond_fcfa"],
        )
    taux = tarifs["commission.consultation_taux_pb"]
    commission = commission_consultation(consultation, taux_pb=taux)
    total = consultation + deplacement
    return {
        "consultation_fcfa": consultation,
        "deplacement_fcfa": deplacement,
        "distance_km_aller": km_aller,
        "distance_exacte_km": round(distance_exacte, 3) if distance_exacte is not None else None,
        "commission_consultation_fcfa": commission,
        "taux_commission_pb": taux,
        "total_fcfa": total,
        "gain_ingénieur_fcfa": consultation - commission + deplacement,
    }


class ServiceAccompagnement:
    """Devis, création, listes, détail, transitions, planification et RDV manqué."""

    # --- Devis et création -------------------------------------------------

    @staticmethod
    def devis(
        agriculteur: Utilisateur,
        *,
        exploitation_id: str,
        ingenieur_id: str,
        modalite: str,
    ) -> dict[str, Any]:
        exploitation = ServiceAccompagnement._exploitation_ou_404(agriculteur, exploitation_id)
        profil = _verifier_ingénieur_valide_disponible(ingenieur_id)
        if modalite not in Modalite.values:
            raise ValidationMetier(
                code="modalite_inconnue",
                message=f"Modalité inconnue : {modalite}.",
                details={"champ": "modalite", "valeurs": list(Modalite.values)},
            )
        dans_zone, _ = _verifier_zone_couvre_exploitation(profil, exploitation)
        if modalite == Modalite.PRESENTIEL and not dans_zone:
            raise ValidationMetier(
                code="hors_zone_ingenieur",
                message=(
                    "L'exploitation est hors de la zone d'intervention de "
                    "l'ingénieur. Choisissez un autre ingénieur ou demandez "
                    "un accompagnement en ligne."
                ),
            )
        return _calculer_devis(profil, exploitation, modalite)

    @classmethod
    @transaction.atomic
    def creer(
        cls,
        agriculteur: Utilisateur,
        *,
        exploitation_id: str,
        ingenieur_id: str,
        modalite: str,
        motif: str,
        description: str | None = None,
    ) -> Accompagnement:
        """POST /accompagnements — crée en `demandee`, sans écrire le devis."""
        if modalite not in Modalite.values:
            raise ValidationMetier(
                code="modalite_inconnue",
                message=f"Modalité inconnue : {modalite}.",
                details={"champ": "modalite", "valeurs": list(Modalite.values)},
            )
        motif_propre = (motif or "").strip()
        if not motif_propre:
            raise ValidationMetier(
                code="motif_obligatoire",
                message="Le motif de la demande est obligatoire.",
                details={"champ": "motif"},
            )
        exploitation = cls._exploitation_ou_404(agriculteur, exploitation_id)
        profil = _verifier_ingénieur_valide_disponible(ingenieur_id)
        dans_zone, _ = _verifier_zone_couvre_exploitation(profil, exploitation)
        if modalite == Modalite.PRESENTIEL and not dans_zone:
            raise ValidationMetier(
                code="hors_zone_ingenieur",
                message="Exploitation hors zone d'intervention de l'ingénieur.",
            )
        # Validation immédiate du déplacement présentiel : si distance_max
        # dépassée, on refuse la demande plutôt que de créer un accompagnement
        # impossible à planifier.
        if modalite == Modalite.PRESENTIEL:
            _calculer_devis(profil, exploitation, modalite)
        accompagnement = Accompagnement.objects.create(
            agriculteur=agriculteur,
            exploitation=exploitation,
            ingenieur_id=profil.utilisateur_id,
            modalite=modalite,
            motif=motif_propre[:255],
            description=(description or "").strip() or None,
            statut=StatutAccompagnement.DEMANDEE,
        )
        AffectationIngenieur.objects.create(
            accompagnement=accompagnement,
            ingenieur_id=profil.utilisateur_id,
            debut=maintenant(),
        )
        _historiser(accompagnement, None, StatutAccompagnement.DEMANDEE, agriculteur, motif_propre)
        notifier(
            profil.utilisateur,
            "accompagnement",
            "Nouvelle demande d'accompagnement",
            f"{agriculteur.nom} — {motif_propre}",
            lien=f"/ingenieur/accompagnements/{accompagnement.id}",
        )
        return accompagnement

    @classmethod
    def _exploitation_ou_404(cls, agriculteur: Utilisateur, exploitation_id: str) -> Exploitation:
        exploitation = Exploitation.objects.filter(
            id=exploitation_id, agriculteur=agriculteur
        ).first()
        if exploitation is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Exploitation introuvable.",
            )
        return exploitation

    # --- Listes et lecture -------------------------------------------------

    @staticmethod
    def liste_agriculteur(agriculteur: Utilisateur) -> list[Accompagnement]:
        return list(
            Accompagnement.objects.filter(agriculteur=agriculteur).select_related(
                "exploitation", "ingenieur"
            )
        )

    @staticmethod
    def liste_ingenieur(ingenieur: Utilisateur) -> list[Accompagnement]:
        return list(
            Accompagnement.objects.filter(ingenieur=ingenieur)
            .exclude(statut=StatutAccompagnement.DEMANDEE)
            .select_related("exploitation", "agriculteur")
        )

    @staticmethod
    def demandes_ingenieur(ingenieur: Utilisateur) -> list[Accompagnement]:
        return list(
            Accompagnement.objects.filter(
                ingenieur=ingenieur, statut=StatutAccompagnement.DEMANDEE
            ).select_related("exploitation", "agriculteur")
        )

    @classmethod
    def obtenir_visible(cls, utilisateur: Utilisateur, identifiant: str) -> Accompagnement:
        accompagnement = (
            Accompagnement.objects.select_related(
                "agriculteur", "ingenieur", "exploitation"
            )
            .filter(id=identifiant)
            .first()
        )
        if accompagnement is None:
            raise NonTrouve()
        role = getattr(utilisateur, "role", None)
        if role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            return accompagnement
        if accompagnement.agriculteur_id == utilisateur.id:
            return accompagnement
        if accompagnement.ingenieur_id == utilisateur.id:
            return accompagnement
        # Un autre utilisateur connecté voit 404, jamais 403.
        raise NonTrouve()

    @staticmethod
    def historique(accompagnement: Accompagnement) -> list[HistoriqueStatut]:
        return list(
            HistoriqueStatut.objects.filter(
                entite_type="accompagnement", entite_id=accompagnement.id
            ).order_by("cree_le")
        )

    @staticmethod
    def affectations(accompagnement: Accompagnement) -> list[AffectationIngenieur]:
        return list(
            accompagnement.affectations.select_related("ingenieur").order_by("-debut")
        )

    # --- Transitions côté ingénieur ---------------------------------------

    @classmethod
    @transaction.atomic
    def accepter(cls, ingenieur: Utilisateur, identifiant: str) -> Accompagnement:
        accompagnement = cls._accompagnement_ing_ou_404(ingenieur, identifiant)
        if accompagnement.statut != StatutAccompagnement.DEMANDEE:
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.ACCEPTEE, "accompagnement"
            )
        _transition(accompagnement, StatutAccompagnement.ACCEPTEE, ingenieur, None)
        notifier(
            accompagnement.agriculteur,
            "accompagnement",
            "Votre demande est acceptée",
            "Vous pouvez confirmer et payer pour finaliser la planification.",
            lien=f"/agriculteur/accompagnement/{accompagnement.id}",
        )
        return accompagnement

    @classmethod
    @transaction.atomic
    def refuser(cls, ingenieur: Utilisateur, identifiant: str, motif: str) -> Accompagnement:
        accompagnement = cls._accompagnement_ing_ou_404(ingenieur, identifiant)
        motif_propre = (motif or "").strip()
        if not motif_propre:
            raise ValidationMetier(
                code="motif_obligatoire",
                message="Un refus doit être motivé.",
                details={"champ": "motif"},
            )
        if accompagnement.statut != StatutAccompagnement.DEMANDEE:
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.REFUSEE, "accompagnement"
            )
        # Fermeture de l'affectation active (motif `refus`).
        cls._cloturer_affectation(accompagnement, MotifFinAffectation.REFUS, ingenieur)
        _transition(accompagnement, StatutAccompagnement.REFUSEE, ingenieur, motif_propre)
        notifier(
            accompagnement.agriculteur,
            "accompagnement",
            "Votre demande est refusée",
            motif_propre,
            lien="/agriculteur/accompagnement",
        )
        return accompagnement

    @classmethod
    def _accompagnement_ing_ou_404(cls, ingenieur: Utilisateur, identifiant: str) -> Accompagnement:
        accompagnement = (
            Accompagnement.objects.select_for_update().select_related("agriculteur").filter(
                id=identifiant, ingenieur=ingenieur
            )
        ).first()
        if accompagnement is None:
            raise NonTrouve()
        return accompagnement

    @staticmethod
    def _cloturer_affectation(
        accompagnement: Accompagnement,
        motif_fin: str,
        acteur: Any,
    ) -> None:
        active = accompagnement.affectations.filter(fin__isnull=True).first()
        if active is None:
            return
        active.fin = maintenant()
        active.motif_fin = motif_fin
        active.save(update_fields=["fin", "motif_fin", "maj_le"])

    # --- Planification, conflit de créneau --------------------------------

    @classmethod
    @transaction.atomic
    def planifier(
        cls,
        ingenieur: Utilisateur,
        identifiant: str,
        *,
        date_debut: datetime,
        date_fin: datetime,
        lieu_rdv_geojson: dict[str, Any] | None,
        lieu_rdv_texte: str | None,
        lien_visio: str | None,
    ) -> Accompagnement:
        """POST /{id}/planifier — fige le montant et verrou le créneau.

        L'accompagnement peut être `acceptee` (planification initiale, qui
        sera suivie d'un paiement) ou `manquee` (reprogrammation après
        RDV manqué).
        """
        accompagnement = cls._accompagnement_ing_ou_404(ingenieur, identifiant)
        if accompagnement.statut not in (
            StatutAccompagnement.ACCEPTEE,
            StatutAccompagnement.MANQUEE,
        ):
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.PLANIFIEE, "accompagnement"
            )
        if date_fin <= date_debut:
            raise ValidationMetier(
                code="duree_invalid",
                message="La date de fin doit suivre la date de début.",
                details={"champ": "date_fin"},
            )
        if date_debut <= maintenant():
            raise ValidationMetier(
                code="date_passee",
                message="Le rendez-vous doit être fixé dans le futur.",
                details={"champ": "date_debut"},
            )
        if accompagnement.modalite == Modalite.PRESENTIEL:
            if lieu_rdv_geojson is None and not (lieu_rdv_texte or "").strip():
                raise ValidationMetier(
                    code="lieu_requis",
                    message="Un rendez-vous présentiel exige une localisation ou une adresse.",
                    details={"champ": "lieu_rdv"},
                )
            if lieu_rdv_geojson is not None:
                valider_geojson(lieu_rdv_geojson)
        else:
            if not (lien_visio or "").strip():
                raise ValidationMetier(
                    code="lien_requis",
                    message=(
                        "Un rendez-vous en ligne exige un lien de visioconférence."
                    ),
                    details={"champ": "lien_visio"},
                )

        # Conflit de créneau : verrou pessimiste sur les RDV déjà planifiés
        # ou manqués du même ingénieur qui chevauchent [debut, fin] demandé.
        cls._verifier_conflit_creneau(
            ingenieur_id=ingenieur.id,
            date_debut=date_debut,
            date_fin=date_fin,
            exclure_id=accompagnement.id,
        )

        # Fige le montant et la distance si pas encore figé (reprogrammation :
        # on conserve les montants déjà saisis au premier cycle).
        if accompagnement.prix_consultation_fcfa is None:
            profil = IngenieurProfil.objects.filter(utilisateur_id=ingenieur.id).first()
            if profil is None:
                raise ValidationMetier(
                    code="profil_ingenieur_manquant",
                    message="Profil ingénieur manquant.",
                )
            devis = _calculer_devis(profil, accompagnement.exploitation, accompagnement.modalite)
            accompagnement.prix_consultation_fcfa = devis["consultation_fcfa"]
            accompagnement.frais_deplacement_fcfa = devis["deplacement_fcfa"]
            accompagnement.distance_km = devis["distance_km_aller"]
            accompagnement.commission_consultation_fcfa = devis["commission_consultation_fcfa"]
        accompagnement.date_rdv_debut = date_debut
        accompagnement.date_rdv_fin = date_fin
        accompagnement.lieu_rdv_texte = (lieu_rdv_texte or "").strip() or None
        accompagnement.lien_visio = (lien_visio or "").strip() or None
        if accompagnement.modalite == Modalite.PRESENTIEL and lieu_rdv_geojson is not None:
            # ChampPoint attend le dict GeoJSON, la conversion MySQL se fait au save.
            accompagnement.lieu_rdv = lieu_rdv_geojson
        accompagnement.save()
        # La transition finale (planifiee) se fait au webhook de paiement
        # (Q-05). En `manquee → planifiee` (reprogrammation après RDV
        # manqué) : la reprogrammation est gratuite et directe, pas de
        # nouveau paiement.
        if accompagnement.statut == StatutAccompagnement.MANQUEE:
            _transition(accompagnement, StatutAccompagnement.PLANIFIEE, ingenieur, None)
        notifier(
            accompagnement.agriculteur,
            "accompagnement",
            "Un rendez-vous est planifié",
            f"Le {date_debut:%d/%m/%Y à %H:%M}.",
            lien=f"/agriculteur/accompagnement/{accompagnement.id}",
        )
        return accompagnement

    @staticmethod
    def _verifier_conflit_creneau(
        *,
        ingenieur_id: str,
        date_debut: datetime,
        date_fin: datetime,
        exclure_id: str | None,
    ) -> None:
        """SELECT FOR UPDATE sur les lignes qui chevauchent le créneau demandé."""
        requete = Accompagnement.objects.select_for_update().filter(
            ingenieur_id=ingenieur_id,
            statut__in=(StatutAccompagnement.PLANIFIEE, StatutAccompagnement.MANQUEE),
            date_rdv_debut__isnull=False,
            date_rdv_fin__isnull=False,
            date_rdv_debut__lt=date_fin,
            date_rdv_fin__gt=date_debut,
        )
        if exclure_id:
            requete = requete.exclude(id=exclure_id)
        conflit = requete.first()
        if conflit is not None:
            raise Conflit(
                code="conflit_creneau",
                message=(
                    "Ce créneau chevauche un autre rendez-vous déjà planifié "
                    "pour cet ingénieur."
                ),
                details={
                    "accompagnement_conflit": conflit.id,
                    "debut": conflit.date_rdv_debut.isoformat() if conflit.date_rdv_debut else None,
                    "fin": conflit.date_rdv_fin.isoformat() if conflit.date_rdv_fin else None,
                },
            )

    # --- Confirmation par l'agriculteur → paiement -------------------------

    @classmethod
    @transaction.atomic
    def confirmer(
        cls,
        agriculteur: Utilisateur,
        identifiant: str,
        *,
        operateur: str,
        telephone: str | None = None,
        cle_idempotence: str | None = None,
        url_base_pwa: str = "",
    ) -> tuple[Paiement, str, bool]:
        """POST /{id}/confirmer — crée le paiement et renvoie l'URL NotchPay.

        Le passage à `planifiee` se fera par `sur_paiement_reussi` au webhook
        (Q-05).
        """
        accompagnement = (
            Accompagnement.objects.select_for_update().filter(
                id=identifiant, agriculteur=agriculteur
            )
        ).first()
        if accompagnement is None:
            raise NonTrouve()
        if accompagnement.statut != StatutAccompagnement.ACCEPTEE:
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.PLANIFIEE, "accompagnement"
            )
        if accompagnement.date_rdv_debut is None:
            raise ValidationMetier(
                code="planification_requise",
                message=(
                    "L'ingénieur doit d'abord planifier le rendez-vous avant "
                    "que l'agriculteur puisse confirmer et payer."
                ),
            )
        if accompagnement.prix_consultation_fcfa is None:
            raise ValidationMetier(
                code="montant_non_figure",
                message="Le montant n'a pas encore été figé par l'ingénieur.",
            )
        import uuid as _uuid

        from apps.paiements.services import _nouvelle_reference_interne

        montant = int(
            (accompagnement.prix_consultation_fcfa or 0)
            + (accompagnement.frais_deplacement_fcfa or 0)
        )
        if montant <= 0:
            raise ValidationMetier(
                code="montant_invalide",
                message="Le total à payer est nul ou négatif.",
            )
        # Idempotence : une même clé renvoie le même paiement (double clic).
        if cle_idempotence:
            existant = Paiement.objects.filter(cle_idempotence=cle_idempotence).first()
            if existant is not None:
                if (
                    existant.accompagnement_id != accompagnement.id
                    or existant.montant_fcfa != montant
                ):
                    raise Conflit(
                        code="cle_idempotence_differente",
                        message="Clé d'idempotence déjà utilisée pour un autre paiement.",
                    )
                return existant, existant.url_paiement or "", True
        paiement = Paiement.objects.create(
            objet=ObjetPaiement.ACCOMPAGNEMENT,
            accompagnement=accompagnement,
            payeur=agriculteur,
            montant_fcfa=montant,
            operateur=operateur,
            reference_interne=_nouvelle_reference_interne(),
            cle_idempotence=cle_idempotence or _uuid.uuid4().hex,
            statut="initie",
        )
        resultat = passerelle_paiement().initialiser(
            montant_fcfa=montant,
            reference_interne=paiement.reference_interne,
            description=(
                f"Accompagnement {accompagnement.id[:8]} — {accompagnement.motif} — AgriSens"
            ),
            retour_url=(
                f"{url_base_pwa}/paiements/retour?reference={paiement.reference_interne}"
            ),
            telephone=telephone,
            email=getattr(agriculteur, "email", None),
            nom_client=getattr(agriculteur, "nom", None),
        )
        paiement.reference_notchpay = resultat["reference_externe"]
        paiement.url_paiement = resultat["url_paiement"]
        paiement.statut = "en_attente"
        paiement.save()
        return paiement, resultat["url_paiement"], False

    # --- Changement d'ingénieur (Q-29) ------------------------------------

    @classmethod
    @transaction.atomic
    def changer_ingenieur(
        cls,
        utilisateur: Utilisateur,
        identifiant: str,
        *,
        nouveau_ingénieur_id: str,
    ) -> Accompagnement:
        """Clôt l'affectation active, repasse à `demandee` chez le nouvel ingénieur.

        Avant paiement (agriculteur ou admin) : depuis `demandee` ou `acceptee`.
        Après paiement (admin uniquement) : depuis `planifiee`, montant conservé.
        """
        accompagnement = (
            Accompagnement.objects.select_for_update().filter(id=identifiant).first()
        )
        if accompagnement is None:
            raise NonTrouve()
        role = getattr(utilisateur, "role", None)
        est_admin = role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN)
        est_agriculteur = (
            role == RoleUtilisateur.AGRICULTEUR and accompagnement.agriculteur_id == utilisateur.id
        )
        if not (est_admin or est_agriculteur):
            raise NonTrouve()
        if accompagnement.statut == StatutAccompagnement.PLANIFIEE and not est_admin:
            # Après paiement : seul l'admin peut changer (Q-29).
            raise ValidationMetier(
                code="changement_apres_paiement",
                message=(
                    "Après paiement, seul un administrateur peut changer "
                    "l'ingénieur (montant conservé, journal Q-29)."
                ),
            )
        if accompagnement.statut not in (
            StatutAccompagnement.DEMANDEE,
            StatutAccompagnement.ACCEPTEE,
            StatutAccompagnement.PLANIFIEE,
        ):
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.DEMANDEE, "accompagnement"
            )
        profil = _verifier_ingénieur_valide_disponible(nouveau_ingénieur_id)
        if profil.utilisateur_id == accompagnement.ingenieur_id:
            raise ValidationMetier(
                code="ingenieur_identique",
                message="Le nouvel ingénieur est identique à l'actuel.",
                details={"champ": "ingenieur_id"},
            )
        # Ferme l'affectation active avec motif `changement`.
        cls._cloturer_affectation(
            accompagnement, MotifFinAffectation.CHANGEMENT, utilisateur
        )
        accompagnement.ingenieur_id = profil.utilisateur_id
        # Si après paiement (admin only), on conserve les montants figés.
        accompagnement.save(update_fields=["ingenieur", "maj_le"])
        AffectationIngenieur.objects.create(
            accompagnement=accompagnement,
            ingenieur_id=profil.utilisateur_id,
            debut=maintenant(),
        )
        # Repasse en `demandee` (nouvel ingénieur à accepter).
        if accompagnement.statut != StatutAccompagnement.DEMANDEE:
            _transition(
                accompagnement,
                StatutAccompagnement.DEMANDEE,
                utilisateur,
                "Changement d'ingénieur (Q-29).",
            )
        notifier(
            profil.utilisateur,
            "accompagnement",
            "Nouvelle demande d'accompagnement",
            f"{accompagnement.agriculteur.nom} — {accompagnement.motif}",
            lien=f"/ingenieur/accompagnements/{accompagnement.id}",
        )
        return accompagnement

    # --- RDV manqué (Q-30) -------------------------------------------------

    @classmethod
    @transaction.atomic
    def marquer_manque(
        cls,
        utilisateur: Utilisateur,
        identifiant: str,
        *,
        absent: str,
    ) -> Accompagnement:
        """POST /{id}/marquer-manque — parties ou admin.

        Q-30 :
        - ingénieur absent → remboursement intégral créé et approuvé automatiquement ;
        - agriculteur absent → pas de remboursement ; une reprogrammation
          gratuite (statut `manquee`, l'ingénieur peut re-planifier) ; au-delà
          d'une reprogrammation, le chemin `annulee` reste ouvert.
        """
        accompagnement = (
            Accompagnement.objects.select_for_update().filter(id=identifiant).first()
        )
        if accompagnement is None:
            raise NonTrouve()
        role = getattr(utilisateur, "role", None)
        est_partie = (
            accompagnement.agriculteur_id == utilisateur.id
            or accompagnement.ingenieur_id == utilisateur.id
        )
        est_admin = role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN)
        if not (est_partie or est_admin):
            raise NonTrouve()
        if accompagnement.statut != StatutAccompagnement.PLANIFIEE:
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.MANQUEE, "accompagnement"
            )
        if absent not in AbsentRDV.values:
            raise ValidationMetier(
                code="absent_inconnu",
                message=f"Valeur d'`absent` inconnue : {absent}.",
                details={"champ": "absent", "valeurs": list(AbsentRDV.values)},
            )
        accompagnement.nb_rdv_manques = (accompagnement.nb_rdv_manques or 0) + 1
        accompagnement.save(update_fields=["nb_rdv_manques", "maj_le"])
        _transition(
            accompagnement,
            StatutAccompagnement.MANQUEE,
            utilisateur,
            f"RDV manqué — {absent} absent.",
        )
        if absent == AbsentRDV.INGENIEUR:
            cls._remboursement_auto(accompagnement, utilisateur)
            notifier(
                accompagnement.agriculteur,
                "accompagnement",
                "RDV manqué par l'ingénieur",
                "Remboursement intégral automatique créé (journal Q-30).",
                lien=f"/agriculteur/accompagnement/{accompagnement.id}",
            )
        else:
            notifier(
                accompagnement.agriculteur
                if utilisateur.id != accompagnement.agriculteur_id
                else accompagnement.ingenieur,
                "accompagnement",
                "RDV manqué",
                "Agriculteur absent : une reprogrammation gratuite est possible.",
                lien=f"/agriculteur/accompagnement/{accompagnement.id}",
            )
        return accompagnement

    @staticmethod
    def _remboursement_auto(accompagnement: Accompagnement, acteur: Any) -> None:
        """Crée et approuve un remboursement intégral (Q-30, ingénieur absent).

        Exécution reste manuelle (Q-26) : l'admin enregistre la référence du
        renvoi opérateur plus tard. Ici : statut = `approuve`, montant accordé
        = montant payé, notification à l'agriculteur.
        """
        from apps.litiges.models import (
            MotifRemboursement,
            Remboursement,
            StatutRemboursement,
        )

        paiement = (
            Paiement.objects.filter(
                accompagnement=accompagnement, statut="reussi"
            )
            .order_by("-cree_le")
            .first()
        )
        if paiement is None:
            # Aucun paiement réussi : rien à rembourser, la transition
            # s'arrête à `manquee`.
            return
        remboursement = Remboursement.objects.create(
            paiement=paiement,
            commande=None,
            accompagnement=accompagnement,
            demandeur=accompagnement.agriculteur,
            motif=MotifRemboursement.ANNULATION,
            montant_demande_fcfa=paiement.montant_fcfa,
            montant_accorde_fcfa=paiement.montant_fcfa,
            statut=StatutRemboursement.APPROUVE,
            statut_commande_origine="",
            motif_decision=(
                "RDV manqué par l'ingénieur : remboursement intégral "
                "automatique (journal Q-30)."
            ),
            decide_par=acteur if getattr(acteur, "role", None) in (
                RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN
            ) else None,
            decide_le=maintenant(),
        )
        HistoriqueStatut.objects.create(
            entite_type="remboursement",
            entite_id=remboursement.id,
            ancien_statut=None,
            nouveau_statut=StatutRemboursement.APPROUVE,
            acteur=acteur,
            motif="Automatique (Q-30, ingénieur absent).",
        )

    # --- Clôture (observation obligatoire) --------------------------------

    @classmethod
    @transaction.atomic
    def terminer(
        cls,
        ingenieur: Utilisateur,
        identifiant: str,
        *,
        observation: str,
    ) -> Accompagnement:
        accompagnement = cls._accompagnement_ing_ou_404(ingenieur, identifiant)
        observation_propre = (observation or "").strip()
        if not observation_propre:
            raise ValidationMetier(
                code="observation_obligatoire",
                message=(
                    "Le compte rendu (observation) est obligatoire pour clôturer "
                    "un accompagnement."
                ),
                details={"champ": "observation"},
            )
        if accompagnement.statut != StatutAccompagnement.PLANIFIEE:
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.TERMINEE, "accompagnement"
            )
        if accompagnement.date_rdv_fin is None or maintenant() < accompagnement.date_rdv_fin:
            raise ValidationMetier(
                code="rdv_non_termine",
                message="Impossible de clôturer avant la fin du rendez-vous.",
                details={"date_fin": (
                    accompagnement.date_rdv_fin.isoformat()
                    if accompagnement.date_rdv_fin
                    else None
                )},
            )
        accompagnement.observation = observation_propre
        accompagnement.save(update_fields=["observation", "maj_le"])
        cls._cloturer_affectation(accompagnement, MotifFinAffectation.TERMINE, ingenieur)
        _transition(accompagnement, StatutAccompagnement.TERMINEE, ingenieur, None)
        notifier(
            accompagnement.agriculteur,
            "accompagnement",
            "Accompagnement terminé",
            "Le compte rendu de l'ingénieur est disponible dans votre historique.",
            lien=f"/agriculteur/accompagnement/{accompagnement.id}",
        )
        return accompagnement

    # --- Annulation par l'agriculteur -------------------------------------

    @classmethod
    @transaction.atomic
    def annuler(cls, agriculteur: Utilisateur, identifiant: str) -> Accompagnement:
        accompagnement = (
            Accompagnement.objects.select_for_update().filter(
                id=identifiant, agriculteur=agriculteur
            )
        ).first()
        if accompagnement is None:
            raise NonTrouve()
        if accompagnement.statut not in (
            StatutAccompagnement.DEMANDEE,
            StatutAccompagnement.ACCEPTEE,
            StatutAccompagnement.PLANIFIEE,
            StatutAccompagnement.MANQUEE,
        ):
            raise TransitionInvalide(
                accompagnement.statut, StatutAccompagnement.ANNULEE, "accompagnement"
            )
        cls._cloturer_affectation(accompagnement, MotifFinAffectation.DESISTEMENT, agriculteur)
        _transition(
            accompagnement, StatutAccompagnement.ANNULEE, agriculteur, "Annulation agriculteur."
        )
        if accompagnement.ingenieur_id is not None:
            notifier(
                accompagnement.ingenieur,
                "accompagnement",
                "Accompagnement annulé",
                "L'agriculteur a annulé sa demande.",
                lien="/ingenieur/accompagnements",
            )
        return accompagnement

    # --- Accès exploitation (F-04 registre) ---------------------------------

    @staticmethod
    def peut_acceder_exploitation(utilisateur: Utilisateur, exploitation: Exploitation) -> bool:
        """Un ingénieur ne voit l'exploitation que pendant un accompagnement actif."""
        role = getattr(utilisateur, "role", None)
        if role in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            return True
        if exploitation.agriculteur_id == utilisateur.id:
            return True
        if role == RoleUtilisateur.INGENIEUR_AGRONOME:
            return Accompagnement.objects.filter(
                ingenieur=utilisateur,
                exploitation=exploitation,
                statut__in=STATUTS_ACCES_EXPLOITATION,
            ).exists()
        # Registre F-04 : un accès explicite accordé via `AccesExploitation`.
        return AccesExploitation.objects.filter(
            exploitation=exploitation, utilisateur=utilisateur, actif=True
        ).exists()


# --- Abonnement au webhook PaiementReussi -----------------------------------


def sur_paiement_reussi(evenement: Any) -> None:
    """Abonné `PaiementReussi` : `acceptee → planifiee` pour un accompagnement.

    Le bus d'événements du backend (`commun/evenements.py`) est synchrone ;
    l'argument `accompagnement_id` est absent sur les événements liés à une
    commande : on ignore silencieusement dans ce cas.
    """
    accompagnement_id = getattr(evenement, "accompagnement_id", "") or ""
    if not accompagnement_id:
        return
    with transaction.atomic():
        accompagnement = (
            Accompagnement.objects.select_for_update().filter(id=accompagnement_id).first()
        )
        if accompagnement is None:
            return
        if accompagnement.statut != StatutAccompagnement.ACCEPTEE:
            # Déjà avancé ou annulé : idempotence.
            return
        _transition(
            accompagnement,
            StatutAccompagnement.PLANIFIEE,
            None,
            f"Webhook paiement réussi ({evenement.paiement_id}).",
        )
        notifier(
            accompagnement.ingenieur,
            "accompagnement",
            "Le rendez-vous est confirmé",
            f"Paiement de {evenement.montant_fcfa} FCFA reçu.",
            lien=f"/ingenieur/accompagnements/{accompagnement.id}",
        )


# Alias singleton (cohérence avec `parametres`, `audit`, `historique`).
accompagnements = ServiceAccompagnement()

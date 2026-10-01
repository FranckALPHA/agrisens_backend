"""Logique métier des profils professionnels (prompt F-03 backend point 3).

Règles appliquées ici :
- spécialités validées contre le référentiel `specialite` (Q-10) ;
- tarif borné par les paramètres `consultation.prix_min_fcfa` /
  `consultation.prix_max_fcfa` (Q-06) — jamais de nombre en dur ;
- rayon de zone borné par `consultation.zone_rayon_km_max` (hypothèse Q-34) ;
- un ingénieur ne peut se rendre disponible qu'avec un profil complet
  (09 ING-01/ING-03 : la mise en relation suppose zone et tarif) ;
- ressource d'un autre utilisateur → 404 ;
- la décision de validation est réservée aux admins, notifie l'ingénieur
  et passe par JOURNAL_AUDIT + HISTORIQUE_STATUT (D-13).
"""

from typing import Any

from commun.exceptions import ErreurMetier, NonTrouve, TransitionInvalide, ValidationMetier
from commun.geo import valider_geojson
from commun.horloge import maintenant
from django.db import transaction
from rest_framework import status

from apps.administration.services import audit, historique, parametres
from apps.fichiers.models import ContexteFichier, Fichier
from apps.notifications.services import notifier
from apps.profils.models import (
    IngenieurProfil,
    LivreurProfil,
    PieceJustificative,
    StatutJustificatif,
    StatutValidation,
    TypeJustificatif,
)
from apps.referentiels.models import Referentiel
from apps.utilisateurs.models import RoleUtilisateur, StatutCompte, Utilisateur

_LONGUEUR_BIOGRAPHIE_MAX = 2000
_LONGUEUR_MODALITES_MAX = 1000

ROLES_ADMIN = ("admin", "super_admin")


def _valider_point_cameroun(geojson: Any, champ: str) -> dict[str, Any]:
    if not isinstance(geojson, dict) or geojson.get("type") != "Point":
        raise ValidationMetier(
            code="geometrie_invalide",
            message=f"{champ} doit être un GeoJSON Point.",
            details={"champ": champ},
        )
    valider_geojson(geojson)
    return geojson


class ServiceProfilIngenieur:
    """Profil professionnel, justificatifs et validation administrative."""

    @staticmethod
    def _profil_ou_creer(utilisateur: Utilisateur) -> IngenieurProfil:
        if utilisateur.role != RoleUtilisateur.INGENIEUR_AGRONOME:
            raise ErreurMetier(
                code="permission_refusee",
                message="Réservé aux ingénieurs agronomes.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )
        profil, _ = IngenieurProfil.objects.get_or_create(utilisateur_id=utilisateur.id)
        return profil

    @classmethod
    def obtenir_profil(cls, utilisateur: Utilisateur) -> IngenieurProfil:
        """Lecture du profil ; créé vide à la première visite (même en attente)."""
        return cls._profil_ou_creer(utilisateur)

    @staticmethod
    def _valider_specialites(valeur: Any) -> list[str]:
        if not isinstance(valeur, list) or not valeur:
            raise ValidationMetier(
                code="specialites_invalides",
                message="Au moins une spécialité est requise (liste de codes).",
                details={"champ": "specialites"},
            )
        codes = {str(c) for c in valeur}
        connus = set(
            Referentiel.objects.filter(type="specialite", actif=True).values_list("code", flat=True)
        )
        inconnues = sorted(codes - connus)
        if inconnues:
            raise ValidationMetier(
                code="specialite_inconnue",
                message=f"Spécialité inconnue au référentiel : {', '.join(inconnues)}.",
                details={"champ": "specialites", "codes_inconnus": inconnues},
            )
        return sorted(codes)

    @classmethod
    def _valider_region(cls, valeur: str | None) -> str | None:
        if not valeur:
            return None
        connu = Referentiel.objects.filter(type="region", actif=True, code=valeur).exists()
        if not connu:
            raise ValidationMetier(
                code="region_inconnue",
                message=f"Région inconnue au référentiel : {valeur}.",
                details={"champ": "region"},
            )
        return valeur

    @classmethod
    @transaction.atomic
    def modifier_profil(cls, utilisateur: Utilisateur, donnees: dict[str, Any]) -> IngenieurProfil:
        """PUT /ingenieurs/moi/profil — validation métier puis enregistrement."""
        profil = cls._profil_ou_creer(utilisateur)

        if "specialites" in donnees:
            profil.specialites = cls._valider_specialites(donnees["specialites"])
        if "region" in donnees:
            profil.region = cls._valider_region(donnees["region"])
        if donnees.get("zone_centre") is not None:
            profil.zone_centre = _valider_point_cameroun(donnees["zone_centre"], "zone_centre")
        elif "zone_centre" in donnees:
            profil.zone_centre = None

        if "zone_rayon_km" in donnees:
            rayon = donnees["zone_rayon_km"]
            if rayon is None:
                profil.zone_rayon_km = None
            else:
                rayon_max = parametres.obtenir("consultation.zone_rayon_km_max")
                if not isinstance(rayon, int) or not (1 <= rayon <= rayon_max):
                    raise ValidationMetier(
                        code="zone_rayon_hors_bornes",
                        message=(
                            f"Le rayon d'intervention doit être un entier entre 1 et "
                            f"{rayon_max} km."
                        ),
                        details={"champ": "zone_rayon_km", "maximum": rayon_max},
                    )
                profil.zone_rayon_km = rayon

        if "tarif_consultation_fcfa" in donnees:
            tarif = donnees["tarif_consultation_fcfa"]
            if tarif is None:
                profil.tarif_consultation_fcfa = None
            else:
                prix_min = parametres.obtenir("consultation.prix_min_fcfa")
                prix_max = parametres.obtenir("consultation.prix_max_fcfa")
                if not isinstance(tarif, int) or not (prix_min <= tarif <= prix_max):
                    raise ValidationMetier(
                        code="tarif_hors_bornes",
                        message=(
                            f"Le tarif de consultation doit être un entier FCFA entre "
                            f"{prix_min} et {prix_max}."
                        ),
                        details={
                            "champ": "tarif_consultation_fcfa",
                            "minimum": prix_min,
                            "maximum": prix_max,
                        },
                    )
                profil.tarif_consultation_fcfa = tarif

        # Hypothèse Q-36 : biographie et modalités, textes libres bornés.
        for champ, limite in (
            ("biographie", _LONGUEUR_BIOGRAPHIE_MAX),
            ("modalites", _LONGUEUR_MODALITES_MAX),
        ):
            if champ in donnees:
                valeur = donnees[champ]
                if valeur is not None and len(str(valeur)) > limite:
                    raise ValidationMetier(
                        code="texte_trop_long",
                        message=f"{champ} : {limite} caractères au maximum.",
                        details={"champ": champ, "maximum": limite},
                    )
                setattr(profil, champ, valeur or None)

        if "photo_id" in donnees:
            profil.photo = cls._fichier_photo(utilisateur, donnees["photo_id"])

        if "disponible" in donnees and donnees["disponible"]:
            cls._exiger_profil_complet(profil)
            profil.disponible = True
        elif "disponible" in donnees:
            profil.disponible = False

        profil.save()
        return profil

    @staticmethod
    def _fichier_photo(utilisateur: Utilisateur, fichier_id: Any) -> Fichier | None:
        if fichier_id is None:
            return None
        fichier = Fichier.objects.filter(
            id=fichier_id, proprietaire_id=utilisateur.id, contexte=ContexteFichier.ACCOMPAGNEMENT
        ).first()
        # Un fichier qui n'appartient pas à l'appelant est « introuvable » (404).
        if fichier is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            )
        if not fichier.type_mime.startswith("image/"):
            raise ValidationMetier(
                code="photo_invalide",
                message="La photo de profil doit être une image.",
                details={"champ": "photo_id"},
            )
        return fichier

    @staticmethod
    def _exiger_profil_complet(profil: IngenieurProfil) -> None:
        """Disponibilité = profil complet et justificatifs déposés (D-13, ING-02)."""
        manquants = []
        if not profil.specialites:
            manquants.append("specialites")
        if profil.zone_centre is None:
            manquants.append("zone_centre")
        if profil.zone_rayon_km is None:
            manquants.append("zone_rayon_km")
        if profil.tarif_consultation_fcfa is None:
            manquants.append("tarif_consultation_fcfa")
        if profil.statut_validation != StatutValidation.VALIDE:
            manquants.append("validation_admin")
        if manquants:
            raise ValidationMetier(
                code="profil_incomplet",
                message=(
                    "Profil incomplet : rendez-vous disponible uniquement après "
                    "compléter le profil et validation par un administrateur."
                ),
                details={"champs_manquants": manquants},
            )

    # --- Justificatifs -------------------------------------------------------

    @classmethod
    @transaction.atomic
    def ajouter_justificatif(
        cls, utilisateur: Utilisateur, *, fichier_id: str, type_document: str
    ) -> PieceJustificative:
        """POST /ingenieurs/moi/justificatifs — rattache un fichier déjà téléversé."""
        profil = cls._profil_ou_creer(utilisateur)

        if type_document not in TypeJustificatif.values:
            raise ValidationMetier(
                code="type_justificatif_inconnu",
                message=f"Type de justificatif inconnu : « {type_document} ».",
                details={
                    "champ": "type",
                    "valeurs_autorisees": list(TypeJustificatif.values),
                },
            )

        fichier = Fichier.objects.filter(
            id=fichier_id,
            proprietaire_id=utilisateur.id,
            contexte=ContexteFichier.JUSTIFICATIF,
        ).first()
        if fichier is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Fichier introuvable.",
            )

        piece = PieceJustificative.objects.create(
            ingenieur=profil,
            type=type_document,
            fichier=fichier,
            statut=StatutJustificatif.SOUMISE,
        )
        audit.journaliser(
            action="justificatif_depose",
            entite_type="piece_justificative",
            entite_id=piece.id,
            acteur=utilisateur,
            nouvelle_valeur={"type": type_document, "fichier_id": fichier.id},
        )
        return piece

    @classmethod
    def lister_justificatifs(cls, utilisateur: Utilisateur) -> list[PieceJustificative]:
        profil = cls._profil_ou_creer(utilisateur)
        return list(profil.justificatifs.select_related("fichier"))

    # --- Décision administrative --------------------------------------------

    @classmethod
    @transaction.atomic
    def valider(
        cls, admin: Utilisateur, ingenieur_id: str, *, adresse_ip: str | None = None
    ) -> IngenieurProfil:
        """POST /admin/ingenieurs/{id}/valider — réservé aux admins (D-13)."""
        cls._exiger_admin(admin)
        profil = cls._profil_admin_ou_404(ingenieur_id)
        if profil.statut_validation != StatutValidation.EN_ATTENTE:
            raise TransitionInvalide(
                ancien_statut=profil.statut_validation,
                nouveau_statut=StatutValidation.VALIDE,
                entite="profil_ingenieur",
            )

        ancien = profil.statut_validation
        profil.statut_validation = StatutValidation.VALIDE
        profil.motif_refus = None
        profil.valide_par = admin
        profil.valide_le = maintenant()
        profil.save()

        cls._acter_compte(profil, StatutCompte.ACTIF)
        cls._tracer(admin, profil, ancien, StatutValidation.VALIDE, None, adresse_ip)
        notifier(
            profil.utilisateur,
            "administration",
            "Votre profil ingénieur est validé",
            "Vous pouvez maintenant utiliser toutes les fonctions de votre espace.",
            lien="/ingenieur",
        )
        return profil

    @classmethod
    @transaction.atomic
    def refuser(
        cls,
        admin: Utilisateur,
        ingenieur_id: str,
        motif: str,
        *,
        adresse_ip: str | None = None,
    ) -> IngenieurProfil:
        """POST /admin/ingenieurs/{id}/refuser — motif obligatoire, notifié (D-13)."""
        cls._exiger_admin(admin)
        motif = (motif or "").strip()
        if not motif:
            raise ValidationMetier(
                code="motif_obligatoire",
                message="Un refus doit être motivé : le motif est obligatoire.",
                details={"champ": "motif"},
            )
        profil = cls._profil_admin_ou_404(ingenieur_id)
        if profil.statut_validation != StatutValidation.EN_ATTENTE:
            raise TransitionInvalide(
                ancien_statut=profil.statut_validation,
                nouveau_statut=StatutValidation.REFUSE,
                entite="profil_ingenieur",
            )

        ancien = profil.statut_validation
        profil.statut_validation = StatutValidation.REFUSE
        profil.motif_refus = motif
        profil.disponible = False
        profil.save()

        cls._acter_compte(profil, StatutCompte.EN_ATTENTE_VALIDATION)
        cls._tracer(admin, profil, ancien, StatutValidation.REFUSE, motif, adresse_ip)
        notifier(
            profil.utilisateur,
            "administration",
            "Votre profil ingénieur est refusé",
            motif,
            lien="/ingenieur/profil-pro",
        )
        return profil

    @staticmethod
    def _exiger_admin(admin: Utilisateur) -> None:
        if admin.role not in ROLES_ADMIN:
            raise ErreurMetier(
                code="permission_refusee",
                message="Décision réservée aux administrateurs.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )

    @staticmethod
    def _profil_admin_ou_404(ingenieur_id: str) -> IngenieurProfil:
        profil = (
            IngenieurProfil.objects.select_related("utilisateur").filter(
                utilisateur_id=ingenieur_id
            )
        ).first()
        if profil is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Profil ingénieur introuvable.",
            )
        return profil

    @staticmethod
    def _acter_compte(profil: IngenieurProfil, statut: str) -> None:
        """Le compte suit la décision : actif si validé, en attente si refusé."""
        utilisateur = profil.utilisateur
        if utilisateur.statut_compte != statut:
            utilisateur.statut_compte = statut
            utilisateur.save(update_fields=["statut_compte", "maj_le"])

    @staticmethod
    def _tracer(
        admin: Utilisateur,
        profil: IngenieurProfil,
        ancien: str,
        nouveau: str,
        motif: str | None,
        adresse_ip: str | None,
    ) -> None:
        historique.enregistrer_transition(
            entite_type="profil_ingenieur",
            entite_id=profil.utilisateur_id,
            ancien=ancien,
            nouveau=nouveau,
            acteur=admin,
            motif=motif,
        )
        audit.journaliser(
            action=f"ingenieur_{nouveau}",
            entite_type="profil_ingenieur",
            entite_id=profil.utilisateur_id,
            acteur=admin,
            ancienne_valeur={"statut_validation": ancien},
            nouvelle_valeur={"statut_validation": nouveau, "motif": motif},
            adresse_ip=adresse_ip,
        )

    # --- Consultation publique ---------------------------------------------

    @staticmethod
    def lister_valides(*, specialite: str | None, region: str | None) -> list[IngenieurProfil]:
        """GET /ingenieurs — validés et disponibles, filtres specialite/region.

        Le filtre par exploitation (zone) sera ajouté en F-11 (prompt F-03).
        """
        queryset = IngenieurProfil.objects.select_related("utilisateur").filter(
            statut_validation=StatutValidation.VALIDE, disponible=True
        )
        if specialite:
            # JSON contains : MySQL 8 fonctions JSON_SEARCH/JSON_CONTAINS via lookup.
            queryset = queryset.filter(specialites__contains=[specialite])
        if region:
            queryset = queryset.filter(region=region)
        return list(queryset.order_by("-maj_le"))

    @staticmethod
    def obtenir_public(ingenieur_id: str) -> IngenieurProfil:
        """GET /ingenieurs/{id} — profil public d'un ingénieur validé, sans téléphone."""
        profil = (
            IngenieurProfil.objects.select_related("utilisateur").filter(
                utilisateur_id=ingenieur_id,
                statut_validation=StatutValidation.VALIDE,
            )
        ).first()
        if profil is None:
            raise NonTrouve(
                code="ressource_introuvable",
                message="Ingénieur introuvable.",
            )
        return profil


class ServiceProfilLivreur:
    """Disponibilité et position du livreur (LIV-01, prompt F-03 frontend point 4)."""

    @staticmethod
    def _profil_ou_creer(utilisateur: Utilisateur) -> LivreurProfil:
        if utilisateur.role != RoleUtilisateur.LIVREUR:
            raise ErreurMetier(
                code="permission_refusee",
                message="Réservé aux livreurs.",
                statut_http=status.HTTP_403_FORBIDDEN,
            )
        profil, _ = LivreurProfil.objects.get_or_create(utilisateur_id=utilisateur.id)
        return profil

    @classmethod
    def obtenir(cls, utilisateur: Utilisateur) -> LivreurProfil:
        return cls._profil_ou_creer(utilisateur)

    @classmethod
    @transaction.atomic
    def definir_disponibilite(
        cls,
        utilisateur: Utilisateur,
        *,
        disponible: bool,
        position: dict[str, Any] | None = None,
        zone: str | None = None,
    ) -> LivreurProfil:
        """PUT /livreurs/moi/disponibilite — se rendre disponible exige une position."""
        profil = cls._profil_ou_creer(utilisateur)
        if disponible and position is None and profil.position is None:
            raise ValidationMetier(
                code="position_requise",
                message=("Un livreur qui se rend disponible doit indiquer sa position."),
                details={"champ": "position"},
            )
        profil.disponible = bool(disponible)
        if position is not None:
            profil.position = _valider_point_cameroun(position, "position")
            profil.position_maj_le = maintenant()
        if zone is not None:
            profil.zone = (zone or "").strip()[:100] or None
        profil.save()
        return profil

"""Services transverses pour les paramètres, l'audit et l'historique des statuts.

Conventions techniques §5 et prompt F-00 point 15.
"""

from datetime import datetime
from typing import Any

from commun.exceptions import NonTrouve, ValidationMetier
from commun.horloge import maintenant
from django.db import transaction

from apps.administration.models import (
    HistoriqueStatut,
    JournalAudit,
    OffreAbonnement,
    ParametrePlateforme,
)
from apps.administration.registre import cle_connue, cles_publiques


class ParametresService:
    """Service d'accès et de modification des paramètres de la plateforme."""

    @classmethod
    def obtenir(cls, cle: str, a_date: datetime | None = None) -> int:
        """Récupère la valeur d'un paramètre à une date donnée (ou maintenant).

        Une valeur à date d'effet future n'est JAMAIS appliquée avant son
        échéance (prompt F-10 point 1) : on filtre toujours `date_effet ≤ date`.
        """
        defn = cle_connue(cle)
        if not defn:
            raise NonTrouve(
                code="parametre_inconnu",
                message=f"Paramètre inconnu : '{cle}'.",
                details={"cle": cle},
            )

        date_cible = a_date or maintenant()
        param = (
            ParametrePlateforme.objects.filter(cle=cle, date_effet__lte=date_cible)
            .order_by("-date_effet", "-cree_le")
            .first()
        )
        if param is not None:
            return param.valeur
        return defn.demarrage

    @classmethod
    def definir(
        cls,
        cle: str,
        valeur: int,
        date_effet: datetime | None = None,
        auteur: Any = None,
        motif: str | None = None,
        adresse_ip: str | None = None,
    ) -> ParametrePlateforme:
        """Définit une nouvelle valeur pour un paramètre après vérification des bornes."""
        defn = cle_connue(cle)
        if not defn:
            raise NonTrouve(
                code="parametre_inconnu",
                message=f"Paramètre inconnu : '{cle}'.",
                details={"cle": cle},
            )

        if not (defn.minimum <= valeur <= defn.maximum):
            raise ValidationMetier(
                code="valeur_hors_bornes",
                message=(
                    f"La valeur {valeur} pour le paramètre '{cle}' est hors bornes "
                    f"(min: {defn.minimum}, max: {defn.maximum})."
                ),
                details={
                    "cle": cle,
                    "valeur": valeur,
                    "minimum": defn.minimum,
                    "maximum": defn.maximum,
                },
            )

        date_application = date_effet or maintenant()

        with transaction.atomic():
            existant = (
                ParametrePlateforme.objects.filter(cle=cle)
                .order_by("-date_effet", "-cree_le")
                .first()
            )
            ancienne_val = existant.valeur if existant else defn.demarrage

            param = ParametrePlateforme.objects.create(
                cle=cle,
                valeur=valeur,
                date_effet=date_application,
                auteur=auteur,
                motif=motif,
            )

            # Audit systématique de la modification de paramètre (journal Q-16)
            AuditService.journaliser(
                action="modifier_parametre",
                entite_type="parametre_plateforme",
                entite_id=param.id,
                acteur=auteur,
                ancienne_valeur={"cle": cle, "valeur": ancienne_val},
                nouvelle_valeur={"cle": cle, "valeur": valeur, "motif": motif},
                adresse_ip=adresse_ip,
            )

        return param

    @staticmethod
    def historique(cle: str) -> list[ParametrePlateforme]:
        """Retourne l'historique des modifications d'un paramètre."""
        defn = cle_connue(cle)
        if not defn:
            raise NonTrouve(
                code="parametre_inconnu",
                message=f"Paramètre inconnu : '{cle}'.",
                details={"cle": cle},
            )
        return list(ParametrePlateforme.objects.filter(cle=cle).order_by("-date_effet", "-cree_le"))

    @staticmethod
    def en_vigueur(cle: str) -> ParametrePlateforme | None:
        """Ligne appliquée à maintenant (plus grande date_effet ≤ maintenant)."""
        return (
            ParametrePlateforme.objects.filter(cle=cle, date_effet__lte=maintenant())
            .order_by("-date_effet", "-cree_le")
            .first()
        )

    @staticmethod
    def planifiees(cle: str) -> list[ParametrePlateforme]:
        """Lignes à date d'effet future (non encore appliquées)."""
        return list(
            ParametrePlateforme.objects.filter(cle=cle, date_effet__gt=maintenant()).order_by(
                "date_effet"
            )
        )

    @classmethod
    def vue_admin(cls) -> list[dict[str, Any]]:
        """Pour ADM-04 : chaque clé avec sa valeur en vigueur, ses valeurs
        planifiées, ses bornes et son groupe (derived from registre).
        """
        from apps.administration.registre import REGISTRE

        resultats: list[dict[str, Any]] = []
        for cle, defn in REGISTRE.items():
            courante = cls.en_vigueur(cle)
            valeur_courante = courante.valeur if courante else defn.demarrage
            resultats.append(
                {
                    "cle": cle,
                    "groupe": cle.split(".", 1)[0],
                    "valeur": valeur_courante,
                    "minimum": defn.minimum,
                    "maximum": defn.maximum,
                    "public": defn.public,
                    "planifiees": [
                        {
                            "valeur": p.valeur,
                            "date_effet": p.date_effet,
                            "motif": p.motif,
                        }
                        for p in cls.planifiees(cle)
                    ],
                }
            )
        return resultats

    @classmethod
    def restaurer(
        cls,
        cle: str,
        acteur: Any,
        *,
        adresse_ip: str | None = None,
    ) -> ParametrePlateforme:
        """Restaure l'avant-dernière valeur en recréant une nouvelle ligne.

        « Restaurer une configuration = créer une nouvelle ligne avec l'ancienne
        valeur » (02 §2.8). La valeur restaurée redevient la plus récente.
        """
        lignes = cls.historique(cle)
        if len(lignes) < 2:
            raise ValidationMetier(
                code="restauration_impossible",
                message="Aucune valeur précédente à restaurer pour ce paramètre.",
                details={"cle": cle},
            )
        avant_derniere = lignes[1]
        return cls.definir(
            cle,
            avant_derniere.valeur,
            date_effet=None,
            auteur=acteur,
            motif="Restauration de la valeur précédente",
            adresse_ip=adresse_ip,
        )

    @classmethod
    def lister_publics(cls) -> dict[str, int]:
        """Retourne un dictionnaire {cle: valeur} de tous les paramètres publics."""
        resultats: dict[str, int] = {}
        for cle in cles_publiques():
            resultats[cle] = cls.obtenir(cle)
        return resultats


class AuditService:
    """Service centralisé de traçabilité et d'audit."""

    @staticmethod
    def journaliser(
        action: str,
        entite_type: str,
        entite_id: str | None = None,
        acteur: Any = None,
        ancienne_valeur: dict[str, Any] | None = None,
        nouvelle_valeur: dict[str, Any] | None = None,
        adresse_ip: str | None = None,
    ) -> JournalAudit:
        """Enregistre une entrée dans le journal d'audit."""
        return JournalAudit.objects.create(
            action=action,
            entite_type=entite_type,
            entite_id=entite_id,
            acteur=acteur,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=nouvelle_valeur,
            adresse_ip=adresse_ip,
        )

    @staticmethod
    def lister(
        *,
        acteur_id: str | None = None,
        entite_type: str | None = None,
        action: str | None = None,
        debut: datetime | None = None,
        fin: datetime | None = None,
    ) -> Any:
        """Queryset d'audit filtrable (acteur, entité, action, période)."""
        queryset = JournalAudit.objects.select_related("acteur").order_by("-cree_le")
        if acteur_id:
            queryset = queryset.filter(acteur_id=acteur_id)
        if entite_type:
            queryset = queryset.filter(entite_type=entite_type)
        if action:
            queryset = queryset.filter(action=action)
        if debut:
            queryset = queryset.filter(cree_le__gte=debut)
        if fin:
            queryset = queryset.filter(cree_le__lte=fin)
        return queryset

    @classmethod
    def exporter_csv(cls, queryset: Any) -> str:
        """Export CSV de l'audit (réservé super-admin). Aucune donnée sensible :"""
        import csv
        import io
        import json

        entete = [
            "id",
            "acteur",
            "action",
            "entite_type",
            "entite_id",
            "date",
            "ancienne_valeur",
            "nouvelle_valeur",
        ]
        tampon = io.StringIO()
        entameur = csv.writer(tampon, delimiter=";", lineterminator="\n")
        entameur.writerow(entete)
        for entree in queryset:
            entameur.writerow(
                [
                    entree.id,
                    entree.acteur.nom if entree.acteur else "",
                    entree.action,
                    entree.entite_type,
                    entree.entite_id or "",
                    entree.cree_le.isoformat(),
                    json.dumps(entree.ancienne_valeur, ensure_ascii=False)
                    if entree.ancienne_valeur
                    else "",
                    json.dumps(entree.nouvelle_valeur, ensure_ascii=False)
                    if entree.nouvelle_valeur
                    else "",
                ]
            )
        return tampon.getvalue()


class ServiceOffres:
    """Gestion versionnée des offres d'abonnement par le super-admin (P-05).

    Toute modification crée une nouvelle ligne (`date_effet`, `active`) : les
    abonnements déjà payés gardent leur version (non vendu en V1, voir D-04).
    """

    @staticmethod
    def en_vigueur(code: str) -> OffreAbonnement | None:
        return (
            OffreAbonnement.objects.filter(code=code, active=True, date_effet__lte=maintenant())
            .order_by("-date_effet")
            .first()
        )

    @classmethod
    def lister(cls) -> list[dict[str, Any]]:
        """Une entrée par code : version en vigueur + historique planifié."""
        codes = list(OffreAbonnement.objects.order_by("code").values_list("code", flat=True))
        vus: set[str] = set()
        resultats: list[dict[str, Any]] = []
        for code in codes:
            if code in vus:
                continue
            vus.add(code)
            courante = cls.en_vigueur(code)
            versions = list(
                OffreAbonnement.objects.filter(code=code).order_by("-date_effet")
            )
            resultats.append(
                {
                    "code": code,
                    "nom": courante.nom if courante else versions[0].nom,
                    "prix_mensuel_fcfa": courante.prix_mensuel_fcfa if courante else None,
                    "quota_planning_mois": courante.quota_planning_mois if courante else None,
                    "quota_recommandation_mois": (
                        courante.quota_recommandation_mois if courante else None
                    ),
                    "fonctionnalites": courante.fonctionnalites if courante else {},
                    "versions": [
                        {
                            "id": v.id,
                            "prix_mensuel_fcfa": v.prix_mensuel_fcfa,
                            "quota_planning_mois": v.quota_planning_mois,
                            "quota_recommandation_mois": v.quota_recommandation_mois,
                            "date_effet": v.date_effet,
                            "active": v.active,
                        }
                        for v in versions
                    ],
                }
            )
        return resultats

    @classmethod
    @transaction.atomic
    def nouvelle_version(
        cls,
        acteur: Any,
        *,
        code: str,
        nom: str,
        prix_mensuel_fcfa: int,
        quota_planning_mois: int,
        quota_recommandation_mois: int,
        fonctionnalites: dict[str, Any] | None = None,
        date_effet: datetime | None = None,
        adresse_ip: str | None = None,
    ) -> OffreAbonnement:
        """POST /admin/offres — enregistre une nouvelle version d'une offre."""
        if prix_mensuel_fcfa < 0:
            raise ValidationMetier(
                code="prix_invalide",
                message="Le prix mensuel ne peut pas être négatif.",
                details={"champ": "prix_mensuel_fcfa"},
            )
        if quota_planning_mois < 0 or quota_recommandation_mois < 0:
            raise ValidationMetier(
                code="quota_invalide",
                message="Les quotas mensuels ne peuvent pas être négatifs.",
            )
        existante = cls.en_vigueur(code)
        with transaction.atomic():
            OffreAbonnement.objects.filter(code=code, active=True).update(
                active=False, maj_le=maintenant()
            )
            offre = OffreAbonnement.objects.create(
                code=code,
                nom=nom,
                prix_mensuel_fcfa=prix_mensuel_fcfa,
                quota_planning_mois=quota_planning_mois,
                quota_recommandation_mois=quota_recommandation_mois,
                fonctionnalites=fonctionnalites or (existante.fonctionnalites if existante else {}),
                date_effet=date_effet or maintenant(),
                active=True,
                cree_par=acteur,
            )
        AuditService.journaliser(
            action="modifier_offre",
            entite_type="offre_abonnement",
            entite_id=offre.id,
            acteur=acteur,
            ancienne_valeur=(
                {
                    "prix_mensuel_fcfa": existante.prix_mensuel_fcfa,
                    "quota_planning_mois": existante.quota_planning_mois,
                    "quota_recommandation_mois": existante.quota_recommandation_mois,
                }
                if existante
                else None
            ),
            nouvelle_valeur={
                "code": code,
                "prix_mensuel_fcfa": prix_mensuel_fcfa,
                "quota_planning_mois": quota_planning_mois,
                "quota_recommandation_mois": quota_recommandation_mois,
            },
            adresse_ip=adresse_ip,
        )
        return offre


# Alias d'instance accessibles depuis les vues (même style que parametres/audit).
offres = ServiceOffres()


class HistoriqueService:
    """Service d'enregistrement des transitions d'états métier."""

    @staticmethod
    def enregistrer_transition(
        entite_type: str,
        entite_id: str,
        ancien: str | None,
        nouveau: str,
        acteur: Any = None,
        motif: str | None = None,
    ) -> HistoriqueStatut:
        """Enregistre une transition d'état dans l'historique."""
        return HistoriqueStatut.objects.create(
            entite_type=entite_type,
            entite_id=entite_id,
            ancien_statut=ancien,
            nouveau_statut=nouveau,
            acteur=acteur,
            motif=motif,
        )


# Instances singleton accessibles facilement
parametres = ParametresService()
audit = AuditService()
historique = HistoriqueService()

"""Services transverses pour les paramètres, l'audit et l'historique des statuts.

Conventions techniques §5 et prompt F-00 point 15.
"""

from datetime import datetime
from typing import Any

from commun.exceptions import NonTrouve, ValidationMetier
from commun.horloge import maintenant
from django.db import transaction

from apps.administration.models import HistoriqueStatut, JournalAudit, ParametrePlateforme
from apps.administration.registre import cle_connue, cles_publiques


class ParametresService:
    """Service d'accès et de modification des paramètres de la plateforme."""

    @staticmethod
    def obtenir(cle: str, a_date: datetime | None = None) -> int:
        """Récupère la valeur d'un paramètre à une date donnée (ou maintenant)."""
        defn = cle_connue(cle)
        if not defn:
            raise NonTrouve(
                code="parametre_inconnu",
                message=f"Paramètre inconnu : '{cle}'.",
                details={"cle": cle},
            )

        qs = ParametrePlateforme.objects.filter(cle=cle)
        if a_date is not None:
            qs = qs.filter(date_effet__lte=a_date)

        param = qs.order_by("-date_effet", "-cree_le").first()
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

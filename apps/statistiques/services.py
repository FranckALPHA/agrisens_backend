"""Logique métier des statistiques (SPEC-11, F-14).

Toute l'agrégation vit ici : les vues DRF se contentent de vérifier l'accès
(rôle) et d'appeler le service. Les calculs reposent sur les **données
confirmées** (paiements réussis / commandes effectivement livrées, accompagnements
terminés) pour rester alignés sur la définition des gains du service `soldes`
(Q-11) : un agriculteur ne compte un chiffre d'affaires que lorsque la marchandise
a été livrée ; de même un livreur compte ses frais sur les courses livrées et un
ingénieur ses honoraires sur les accompagnements terminés.

Argent en entiers FCFA, quantités en Decimal (conventions §2). Filtre de période
`du` / `au` (inclusives, dates ISO) converti en bornes UTC explicites : on compare
`cree_le` (stocké en UTC) à des datetimes UTC pour éviter tout `CONVERT_TZ` MySQL
dont les tables de fuseaux ne sont pas garanties chargées (hypothèse journal Q-32).
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from commun.exceptions import ValidationMetier
from django.db.models import Count, Q, Sum

from apps.accompagnements.models import Accompagnement, StatutAccompagnement
from apps.commandes.models import Commande, LigneCommande, StatutCommande
from apps.litiges.models import Remboursement, StatutRemboursement
from apps.livraisons.models import Livraison, StatutLivraison
from apps.plannings.models import GenerationIA
from apps.utilisateurs.models import Utilisateur

# Statuts de commande où la marchandise a été livrée (donc vendue/gagnée) :
# même ensemble que ServiceSoldes._STATUTS_LIVRES (Q-11) — un remboursement en
# cours ou accordé ne « dé-commande » pas la vente déjà comptabilisée.
STATUTS_VENDUES: tuple[str, ...] = (
    StatutCommande.LIVREE,
    StatutCommande.REMBOURSEMENT_EN_ATTENTE,
    StatutCommande.REMBOURSEE,
)

GRANULARITES = frozenset({"jour", "semaine", "mois"})


def normaliser_granularite(valeur: str | None) -> str:
    return valeur if valeur in GRANULARITES else "mois"


def _bornes_periode(du: str | None, au: str | None) -> tuple[datetime | None, datetime | None]:
    """Convertit `du`/`au` (dates ISO) en bornes UTC : [debut, fin) exclusif à la fin."""
    debut = _date_debut_utc(du, "du")
    fin = None
    if au:
        fin_jour = _parse_date(au, "au")
        fin = datetime(fin_jour.year, fin_jour.month, fin_jour.day, tzinfo=UTC) + timedelta(days=1)
    return debut, fin


def _parse_date(valeur: str, champ: str) -> date:
    try:
        return date.fromisoformat(valeur)
    except ValueError as exc:
        raise ValidationMetier(
            code="periode_invalide",
            message=f"Le paramètre « {champ} » doit être une date au format AAAA-MM-JJ.",
            details={champ: valeur},
        ) from exc


def _date_debut_utc(valeur: str | None, champ: str) -> datetime | None:
    if not valeur:
        return None
    jour = _parse_date(valeur, champ)
    return datetime(jour.year, jour.month, jour.day, tzinfo=UTC)


def _q_periode(debut: datetime | None, fin: datetime | None, champ: str = "cree_le") -> Q:
    q = Q()
    prefix = f"{champ}__"
    if debut is not None:
        q &= Q(**{f"{prefix}gte": debut})
    if fin is not None:
        q &= Q(**{f"{prefix}lt": fin})
    return q


def _cle_periode(moment: datetime, granularite: str) -> str:
    """Clé de tri ISO d'un instant UTC selon la granularité demandée."""
    jour = moment.astimezone(UTC).date()
    if granularite == "jour":
        return jour.isoformat()
    if granularite == "semaine":
        isosemaine = jour.isocalendar()
        return f"{isosemaine.year}-S{isosemaine.week:02d}"
    return f"{jour.year:04d}-{jour.month:02d}"


def _series(lignes: list[tuple[datetime, int]], granularite: str) -> list[dict[str, Any]]:
    """Agrège (instant, montant) en points {periode, nombre, montant_fcfa} triés."""
    cumul: dict[str, dict[str, Any]] = defaultdict(lambda: {"nombre": 0, "montant_fcfa": 0})
    for moment, montant in lignes:
        cle = _cle_periode(moment, granularite)
        cumul[cle]["nombre"] += 1
        cumul[cle]["montant_fcfa"] += int(montant or 0)
    return [
        {"periode": cle, "nombre": cumul[cle]["nombre"], "montant_fcfa": cumul[cle]["montant_fcfa"]}
        for cle in sorted(cumul)
    ]


def _count_par_statut(queryset: Any, champ: str = "statut") -> dict[str, int]:
    compte = queryset.values(champ).annotate(nombre=Count("id"))
    return {ligne[champ]: ligne["nombre"] for ligne in compte}


def _periode(du: str | None, au: str | None) -> dict[str, Any]:
    return {"du": du, "au": au}


class ServiceStatistiques:
    """Agrégats par rôle et tableau de bord plateforme (SPEC-11)."""

    @staticmethod
    def mes_ventes(
        agriculteur: Utilisateur, *, du: str | None, au: str | None, granularite: str
    ) -> dict[str, Any]:
        """Ventes d'un agriculteur : CA, commissions, revenu net, quantités,
        produits les plus vendus et série temporelle (commandes livrées)."""
        debut, fin = _bornes_periode(du, au)
        commandes = Commande.objects.filter(
            Q(agriculteur=agriculteur, statut__in=STATUTS_VENDUES) & _q_periode(debut, fin)
        )
        agg = commandes.aggregate(
            ca=Sum("sous_total_fcfa", default=0),
            commissions=Sum("commission_fcfa", default=0),
            nombre=Count("id"),
        )
        ca = int(agg["ca"] or 0)
        commissions = int(agg["commissions"] or 0)

        lignes = LigneCommande.objects.filter(commande__in=commandes)
        total_lignes = lignes.aggregate(total=Sum("quantite", default=Decimal("0")))["total"]
        quantite = total_lignes or Decimal("0")
        produits = (
            lignes.values("nom_produit")
            .annotate(unites=Sum("quantite"), total=Sum("total_ligne_fcfa"))
            .order_by("-total")[:5]
        )
        produits_populaires: list[dict[str, Any]] = [
            {
                "nom_produit": ligne["nom_produit"],
                "unites": float(ligne["unites"] or 0),
                "total_fcfa": int(ligne["total"] or 0),
            }
            for ligne in produits
        ]

        serie = _series(list(commandes.values_list("cree_le", "sous_total_fcfa")), granularite)
        return {
            "periode": _periode(du, au),
            "nombre_ventes": int(agg["nombre"] or 0),
            "chiffre_affaires_brut_fcfa": ca,
            "commissions_fcfa": commissions,
            "revenu_net_fcfa": ca - commissions,
            "quantite_totale": float(quantite),
            "produits_populaires": produits_populaires,
            "serie": serie,
        }

    @staticmethod
    def mes_livraisons(
        livreur: Utilisateur, *, du: str | None, au: str | None, granularite: str
    ) -> dict[str, Any]:
        """Livraisons d'un livreur : total, répartition par statut, frais cumulés
        sur les courses livrées et série temporelle."""
        debut, fin = _bornes_periode(du, au)
        base = Livraison.objects.filter(Q(livreur=livreur) & _q_periode(debut, fin))
        agg = base.aggregate(nombre=Count("id"))
        livrees = base.filter(statut=StatutLivraison.LIVREE)
        frais = livrees.aggregate(total=Sum("commande__frais_livraison_fcfa", default=0))["total"]
        serie = _series(
            [
                (moment, int(frais_livraison or 0))
                for moment, frais_livraison in base.values_list(
                    "cree_le", "commande__frais_livraison_fcfa"
                )
            ],
            granularite,
        )
        return {
            "periode": _periode(du, au),
            "nombre_livraisons": int(agg["nombre"] or 0),
            "livraisons_par_statut": _count_par_statut(base),
            "livrees": livrees.count(),
            "frais_cumules_fcfa": int(frais or 0),
            "serie": serie,
        }

    @staticmethod
    def mes_accompagnements(
        ingenieur: Utilisateur, *, du: str | None, au: str | None, granularite: str
    ) -> dict[str, Any]:
        """Accompagnements d'un ingénieur : total, répartition par statut, revenu
        net des missions terminées (consultation − commission + déplacement) et
        série temporelle."""
        debut, fin = _bornes_periode(du, au)
        base = Accompagnement.objects.filter(Q(ingenieur=ingenieur) & _q_periode(debut, fin))
        agg = base.aggregate(nombre=Count("id"))
        terminees = base.filter(statut=StatutAccompagnement.TERMINEE)
        cumul = terminees.aggregate(
            consultation=Sum("prix_consultation_fcfa", default=0),
            deplacement=Sum("frais_deplacement_fcfa", default=0),
            commission=Sum("commission_consultation_fcfa", default=0),
        )
        revenu = (
            int(cumul["consultation"] or 0)
            + int(cumul["deplacement"] or 0)
            - int(cumul["commission"] or 0)
        )

        serie_points: list[tuple[datetime, int]] = []
        for moment, consultation, deplacement, commission in terminees.values_list(
            "cree_le",
            "prix_consultation_fcfa",
            "frais_deplacement_fcfa",
            "commission_consultation_fcfa",
        ):
            montant = int(consultation or 0) + int(deplacement or 0) - int(commission or 0)
            serie_points.append((moment, montant))
        return {
            "periode": _periode(du, au),
            "nombre_accompagnements": int(agg["nombre"] or 0),
            "accompagnements_par_statut": _count_par_statut(base),
            "terminees": terminees.count(),
            "revenu_net_fcfa": revenu,
            "serie": _series(serie_points, granularite),
        }

    @staticmethod
    def plateforme(*, du: str | None, au: str | None, granularite: str) -> dict[str, Any]:
        """Statistiques globales de la plateforme (admin / super-admin).

        Volumes et revenus calculés sur les données confirmées : commandes
        livrées pour le chiffre d'affaires et les commissions ; remboursements
        « effectués » pour les sommes restituées ; générations IA par statut.
        """
        debut, fin = _bornes_periode(du, au)

        utilisateurs_par_role = {
            ligne["role"]: ligne["nombre"]
            for ligne in Utilisateur.objects.filter(_q_periode(debut, fin))
            .values("role")
            .annotate(nombre=Count("id"))
        }

        commandes = Commande.objects.filter(_q_periode(debut, fin))
        commandes_par_statut = _count_par_statut(commandes)
        vendues = commandes.filter(statut__in=STATUTS_VENDUES)
        agg_cmd = vendues.aggregate(
            ca=Sum("sous_total_fcfa", default=0),
            commissions=Sum("commission_fcfa", default=0),
        )
        commissions_commandes = int(agg_cmd["commissions"] or 0)

        accompagnements = Accompagnement.objects.filter(_q_periode(debut, fin))
        agg_acc = accompagnements.filter(statut=StatutAccompagnement.TERMINEE).aggregate(
            commissions=Sum("commission_consultation_fcfa", default=0)
        )
        commissions_accompagnements = int(agg_acc["commissions"] or 0)

        remboursements = Remboursement.objects.filter(_q_periode(debut, fin))
        agg_rem = remboursements.filter(statut=StatutRemboursement.EFFECTUE).aggregate(
            total=Sum("montant_accorde_fcfa", default=0)
        )

        livraisons = Livraison.objects.filter(_q_periode(debut, fin))
        generations = GenerationIA.objects.filter(_q_periode(debut, fin))

        serie = _series(list(commandes.values_list("cree_le", "sous_total_fcfa")), granularite)
        return {
            "periode": _periode(du, au),
            "utilisateurs_par_role": utilisateurs_par_role,
            "total_utilisateurs": sum(utilisateurs_par_role.values()),
            "commandes_par_statut": commandes_par_statut,
            "total_commandes": commandes.count(),
            "chiffre_affaires_fcfa": int(agg_cmd["ca"] or 0),
            "remboursements": {
                "nombre": remboursements.count(),
                "montant_effectue_fcfa": int(agg_rem["total"] or 0),
            },
            "livraisons_par_statut": _count_par_statut(livraisons),
            "total_livraisons": livraisons.count(),
            "accompagnements_par_statut": _count_par_statut(accompagnements),
            "total_accompagnements": accompagnements.count(),
            "generations_ia_par_statut": _count_par_statut(generations),
            "total_generations_ia": generations.count(),
            "revenus_plateforme_fcfa": commissions_commandes + commissions_accompagnements,
            "serie": serie,
        }


__all__ = [
    "GRANULARITES",
    "STATUTS_VENDUES",
    "ServiceStatistiques",
    "normaliser_granularite",
]

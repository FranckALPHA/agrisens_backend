"""Vues API F-10 : paramètres, offres, audit et référentiels (SPEC-11)."""

from datetime import datetime

from commun.exceptions import ErreurMetier, NonTrouve
from commun.pagination import reponse_liste, schema_enveloppe_liste
from commun.permissions import ARole
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration.serializers_f10 import (
    AuditEntreeSerializer,
    OffreAdminSerializer,
    OffreCreationSerializer,
    ParametreAdminSerializer,
    ParametreCreationSerializer,
    ParametreHistoriqueSerializer,
)
from apps.administration.services import audit, offres, parametres
from apps.administration.services_admin import ROLES_ADMIN

from .views import _adresse_ip, _porteur

# Enveloppe paginée documentée pour GET /admin/audit.
LISTE_AUDIT = schema_enveloppe_liste("ListeAudit", AuditEntreeSerializer)

_CLE_PATTERN = r"[a-z_]+(?:\.[a-z_0-9]+)+"


def _cle_valide(cle: str) -> None:
    """404 si la clé n'appartient pas au registre (evite 500 côté service)."""
    from apps.administration.registre import cle_connue

    if cle_connue(cle) is None:
        raise NonTrouve(
            code="parametre_inconnu",
            message=f"Paramètre inconnu : '{cle}'.",
            details={"cle": cle},
        )


def _parse_date(param: str | None) -> datetime | None:
    if not param:
        return None
    try:
        return datetime.fromisoformat(param.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NonTrouve(
            code="date_invalide",
            message=f"Date invalide : '{param}' (format ISO 8601 attendu).",
            details={"valeur": param},
        ) from exc


class VueAdminParametres(APIView):
    """GET /admin/parametres — valeurs en vigueur + planifiées (adm lecture)."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Lister les paramètres (admin)",
        description=(
            "Chaque paramètre du registre avec sa valeur en vigueur, ses bornes "
            "et ses valeurs planifiées. Lecture seule pour l'admin."
        ),
        responses={200: ParametreAdminSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        return Response(ParametreAdminSerializer(parametres.vue_admin(), many=True).data)


class VueAdminParametreCle(APIView):
    """POST /admin/parametres/{cle} — nouvelle valeur (sadm seul)."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Définir une nouvelle valeur de paramètre",
        description=(
            "Bornes vérifiées, motif obligatoire, date d'effet immédiate ou "
            "future. Chaque changement crée une nouvelle ligne (02 §2.8) et "
            "une entrée d'audit."
        ),
        parameters=[OpenApiParameter("cle", str, description="Clé du paramètre (groupe.nom)")],
        request=ParametreCreationSerializer,
        responses={
            201: ParametreHistoriqueSerializer,
            403: OpenApiResponse(description="permission_refusee"),
            404: OpenApiResponse(description="parametre_inconnu"),
            422: OpenApiResponse(description="valeur_hors_bornes"),
        },
    )
    def post(self, request: Request, cle: str) -> Response:
        _cle_valide(cle)
        serializer = ParametreCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        ligne = parametres.definir(
            cle,
            donnees["valeur"],
            date_effet=donnees.get("date_effet"),
            auteur=_porteur(request),
            motif=donnees["motif"],
            adresse_ip=_adresse_ip(request),
        )
        return Response(
            ParametreHistoriqueSerializer(
                {
                    "id": ligne.id,
                    "valeur": ligne.valeur,
                    "date_effet": ligne.date_effet,
                    "motif": ligne.motif,
                    "auteur_nom": ligne.auteur.nom if ligne.auteur else None,
                    "cree_le": ligne.cree_le,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class VueAdminParametreHistorique(APIView):
    """GET /admin/parametres/{cle}/historique — toutes les lignes (sadm)."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Historique d'un paramètre",
        parameters=[OpenApiParameter("cle", str, description="Clé du paramètre")],
        responses={
            200: ParametreHistoriqueSerializer(many=True),
            404: OpenApiResponse(description="parametre_inconnu"),
        },
    )
    def get(self, request: Request, cle: str) -> Response:
        lignes = parametres.historique(cle)
        donnees = [
            {
                "id": ligne.id,
                "valeur": ligne.valeur,
                "date_effet": ligne.date_effet,
                "motif": ligne.motif,
                "auteur_nom": ligne.auteur.nom if ligne.auteur else None,
                "cree_le": ligne.cree_le,
            }
            for ligne in lignes
        ]
        return Response(ParametreHistoriqueSerializer(donnees, many=True).data)


class VueAdminParametreRestaurer(APIView):
    """POST /admin/parametres/{cle}/restaurer — recrée l'avant-dernière valeur."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Restaurer la valeur précédente d'un paramètre",
        description="Restaurer = créer une nouvelle ligne avec l'ancienne valeur (02 §2.8).",
        parameters=[OpenApiParameter("cle", str, description="Clé du paramètre")],
        request=None,
        responses={
            201: ParametreHistoriqueSerializer,
            404: OpenApiResponse(description="parametre_inconnu"),
            422: OpenApiResponse(description="restauration_impossible"),
        },
    )
    def post(self, request: Request, cle: str) -> Response:
        _cle_valide(cle)
        ligne = parametres.restaurer(
            cle, _porteur(request), adresse_ip=_adresse_ip(request)
        )
        return Response(
            ParametreHistoriqueSerializer(
                {
                    "id": ligne.id,
                    "valeur": ligne.valeur,
                    "date_effet": ligne.date_effet,
                    "motif": ligne.motif,
                    "auteur_nom": ligne.auteur.nom if ligne.auteur else None,
                    "cree_le": ligne.cree_le,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class VueAdminOffres(APIView):
    """GET /admin/offres (adm lecture) ; POST (sadm) — nouvelle version d'offre."""

    permission_classes = [ARole(*ROLES_ADMIN)]

    @extend_schema(
        summary="Lister les offres d'abonnement versionnées",
        responses={200: OffreAdminSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        return Response(OffreAdminSerializer(offres.lister(), many=True).data)

    @extend_schema(
        summary="Enregistrer une nouvelle version d'offre",
        description=(
            "Réservé au super-admin. Les versions précédentes restent en base ; "
            "la nouvelle version s'applique aux nouveaux abonnements (P-05)."
        ),
        request=OffreCreationSerializer,
        responses={
            201: OffreAdminSerializer,
            403: OpenApiResponse(description="permission_refusee"),
            422: OpenApiResponse(description="prix_invalide | quota_invalide"),
        },
    )
    def post(self, request: Request) -> Response:
        if not _porteur(request).is_superuser:
            raise ErreurMetier(
                code="permission_refusee",
                message="Seul le super-admin modifie les offres.",
                statut_http=403,
            )
        serializer = OffreCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        offre = offres.nouvelle_version(
            _porteur(request),
            code=donnees["code"],
            nom=donnees["nom"],
            prix_mensuel_fcfa=donnees["prix_mensuel_fcfa"],
            quota_planning_mois=donnees["quota_planning_mois"],
            quota_recommandation_mois=donnees["quota_recommandation_mois"],
            fonctionnalites=donnees.get("fonctionnalites"),
            date_effet=donnees.get("date_effet"),
            adresse_ip=_adresse_ip(request),
        )
        courante = {
            "code": offre.code,
            "nom": offre.nom,
            "prix_mensuel_fcfa": offre.prix_mensuel_fcfa,
            "quota_planning_mois": offre.quota_planning_mois,
            "quota_recommandation_mois": offre.quota_recommandation_mois,
            "fonctionnalites": offre.fonctionnalites,
            "versions": [
                {
                    "id": offre.id,
                    "prix_mensuel_fcfa": offre.prix_mensuel_fcfa,
                    "quota_planning_mois": offre.quota_planning_mois,
                    "quota_recommandation_mois": offre.quota_recommandation_mois,
                    "date_effet": offre.date_effet,
                    "active": offre.active,
                }
            ],
        }
        return Response(
            OffreAdminSerializer(courante).data, status=status.HTTP_201_CREATED
        )


class VueAdminAudit(APIView):
    """GET /admin/audit — journal filtrable, réservé super-admin."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Consulter le journal d'audit",
        description="Filtres : acteur, entité, action, période (debut/fin ISO).",
        parameters=[
            OpenApiParameter("acteur", str, description="Identifiant de l'acteur"),
            OpenApiParameter("entite_type", str, description="Type d'entité auditée"),
            OpenApiParameter("action", str, description="Action journalisée"),
            OpenApiParameter("debut", str, description="Date de début (ISO 8601)"),
            OpenApiParameter("fin", str, description="Date de fin (ISO 8601)"),
            OpenApiParameter("page", int, description="Page de résultats (défaut 1)"),
            OpenApiParameter("taille", int, description="Taille de page, 1–100 (défaut 20)"),
        ],
        responses={200: LISTE_AUDIT},
    )
    def get(self, request: Request) -> Response:
        queryset = audit.lister(
            acteur_id=request.query_params.get("acteur") or None,
            entite_type=request.query_params.get("entite_type") or None,
            action=request.query_params.get("action") or None,
            debut=_parse_date(request.query_params.get("debut")),
            fin=_parse_date(request.query_params.get("fin")),
        )
        return reponse_liste(request, queryset, AuditEntreeSerializer)


class VueAdminAuditExport(APIView):
    """GET /admin/audit/export — CSV complet, réservé super-admin."""

    permission_classes = [ARole("super_admin")]

    @extend_schema(
        summary="Exporter l'audit en CSV",
        description="Mêmes filtres que la liste ; export sans pagination.",
        parameters=[
            OpenApiParameter("acteur", str),
            OpenApiParameter("entite_type", str),
            OpenApiParameter("action", str),
            OpenApiParameter("debut", str),
            OpenApiParameter("fin", str),
        ],
        responses={(200, "text/csv"): OpenApiResponse(description="Fichier CSV")},
    )
    def get(self, request: Request) -> HttpResponse:
        queryset = audit.lister(
            acteur_id=request.query_params.get("acteur") or None,
            entite_type=request.query_params.get("entite_type") or None,
            action=request.query_params.get("action") or None,
            debut=_parse_date(request.query_params.get("debut")),
            fin=_parse_date(request.query_params.get("fin")),
        )
        contenu = audit.exporter_csv(queryset)
        reponse = HttpResponse(contenu, content_type="text/csv; charset=utf-8")
        reponse["Content-Disposition"] = 'attachment; filename="audit-agrisens.csv"'
        return reponse

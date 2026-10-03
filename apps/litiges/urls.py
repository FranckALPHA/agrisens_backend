from django.urls import re_path

from apps.litiges.views import (
    VueAdminRemboursements,
    VueAdminSignalements,
    VueAdminSignalementTraiter,
    VueCommandeEvaluation,
    VueMesRemboursements,
    VueMesSignalements,
    VueRemboursementApprouver,
    VueRemboursementExaminer,
    VueRemboursementExecuter,
    VueRemboursementRefuser,
    VueRemboursements,
    VueSignalements,
)

urlpatterns = [
    # SPEC-05 — remboursements (acheteur)
    re_path(r"^remboursements/?$", VueRemboursements.as_view(), name="remboursements_creer"),
    re_path(
        r"^remboursements/mes-demandes/?$",
        VueMesRemboursements.as_view(),
        name="mes_remboursements",
    ),
    # SPEC-05 — remboursements (administration, exécution manuelle Q-26)
    re_path(
        r"^admin/remboursements/?$",
        VueAdminRemboursements.as_view(),
        name="admin_remboursements",
    ),
    re_path(
        r"^admin/remboursements/(?P<identifiant>[0-9a-f-]+)/examiner/?$",
        VueRemboursementExaminer.as_view(),
        name="admin_remboursement_examiner",
    ),
    re_path(
        r"^admin/remboursements/(?P<identifiant>[0-9a-f-]+)/approuver/?$",
        VueRemboursementApprouver.as_view(),
        name="admin_remboursement_approuver",
    ),
    re_path(
        r"^admin/remboursements/(?P<identifiant>[0-9a-f-]+)/refuser/?$",
        VueRemboursementRefuser.as_view(),
        name="admin_remboursement_refuser",
    ),
    re_path(
        r"^admin/remboursements/(?P<identifiant>[0-9a-f-]+)/executer/?$",
        VueRemboursementExecuter.as_view(),
        name="admin_remboursement_executer",
    ),
    # SPEC-10 — signalements
    re_path(r"^signalements/?$", VueSignalements.as_view(), name="signalements_creer"),
    re_path(
        r"^signalements/mes-signalements/?$",
        VueMesSignalements.as_view(),
        name="mes_signalements",
    ),
    re_path(
        r"^admin/signalements/?$",
        VueAdminSignalements.as_view(),
        name="admin_signalements",
    ),
    re_path(
        r"^admin/signalements/(?P<identifiant>[0-9a-f-]+)/traiter/?$",
        VueAdminSignalementTraiter.as_view(),
        name="admin_signalement_traiter",
    ),
    # F-09 — évaluation d'une commande livrée (02 §2.3)
    re_path(
        r"^commandes/(?P<identifiant>[0-9a-f-]+)/evaluation/?$",
        VueCommandeEvaluation.as_view(),
        name="commande_evaluation",
    ),
]

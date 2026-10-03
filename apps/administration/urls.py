"""Routes pour le module administration (paramètres + gestion des utilisateurs F-03)."""

from django.urls import re_path

from apps.administration.views import (
    VueCompteursAdmin,
    VueDemandesReinitialisation,
    VueDetailUtilisateurAdmin,
    VueListeUtilisateursAdmin,
    VueParametresPublics,
    VueReactiverUtilisateur,
    VueReinitialiserMotDePasse,
    VueSuspendreUtilisateur,
)
from apps.administration.views_f10 import (
    VueAdminAudit,
    VueAdminAuditExport,
    VueAdminOffres,
    VueAdminParametreCle,
    VueAdminParametreHistorique,
    VueAdminParametreRestaurer,
    VueAdminParametres,
)

app_name = "administration"

urlpatterns = [
    re_path(
        r"^parametres/publics/?$",
        VueParametresPublics.as_view(),
        name="parametres_publics",
    ),
    # Gestion des utilisateurs (SPEC-11, prompt F-03 point 4)
    re_path(
        r"^admin/utilisateurs/compteurs/?$",
        VueCompteursAdmin.as_view(),
        name="admin_utilisateurs_compteurs",
    ),
    re_path(
        r"^admin/utilisateurs/demandes-reinitialisation/?$",
        VueDemandesReinitialisation.as_view(),
        name="admin_demandes_reinitialisation",
    ),
    re_path(
        r"^admin/utilisateurs/?$",
        VueListeUtilisateursAdmin.as_view(),
        name="admin_utilisateurs_liste",
    ),
    re_path(
        r"^admin/utilisateurs/(?P<id>[0-9a-f-]{36})/?$",
        VueDetailUtilisateurAdmin.as_view(),
        name="admin_utilisateurs_detail",
    ),
    re_path(
        r"^admin/utilisateurs/(?P<id>[0-9a-f-]{36})/suspendre/?$",
        VueSuspendreUtilisateur.as_view(),
        name="admin_utilisateurs_suspendre",
    ),
    re_path(
        r"^admin/utilisateurs/(?P<id>[0-9a-f-]{36})/reactiver/?$",
        VueReactiverUtilisateur.as_view(),
        name="admin_utilisateurs_reactiver",
    ),
    re_path(
        r"^admin/utilisateurs/(?P<id>[0-9a-f-]{36})/reinitialiser-mot-de-passe/?$",
        VueReinitialiserMotDePasse.as_view(),
        name="admin_utilisateurs_reset_mdp",
    ),
    # Paramètres (SPEC-11, prompt F-10 point 1) — sous-routes avant la clé générique
    re_path(
        r"^admin/parametres/(?P<cle>[a-z_]+(?:\.[a-z_0-9]+)+)/historique/?$",
        VueAdminParametreHistorique.as_view(),
        name="admin_parametres_historique",
    ),
    re_path(
        r"^admin/parametres/(?P<cle>[a-z_]+(?:\.[a-z_0-9]+)+)/restaurer/?$",
        VueAdminParametreRestaurer.as_view(),
        name="admin_parametres_restaurer",
    ),
    re_path(
        r"^admin/parametres/(?P<cle>[a-z_]+(?:\.[a-z_0-9]+)+)/?$",
        VueAdminParametreCle.as_view(),
        name="admin_parametres_cle",
    ),
    re_path(
        r"^admin/parametres/?$",
        VueAdminParametres.as_view(),
        name="admin_parametres_liste",
    ),
    # Offres d'abonnement versionnées (F-10 point 2)
    re_path(r"^admin/offres/?$", VueAdminOffres.as_view(), name="admin_offres"),
    # Audit (F-10 point 4) — export avant la liste simple
    re_path(r"^admin/audit/export/?$", VueAdminAuditExport.as_view(), name="admin_audit_export"),
    re_path(r"^admin/audit/?$", VueAdminAudit.as_view(), name="admin_audit"),
]

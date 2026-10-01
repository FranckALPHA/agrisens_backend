"""Tests unitaires des services de profils (prompt F-03 backend point 6).

Règles vérifiées au niveau service : tarif et rayon bornés par les
paramètres, spécialités/régions du référentiel, disponibilité exigeant
un profil complet et validé, décision admin (notifie + journalise),
position obligatoire pour un livreur disponible.
"""

from typing import Any

import pytest
from apps.administration.models import JournalAudit
from apps.notifications.models import Notification
from apps.profils.models import IngenieurProfil, LivreurProfil, StatutValidation
from apps.profils.services import ServiceProfilIngenieur, ServiceProfilLivreur
from apps.utilisateurs.models import RoleUtilisateur, StatutCompte
from commun.exceptions import NonTrouve, TransitionInvalide, ValidationMetier

POINT_DOUALA = {"type": "Point", "coordinates": [9.7679, 4.0483]}


@pytest.mark.unit
class TestServiceProfilIngenieur:
    def test_tarif_hors_bornes_refuse(self, db: Any, fabrique_utilisateur: Any) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        with pytest.raises(ValidationMetier) as exc:
            # 100 < prix_min (2 000, valeur de démarrage du paramètre) : bornes
            # lues dans PARAMETRE_PLATEFORME, jamais en dur.
            ServiceProfilIngenieur.modifier_profil(nadia, {"tarif_consultation_fcfa": 100})
        assert exc.value.code == "tarif_hors_bornes"
        assert exc.value.details["minimum"] == 2_000

    def test_specialite_inconnue_refusee(self, db: Any, fabrique_utilisateur: Any) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        with pytest.raises(ValidationMetier) as exc:
            ServiceProfilIngenieur.modifier_profil(nadia, {"specialites": ["astrologie"]})
        assert exc.value.code == "specialite_inconnue"

    def test_profil_valide_enregistre_specialites_et_zone(
        self, db: Any, fabrique_utilisateur: Any
    ) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        profil = ServiceProfilIngenieur.modifier_profil(
            nadia,
            {
                "specialites": ["vivriers", "maraichage"],
                "region": "littoral",
                "zone_centre": POINT_DOUALA,
                "zone_rayon_km": 40,
                "tarif_consultation_fcfa": 10_000,
            },
        )
        assert profil.specialites == ["maraichage", "vivriers"]  # trié, canonique
        assert profil.zone_rayon_km == 40
        assert profil.statut_validation == StatutValidation.EN_ATTENTE

    def test_disponibilite_exige_profil_complet_et_valide(
        self, db: Any, fabrique_utilisateur: Any
    ) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        with pytest.raises(ValidationMetier) as exc:
            ServiceProfilIngenieur.modifier_profil(nadia, {"disponible": True})
        assert exc.value.code == "profil_incomplet"
        assert "validation_admin" in exc.value.details["champs_manquants"]

    def test_valider_notifie_journalise_et_act_le_compte(
        self, db: Any, fabrique_utilisateur: Any
    ) -> None:
        nadia = fabrique_utilisateur(
            role=RoleUtilisateur.INGENIEUR_AGRONOME.value,
            statut_compte=StatutCompte.EN_ATTENTE_VALIDATION,
        )
        IngenieurProfil.objects.create(utilisateur=nadia)
        mireille = fabrique_utilisateur(role=RoleUtilisateur.ADMIN.value)
        profil = ServiceProfilIngenieur.valider(mireille, str(nadia.id))
        nadia.refresh_from_db()
        assert profil.statut_validation == StatutValidation.VALIDE
        assert profil.valide_par_id == mireille.id
        assert nadia.statut_compte == StatutCompte.ACTIF
        assert Notification.objects.filter(destinataire=nadia, type="administration").count() == 1
        assert JournalAudit.objects.filter(action="ingenieur_valide").count() == 1

    def test_refuser_sans_motif_impossible(self, db: Any, fabrique_utilisateur: Any) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        IngenieurProfil.objects.create(utilisateur=nadia)
        mireille = fabrique_utilisateur(role=RoleUtilisateur.ADMIN.value)
        with pytest.raises(ValidationMetier) as exc:
            ServiceProfilIngenieur.refuser(mireille, str(nadia.id), "   ")
        assert exc.value.code == "motif_obligatoire"

    def test_double_decision_est_une_transition_invalide(
        self, db: Any, fabrique_utilisateur: Any
    ) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        IngenieurProfil.objects.create(utilisateur=nadia)
        mireille = fabrique_utilisateur(role=RoleUtilisateur.ADMIN.value)
        ServiceProfilIngenieur.valider(mireille, str(nadia.id))
        with pytest.raises(TransitionInvalide):
            ServiceProfilIngenieur.valider(mireille, str(nadia.id))

    def test_decision_par_un_non_admin_refusee(self, db: Any, fabrique_utilisateur: Any) -> None:
        from commun.exceptions import ErreurMetier

        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        IngenieurProfil.objects.create(utilisateur=nadia)
        paul = fabrique_utilisateur(role=RoleUtilisateur.AGRICULTEUR.value)
        with pytest.raises(ErreurMetier) as exc:
            ServiceProfilIngenieur.valider(paul, str(nadia.id))
        assert exc.value.code == "permission_refusee"

    def test_obtenir_public_introuvable_si_non_valide(
        self, db: Any, fabrique_utilisateur: Any
    ) -> None:
        nadia = fabrique_utilisateur(role=RoleUtilisateur.INGENIEUR_AGRONOME.value)
        IngenieurProfil.objects.create(utilisateur=nadia)
        with pytest.raises(NonTrouve):
            ServiceProfilIngenieur.obtenir_public(str(nadia.id))


@pytest.mark.unit
class TestServiceProfilLivreur:
    def test_disponibilite_sans_position_refusee(self, db: Any, fabrique_utilisateur: Any) -> None:
        jean = fabrique_utilisateur(role=RoleUtilisateur.LIVREUR.value)
        with pytest.raises(ValidationMetier) as exc:
            ServiceProfilLivreur.definir_disponibilite(jean, disponible=True)
        assert exc.value.code == "position_requise"

    def test_disponibilite_avec_position_enregistree(
        self, db: Any, fabrique_utilisateur: Any
    ) -> None:
        jean = fabrique_utilisateur(role=RoleUtilisateur.LIVREUR.value)
        profil = ServiceProfilLivreur.definir_disponibilite(
            jean, disponible=True, position=POINT_DOUALA, zone="littoral"
        )
        assert profil.disponible is True
        assert profil.position_maj_le is not None
        assert profil.zone == "littoral"
        # Relu depuis MySQL : le point a fait l'aller GeoJSON → ST_GeomFromText → ST_AsText.
        relu = LivreurProfil.objects.get(utilisateur_id=jean.id)
        assert relu.position is not None

    def test_profil_livreur_reserve_aux_livreurs(self, db: Any, fabrique_utilisateur: Any) -> None:
        from commun.exceptions import ErreurMetier

        paul = fabrique_utilisateur(role=RoleUtilisateur.AGRICULTEUR.value)
        with pytest.raises(ErreurMetier):
            ServiceProfilLivreur.obtenir(paul)

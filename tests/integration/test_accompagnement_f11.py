"""Tests d'intégration API F-11 : accompagnement agronomique (SPEC-07).

Couvre le parcours complet (demande → acceptation → planification → paiement
webhook → clôture), les cas particuliers Q-29 (changement d'ingénieur) et
Q-30 (RDV manqué), le conflit de créneau, l'accès exploitation limité aux
statuts actifs et le filtre de zone `GET /ingenieurs?exploitation_id=`.
"""

import json
from datetime import timedelta
from typing import Any

import pytest
from apps.accompagnements.models import (
    Accompagnement,
    AffectationIngenieur,
    MotifFinAffectation,
    StatutAccompagnement,
)
from apps.administration.services import parametres
from apps.litiges.models import Remboursement, StatutRemboursement
from apps.notifications.models import Notification
from apps.paiements.models import ObjetPaiement, Paiement, StatutPaiement
from apps.paiements.passerelle import EN_TETE_SIGNATURE, signer_webhook
from apps.paiements.services import soldes
from apps.profils.models import IngenieurProfil, StatutValidation
from apps.utilisateurs.models import Utilisateur
from commun.horloge import decaler, maintenant, reinitialiser
from rest_framework.test import APIClient

from tests.unit.test_exploitations import polygone_yaounde

API = "/api/v1"
POINT_YAOUNDE = {"type": "Point", "coordinates": [11.5137, 3.848]}
# Nadia à ~11,1 km au nord : facturé 12 km → 4 800 FCFA (docs/04 §4).
POINT_NADIA = {"type": "Point", "coordinates": [11.5137, 3.9480]}
POINT_DOUALA = {"type": "Point", "coordinates": [9.7679, 4.0483]}


@pytest.fixture(autouse=True)
def _horloge_reinit() -> Any:
    reinitialiser()
    yield
    reinitialiser()


def _creer_exploitation(client: APIClient, nom: str = "Champ F11") -> dict[str, Any]:
    rep = client.post(
        f"{API}/exploitations",
        {
            "nom": nom,
            "parcelle": polygone_yaounde(),
            "type_sol": "argileux",
            "region": "centre",
        },
        format="json",
    )
    assert rep.status_code == 201, rep.data
    return rep.json()


def _configurer_ingenieure(
    client_authentifie: Any,
    *,
    zone_centre: dict[str, Any] | None = None,
    zone_rayon_km: int = 60,
    tarif: int = 10_000,
    specialites: list[str] | None = None,
    disponible: bool = True,
    valider: bool = True,
) -> tuple[APIClient, Any]:
    """Crée une ingénieure, remplit son profil via l'API, la valide si demandé."""
    client, user = client_authentifie(role="ingenieur_agronome")
    rep = client.put(
        f"{API}/ingenieurs/moi/profil",
        {
            "specialites": specialites or ["maraichage"],
            "region": "centre",
            "zone_centre": zone_centre or POINT_NADIA,
            "zone_rayon_km": zone_rayon_km,
            "tarif_consultation_fcfa": tarif,
            "biographie": "Ingénieure de test.",
            "modalites": "Sur site.",
        },
        format="json",
    )
    assert rep.status_code == 200, rep.data
    if valider:
        mireille, _ = client_authentifie(role="admin")
        v = mireille.post(f"{API}/admin/ingenieurs/{user.id}/valider", {}, format="json")
        assert v.status_code == 200, v.data
    # Marquer disponible (le PUT ne touche pas ce champ pour l'instant).
    profil = IngenieurProfil.objects.get(utilisateur_id=user.id)
    profil.disponible = disponible
    profil.save(update_fields=["disponible", "maj_le"])
    user.refresh_from_db()
    return client, user


def _poster_webhook_succes(client: APIClient, paiement: Paiement) -> None:
    corps = json.dumps(
        {
            "id": f"evt-{paiement.reference_interne}",
            "type": "payment.complete",
            "data": {
                "reference": paiement.reference_notchpay,
                "merchant_reference": paiement.reference_interne,
                "amount": paiement.montant_fcfa,
                "currency": "XAF",
            },
        }
    ).encode("utf-8")
    entete = {f"HTTP_{EN_TETE_SIGNATURE.upper().replace('-', '_')}": signer_webhook(corps)}
    rep = client.post(
        f"{API}/webhooks/notchpay",
        data=corps,
        content_type="application/json",
        **entete,  # type: ignore[arg-type]
    )
    assert rep.status_code == 200, rep.data


def _parcours_jusqu_acceptee(
    client_authentifie: Any,
    *,
    modalite: str = "presentiel",
) -> dict[str, Any]:
    paul, paul_user = client_authentifie(role="agriculteur")
    nadia, nadia_user = _configurer_ingenieure(client_authentifie)
    exploitation = _creer_exploitation(paul)
    demande = paul.post(
        f"{API}/accompagnements",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia_user.id,
            "modalite": modalite,
            "motif": "Conseil fertilisation",
        },
        format="json",
    )
    assert demande.status_code == 201, demande.data
    acc = demande.json()
    acceptee = nadia.post(f"{API}/accompagnements/{acc['id']}/accepter")
    assert acceptee.status_code == 200, acceptee.data
    return {
        "paul": paul,
        "paul_user": paul_user,
        "nadia": nadia,
        "nadia_user": nadia_user,
        "exploitation": exploitation,
        "accompagnement": acceptee.json(),
    }


def _planifier_et_payer(ctx: dict[str, Any]) -> Paiement:
    """Planifie le créneau par Nadia, Paul confirme, webhook NOTchPay réussi."""
    paul, nadia, acc = ctx["paul"], ctx["nadia"], ctx["accompagnement"]
    debut = maintenant() + timedelta(days=3, hours=10)
    fin = debut + timedelta(hours=1)
    rep = nadia.post(
        f"{API}/accompagnements/{acc['id']}/planifier",
        {
            "date_debut": debut.isoformat(),
            "date_fin": fin.isoformat(),
            "lieu_rdv_texte": "Siège Champ Nord",
        },
        format="json",
    )
    assert rep.status_code == 200, rep.data
    conf = paul.post(
        f"{API}/accompagnements/{acc['id']}/confirmer",
        {"operateur": "mtn_momo"},
        format="json",
    )
    assert conf.status_code == 201, conf.data
    paiement = Paiement.objects.get(id=conf.json()["paiement_id"])
    _poster_webhook_succes(paul, paiement)
    paiement.refresh_from_db()
    return paiement


# --- Devis --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_devis_12_km_aller_donne_4800_fcfa(client_authentifie: Any) -> None:
    """docs/04 §4, docs/07 §15.6 : 12 km aller → 24 × 200 = 4 800 FCFA."""
    paul, _ = client_authentifie(role="agriculteur")
    _, nadia = _configurer_ingenieure(client_authentifie)
    exploitation = _creer_exploitation(paul)
    rep = paul.post(
        f"{API}/accompagnements/devis",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia.id,
            "modalite": "presentiel",
        },
        format="json",
    )
    assert rep.status_code == 200, rep.data
    d = rep.json()
    assert d["consultation_fcfa"] == 10_000
    assert d["distance_km_aller"] == 12
    assert d["deplacement_fcfa"] == 4_800
    # Q-06 : commission 5 % uniquement sur la consultation.
    assert d["commission_consultation_fcfa"] == 500
    assert d["total_fcfa"] == 14_800
    # Gain net = consultation − commission + déplacement.
    assert d["gain_ingénieur_fcfa"] == 14_300


@pytest.mark.integration
@pytest.mark.django_db
def test_devis_en_ligne_aucun_deplacement(client_authentifie: Any) -> None:
    paul, _ = client_authentifie(role="agriculteur")
    _, nadia = _configurer_ingenieure(client_authentifie)
    exploitation = _creer_exploitation(paul)
    rep = paul.post(
        f"{API}/accompagnements/devis",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia.id,
            "modalite": "en_ligne",
        },
        format="json",
    )
    assert rep.status_code == 200, rep.data
    assert rep.json()["deplacement_fcfa"] == 0
    assert rep.json()["distance_km_aller"] is None
    assert rep.json()["total_fcfa"] == 10_000


@pytest.mark.integration
@pytest.mark.django_db
def test_devis_presentiel_refuse_au_dela_distance_max(client_authentifie: Any) -> None:
    """docs/12 PW-F11-03 : distance_max_km dépassée → 422 `distance_max_depasee`."""
    paul, _ = client_authentifie(role="agriculteur")
    # Ingénieure à ~12 km du champ, mais on abaisse la distance max à 10 km.
    _, nadia = _configurer_ingenieure(client_authentifie, zone_centre=POINT_NADIA, zone_rayon_km=60)
    exploitation = _creer_exploitation(paul)
    parametres.definir("deplacement.distance_max_km", 10)
    rep = paul.post(
        f"{API}/accompagnements/devis",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia.id,
            "modalite": "presentiel",
        },
        format="json",
    )
    assert rep.status_code == 422, rep.data
    assert rep.json()["erreur"]["code"] == "distance_max_depasee"
    assert rep.json()["erreur"]["details"]["modalite_suggeree"] == "en_ligne"


# --- Parcours complet + webhook Q-05 ------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_parcours_complet_jusqua_terminee(client_authentifie: Any) -> None:
    """E2E-07 côté API : demande → acceptée → planifiée → payée (webhook) → terminée."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    paul, nadia = ctx["paul"], ctx["nadia"]
    nadia_user, acc = ctx["nadia_user"], ctx["accompagnement"]

    # Nadia planifie : le statut reste `acceptee` (pas encore payé).
    debut = maintenant() + timedelta(days=3, hours=10)
    fin = debut + timedelta(hours=1)
    plan = nadia.post(
        f"{API}/accompagnements/{acc['id']}/planifier",
        {
            "date_debut": debut.isoformat(),
            "date_fin": fin.isoformat(),
            "lieu_rdv_texte": "Siège Champ Nord",
        },
        format="json",
    )
    assert plan.status_code == 200, plan.data
    assert plan.json()["statut"] == StatutAccompagnement.ACCEPTEE
    assert plan.json()["prix_consultation_fcfa"] == 10_000
    assert plan.json()["frais_deplacement_fcfa"] == 4_800
    assert plan.json()["distance_km"] == 12

    # Paul confirme → paiement ; puis webhook NotchPay réussi.
    conf = paul.post(
        f"{API}/accompagnements/{acc['id']}/confirmer",
        {"operateur": "mtn_momo"},
        format="json",
    )
    assert conf.status_code == 201, conf.data
    corps = conf.json()
    assert corps["montant_fcfa"] == 14_800
    assert corps["url_paiement"]
    paiement = Paiement.objects.get(id=corps["paiement_id"])
    _poster_webhook_succes(paul, paiement)

    # Après webhook : `planifiee` + notification à l'ingénieur.
    paiement.refresh_from_db()
    assert paiement.statut == StatutPaiement.REUSSI
    assert paiement.objet == ObjetPaiement.ACCOMPAGNEMENT
    assert Accompagnement.objects.get(id=acc["id"]).statut == StatutAccompagnement.PLANIFIEE
    assert Notification.objects.filter(
        destinataire_id=nadia_user.id, type="accompagnement"
    ).exists()

    # Clôture impossible sans observation (07 §15.6).
    decaler(timedelta(days=4))
    refus = nadia.post(
        f"{API}/accompagnements/{acc['id']}/terminer",
        {"observation": "   "},
        format="json",
    )
    assert refus.status_code == 422, refus.data
    assert refus.json()["erreur"]["code"] == "observation_obligatoire"

    ok = nadia.post(
        f"{API}/accompagnements/{acc['id']}/terminer",
        {"observation": "Sol riche, prévoir rotation maïs/niéré."},
        format="json",
    )
    assert ok.status_code == 200, ok.data
    assert ok.json()["statut"] == StatutAccompagnement.TERMINEE
    assert "rotation" in ok.json()["observation"]

    # Affectation active clôturée avec motif `termine`.
    derniere = AffectationIngenieur.objects.filter(accompagnement_id=acc["id"]).latest("debut")
    assert derniere.motif_fin == MotifFinAffectation.TERMINE
    assert derniere.fin is not None


# --- Conflit de créneau (409) -------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_conflit_creneau_refuse_409(client_authentifie: Any) -> None:
    """docs/12 PW-F11-04 : deux RDV qui se chevauchent → 409."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    nadia, nadia_user = ctx["nadia"], ctx["nadia_user"]
    # Planification + paiement de acc1 → statut `planifiee` (verrou bloquant).
    paiement = _planifier_et_payer(ctx)
    assert paiement.statut == StatutPaiement.REUSSI
    acc1 = Accompagnement.objects.get(id=ctx["accompagnement"]["id"])
    debut = acc1.date_rdv_debut
    fin = acc1.date_rdv_fin
    assert debut is not None and fin is not None

    # Deuxième demande par un autre agriculteur, même ingénieur.
    paul2, _ = client_authentifie(role="agriculteur")
    exp2 = _creer_exploitation(paul2, nom="Champ 2")
    d2 = paul2.post(
        f"{API}/accompagnements",
        {
            "exploitation_id": exp2["id"],
            "ingenieur_id": nadia_user.id,
            "modalite": "presentiel",
            "motif": "Deuxième demande",
        },
        format="json",
    )
    assert d2.status_code == 201, d2.data
    acc2 = d2.json()
    nadia.post(f"{API}/accompagnements/{acc2['id']}/accepter")
    # Créneau qui chevauche acc1 → conflit.
    rep = nadia.post(
        f"{API}/accompagnements/{acc2['id']}/planifier",
        {
            "date_debut": (debut + timedelta(minutes=30)).isoformat(),
            "date_fin": (fin + timedelta(minutes=30)).isoformat(),
            "lieu_rdv_texte": "Champ",
        },
        format="json",
    )
    assert rep.status_code == 409, rep.data
    assert rep.json()["erreur"]["code"] == "conflit_creneau"


# --- Changement d'ingénieur (Q-29) --------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_changement_ingenieur_avant_paiement_par_agriculteur(client_authentifie: Any) -> None:
    """Q-29 : agriculteur peut changer l'ingénieur avant le paiement."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    paul, nadia_user = ctx["paul"], ctx["nadia_user"]
    acc = ctx["accompagnement"]
    _, clarisse = _configurer_ingenieure(client_authentifie, tarif=15_000)
    assert clarisse.id != nadia_user.id
    rep = paul.post(
        f"{API}/accompagnements/{acc['id']}/changer-ingenieur",
        {"ingenieur_id": clarisse.id},
        format="json",
    )
    assert rep.status_code == 200, rep.data
    assert rep.json()["ingenieur"] == clarisse.id
    assert rep.json()["statut"] == StatutAccompagnement.DEMANDEE
    # Affectation Nadia clôturée avec motif `changement`, nouvelle active.
    a_nadia = AffectationIngenieur.objects.get(
        accompagnement_id=acc["id"], ingenieur_id=nadia_user.id
    )
    assert a_nadia.motif_fin == MotifFinAffectation.CHANGEMENT
    a_clar = AffectationIngenieur.objects.get(
        accompagnement_id=acc["id"], ingenieur_id=clarisse.id
    )
    assert a_clar.fin is None


@pytest.mark.integration
@pytest.mark.django_db
def test_changement_apres_paiement_reserve_a_admin(client_authentifie: Any) -> None:
    """Q-29 : après paiement, seul l'admin peut changer (montant conservé)."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    paul, acc = ctx["paul"], ctx["accompagnement"]
    _planifier_et_payer(ctx)
    _, clarisse = _configurer_ingenieure(client_authentifie)

    refus = paul.post(
        f"{API}/accompagnements/{acc['id']}/changer-ingenieur",
        {"ingenieur_id": clarisse.id},
        format="json",
    )
    assert refus.status_code == 422, refus.data
    assert refus.json()["erreur"]["code"] == "changement_apres_paiement"

    mireille, _ = client_authentifie(role="admin")
    ok = mireille.post(
        f"{API}/accompagnements/{acc['id']}/changer-ingenieur",
        {"ingenieur_id": clarisse.id},
        format="json",
    )
    assert ok.status_code == 200, ok.data
    assert ok.json()["ingenieur"] == clarisse.id
    # Montant conservé : toujours 10 000 (consultation d'origine).
    assert ok.json()["prix_consultation_fcfa"] == 10_000
    assert ok.json()["statut"] == StatutAccompagnement.DEMANDEE


# --- RDV manqué (Q-30) --------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_rdv_manque_par_ingenieur_declenche_remboursement_auto(
    client_authentifie: Any,
) -> None:
    """Q-30 : ingénieur absent → remboursement intégral créé et approuvé."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    paul, acc = ctx["paul"], ctx["accompagnement"]
    paiement = _planifier_et_payer(ctx)
    decaler(timedelta(days=4))
    rep = paul.post(
        f"{API}/accompagnements/{acc['id']}/marquer-manque",
        {"absent": "ingenieur"},
        format="json",
    )
    assert rep.status_code == 200, rep.data
    assert rep.json()["statut"] == StatutAccompagnement.MANQUEE
    assert rep.json()["nb_rdv_manques"] == 1

    r = Remboursement.objects.get(accompagnement_id=acc["id"])
    assert r.statut == StatutRemboursement.APPROUVE
    assert r.commande is None
    assert r.montant_accorde_fcfa == paiement.montant_fcfa
    assert r.demandeur_id == ctx["paul_user"].id


@pytest.mark.integration
@pytest.mark.django_db
def test_rdv_manque_par_agriculteur_permet_reprogrammation(client_authentifie: Any) -> None:
    """Q-30 : agriculteur absent → pas de remboursement, reprogrammation gratuite."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    nadia, acc = ctx["nadia"], ctx["accompagnement"]
    _planifier_et_payer(ctx)
    decaler(timedelta(days=4))
    rep = nadia.post(
        f"{API}/accompagnements/{acc['id']}/marquer-manque",
        {"absent": "agriculteur"},
        format="json",
    )
    assert rep.status_code == 200, rep.data
    assert rep.json()["statut"] == StatutAccompagnement.MANQUEE
    assert not Remboursement.objects.filter(accompagnement_id=acc["id"]).exists()

    # Reprogrammation : `manquee → planifiee` sans nouveau paiement.
    nouveau_debut = maintenant() + timedelta(days=2, hours=14)
    nouveau_fin = nouveau_debut + timedelta(hours=1)
    re = nadia.post(
        f"{API}/accompagnements/{acc['id']}/planifier",
        {
            "date_debut": nouveau_debut.isoformat(),
            "date_fin": nouveau_fin.isoformat(),
            "lieu_rdv_texte": "Champ",
        },
        format="json",
    )
    assert re.status_code == 200, re.data
    assert re.json()["statut"] == StatutAccompagnement.PLANIFIEE


# --- Accès exploitation F-04 --------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_acces_exploitation_uniquement_pendant_accompagnement_actif(
    client_authentifie: Any,
) -> None:
    """docs/12 PW-F11-07 : Nadia ne voit Champ Nord que pendant l'accompagnement actif."""
    paul, _ = client_authentifie(role="agriculteur")
    nadia, nadia_user = _configurer_ingenieure(client_authentifie)
    exploitation = _creer_exploitation(paul)

    # Avant demande : 404 (conventions §4 — pas de 403).
    assert nadia.get(f"{API}/exploitations/{exploitation['id']}").status_code == 404

    demande = paul.post(
        f"{API}/accompagnements",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia_user.id,
            "modalite": "presentiel",
            "motif": "Conseil",
        },
        format="json",
    )
    assert demande.status_code == 201
    acc_id = demande.json()["id"]
    # `demandee` n'ouvre PAS encore l'accès (02 §3.3 + prompt F-11 point 5).
    assert nadia.get(f"{API}/exploitations/{exploitation['id']}").status_code == 404
    nadia.post(f"{API}/accompagnements/{acc_id}/accepter")
    pendant = nadia.get(f"{API}/exploitations/{exploitation['id']}")
    assert pendant.status_code == 200
    assert pendant.json()["nom"] == "Champ F11"

    paul.post(f"{API}/accompagnements/{acc_id}/annuler")
    assert nadia.get(f"{API}/exploitations/{exploitation['id']}").status_code == 404


# --- Permissions et garde -----------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_demande_a_un_ingenieur_non_valide_refusee(client_authentifie: Any) -> None:
    """D-14 : seul un ingénieur validé et disponible est éligible."""
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _creer_exploitation(paul)
    # Ingénieur fraîchement créé, non validé.
    nadia, nadia_user = client_authentifie(role="ingenieur_agronome")
    nadia.put(
        f"{API}/ingenieurs/moi/profil",
        {
            "specialites": ["maraichage"],
            "region": "centre",
            "zone_centre": POINT_NADIA,
            "zone_rayon_km": 60,
            "tarif_consultation_fcfa": 10_000,
            "biographie": "En attente.",
        },
        format="json",
    )
    profil = IngenieurProfil.objects.get(utilisateur_id=nadia_user.id)
    profil.disponible = True
    profil.save(update_fields=["disponible", "maj_le"])
    assert profil.statut_validation == StatutValidation.EN_ATTENTE

    rep = paul.post(
        f"{API}/accompagnements",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia_user.id,
            "modalite": "presentiel",
            "motif": "Test",
        },
        format="json",
    )
    assert rep.status_code == 422, rep.data
    assert rep.json()["erreur"]["code"] == "ingenieur_indisponible"


@pytest.mark.integration
@pytest.mark.django_db
def test_accompagnement_d_autrui_renvoie_404(client_authentifie: Any) -> None:
    """Conventions §4 : pas de 403 sur ressource privée, 404 systématique."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    acc_id = ctx["accompagnement"]["id"]
    inconnu, _ = client_authentifie(role="agriculteur")
    assert inconnu.get(f"{API}/accompagnements/{acc_id}").status_code == 404
    # Et pour un acheteur également.
    amina, _ = client_authentifie(role="acheteur")
    assert amina.get(f"{API}/accompagnements/{acc_id}").status_code == 404


@pytest.mark.integration
@pytest.mark.django_db
def test_seul_ingénieur_affecté_peut_accepter(client_authentifie: Any) -> None:
    """Un autre ingénieur tentant d'accepter une demande qui n'est pas la sienne → 404."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    acc_id = ctx["accompagnement"]["id"]
    autre, _ = _configurer_ingenieure(client_authentifie, tarif=8_000)
    rep = autre.post(f"{API}/accompagnements/{acc_id}/accepter")
    assert rep.status_code == 404


# --- GET /ingenieurs?exploitation_id (filtre zone) ----------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_filtre_exploitations_par_zone(client_authentifie: Any) -> None:
    """GET /ingenieurs?exploitation_id= ne retourne que les ingénieurs dont la
    zone couvre le centre de l'exploitation (docs/03 §SPEC-07, D-14)."""
    paul, _ = client_authentifie(role="agriculteur")
    exploitation = _creer_exploitation(paul)
    # Nadia : près du centre, zone 60 km → couvrante.
    _, nadia = _configurer_ingenieure(
        client_authentifie, zone_centre=POINT_NADIA, zone_rayon_km=60
    )
    # Clarisse : loin (Douala), zone 30 km → hors zone.
    _, clarisse = _configurer_ingenieure(
        client_authentifie,
        zone_centre=POINT_DOUALA,
        zone_rayon_km=30,
        specialites=["cacao_cafe"],
    )
    rep = paul.get(f"{API}/ingenieurs?exploitation_id={exploitation['id']}")
    assert rep.status_code == 200, rep.data
    ids = {el["id"] for el in rep.json()["elements"]}
    assert nadia.id in ids
    assert clarisse.id not in ids


# --- Refus et notification ----------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_refus_avec_motif_et_notif(client_authentifie: Any) -> None:
    """Refus d'une `demandee` → 422 si motif vide, 200 sinon, notification."""
    paul, paul_user = client_authentifie(role="agriculteur")
    nadia, nadia_user = _configurer_ingenieure(client_authentifie)
    exploitation = _creer_exploitation(paul)
    demande = paul.post(
        f"{API}/accompagnements",
        {
            "exploitation_id": exploitation["id"],
            "ingenieur_id": nadia_user.id,
            "modalite": "en_ligne",
            "motif": "Test refus",
        },
        format="json",
    )
    assert demande.status_code == 201, demande.data
    acc_id = demande.json()["id"]

    sans_motif = nadia.post(
        f"{API}/accompagnements/{acc_id}/refuser", {"motif": " "}, format="json"
    )
    assert sans_motif.status_code == 422, sans_motif.data
    assert sans_motif.json()["erreur"]["code"] == "motif_obligatoire"

    ok = nadia.post(
        f"{API}/accompagnements/{acc_id}/refuser",
        {"motif": "Spécialité différente."},
        format="json",
    )
    assert ok.status_code == 200, ok.data
    assert ok.json()["statut"] == StatutAccompagnement.REFUSEE
    notif = (
        Notification.objects.filter(destinataire_id=paul_user.id, type="accompagnement")
        .order_by("-cree_le")
        .first()
    )
    assert notif is not None
    assert "Spécialité" in notif.contenu


# --- Soldes ingénieur (F-10 intégration) --------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_solde_ingenieur_integre_accompagnements_termines(client_authentifie: Any) -> None:
    """Un accompagnement `terminee` crédite consultation − commission + déplacement."""
    ctx = _parcours_jusqu_acceptee(client_authentifie)
    nadia, nadia_user, acc = ctx["nadia"], ctx["nadia_user"], ctx["accompagnement"]
    _planifier_et_payer(ctx)
    decaler(timedelta(days=4))
    nadia.post(
        f"{API}/accompagnements/{acc['id']}/terminer",
        {"observation": "Compte rendu de test."},
        format="json",
    )
    user = Utilisateur.objects.get(id=nadia_user.id)
    assert soldes.details(user)["solde"] == 14_300




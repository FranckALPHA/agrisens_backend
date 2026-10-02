"""Logique métier des commandes (SPEC-04, modèle 02 §2.3 et §3.1).

Devis sans écriture, création à un seul agriculteur avec copies figées,
transitions avec historique + notifications, expiration par le worker.
Le paiement (F-07), la livraison (F-08) et l'évaluation (F-09) étendront
cette machine à états sans la réécrire.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from commun.exceptions import Conflit, NonTrouve, TransitionInvalide, ValidationMetier
from commun.geo import distance_km, valider_geojson
from commun.horloge import maintenant
from django.db import transaction

from apps.administration.models import HistoriqueStatut
from apps.administration.services import parametres
from apps.commandes.models import Commande, CompteurReference, LigneCommande, StatutCommande
from apps.commandes.tarification import frais_livraison, total_ligne
from apps.exploitations.models import StatutExploitation
from apps.notifications.services import notifier
from apps.produits.models import Produit, StatutProduit
from apps.produits.services import liberer as _liberer
from apps.produits.services import reserver as _reserver
from apps.utilisateurs.models import Adresse, RoleUtilisateur, Utilisateur

CLES_TARIFS = (
    "livraison.base_fcfa",
    "livraison.tarif_km_fcfa",
    "livraison.minimum_fcfa",
    "livraison.plafond_fcfa",
    "livraison.distance_max_km",
    "commande.delai_acceptation_h",
    "commande.delai_paiement_h",
    "commission.taux_pb",
)

# Statuts où l'agriculteur ne voit que la zone de livraison (D-21, PW-F06-11).
STATUTS_ADRESSE_MASQUEE = frozenset(
    {
        StatutCommande.EN_ATTENTE,
        StatutCommande.ACCEPTEE,
        StatutCommande.PAIEMENT_EN_ATTENTE,
        StatutCommande.REJETEE,
        StatutCommande.ANNULEE,
    }
)


def _lire_tarifs() -> dict[str, Any]:
    return {cle: parametres.obtenir(cle) for cle in CLES_TARIFS}


def _en_decimal(valeur: Any, champ: str) -> Decimal:
    try:
        quantite = Decimal(str(valeur))
    except Exception:
        raise ValidationMetier(
            "quantite_invalide", "Quantité invalide.", {"champ": champ}
        ) from None
    if quantite <= 0:
        raise ValidationMetier(
            "quantite_invalide",
            "La quantité doit être strictement positive.",
            {"champ": champ},
        )
    return quantite


def _produit_commandable(produit_id: str) -> Produit:
    produit = (
        Produit.objects.filter(id=produit_id)
        .select_related("exploitation", "exploitation__agriculteur")
        .first()
    )
    if (
        produit is None
        or produit.statut != StatutProduit.DISPONIBLE
        or produit.exploitation.statut != StatutExploitation.ACTIVE
    ):
        raise ValidationMetier(
            "produit_indisponible",
            "Produit indisponible.",
            {"produit_id": produit_id},
        )
    return produit


def _adresse_acheteur(
    acheteur: Any, adresse_id: str | None, adresse_texte: str | None, position: Any
) -> tuple[str, str, dict[str, Any]]:
    """Résout (texte figé, zone, position GeoJSON) depuis une adresse ou une saisie."""
    if adresse_id:
        adresse = Adresse.objects.filter(id=adresse_id, utilisateur=acheteur).first()
        if adresse is None:
            raise NonTrouve()
        position_geo = (
            adresse.position.__geo_interface__
            if hasattr(adresse.position, "__geo_interface__")
            else adresse.position
        )
        texte = f"{adresse.libelle} — {adresse.description}, {adresse.ville} ({adresse.region})"
        zone = adresse.ville or adresse.region or "Zone non précisée"
        return texte, zone, position_geo
    if not adresse_texte or not position:
        raise ValidationMetier(
            "adresse_obligatoire",
            "Adresse de livraison obligatoire (adresse enregistrée ou texte + position).",
        )
    valider_geojson(position)
    if position.get("type") != "Point":
        raise ValidationMetier("geometrie_invalide", "La position de livraison doit être un point.")
    return str(adresse_texte), "Zone non précisée", position


def _calculer_devis(
    lignes: list[dict[str, Any]],
    adresse_texte: str,
    zone: str,
    position_livraison: dict[str, Any],
) -> dict[str, Any]:
    """Prépare lignes + totaux + distance + frais, sans rien écrire."""
    if not lignes:
        raise ValidationMetier("commande_vide", "La commande ne contient aucune ligne.")
    tarifs = _lire_tarifs()
    details: list[dict[str, Any]] = []
    agriculteur_id = None
    for ligne in lignes:
        produit = _produit_commandable(ligne["produit_id"])
        quantite = _en_decimal(ligne.get("quantite"), "quantite")
        if agriculteur_id is None:
            agriculteur_id = produit.exploitation.agriculteur_id
        elif produit.exploitation.agriculteur_id != agriculteur_id:
            raise ValidationMetier(
                "commande_multi_agriculteurs",
                "Une commande concerne un seul agriculteur (D-02).",
            )
        if produit.quantite_disponible < quantite:
            raise Conflit(
                code="stock_insuffisant",
                message=f"Stock disponible insuffisant pour {produit.nom}.",
                details={
                    "produit_id": produit.id,
                    "disponible": str(produit.quantite_disponible),
                },
            )
        details.append(
            {
                "produit": produit,
                "quantite": quantite,
                "total": total_ligne(quantite, produit.prix_unitaire_fcfa),
            }
        )
    premier = details[0]["produit"]
    centre = premier.exploitation.centre
    centre_geo = centre.__geo_interface__ if hasattr(centre, "__geo_interface__") else centre
    distance_exacte = distance_km(centre_geo, position_livraison)
    if distance_exacte > float(tarifs["livraison.distance_max_km"]):
        raise ValidationMetier(
            "hors_zone_livraison",
            "Adresse hors zone de livraison.",
            {"distance_km": round(distance_exacte, 2)},
        )
    km_factures, frais = frais_livraison(
        distance_exacte,
        base_fcfa=int(tarifs["livraison.base_fcfa"]),
        tarif_km_fcfa=int(tarifs["livraison.tarif_km_fcfa"]),
        minimum_fcfa=int(tarifs["livraison.minimum_fcfa"]),
        plafond_fcfa=int(tarifs["livraison.plafond_fcfa"]),
    )
    sous_total = sum(d["total"] for d in details)
    return {
        "agriculteur_id": agriculteur_id,
        "details": details,
        "adresse_texte": adresse_texte,
        "zone": zone,
        "position_livraison": position_livraison,
        "position_recuperation": centre_geo,  # Hypothèse Q-39 : 1re ligne.
        "distance_exacte_km": distance_exacte,
        "distance_km": km_factures,
        "sous_total_fcfa": sous_total,
        "frais_livraison_fcfa": frais,
        "total_fcfa": sous_total + frais,
        "tarifs": tarifs,
    }


def _nouvelle_reference() -> str:
    annee = maintenant().year
    with transaction.atomic():
        compteur, _ = CompteurReference.objects.select_for_update().get_or_create(
            prefixe="CMD", annee=annee, defaults={"dernier_numero": 0}
        )
        compteur.dernier_numero += 1
        compteur.save(update_fields=["dernier_numero", "maj_le"])
        return f"CMD-{annee}-{compteur.dernier_numero:06d}"


def _historiser(
    commande: Commande, ancien: str | None, nouveau: str, acteur: Any, motif: str | None
) -> None:
    HistoriqueStatut.objects.create(
        entite_type="commande",
        entite_id=commande.id,
        ancien_statut=ancien,
        nouveau_statut=nouveau,
        acteur=acteur,
        motif=motif,
    )


def _transition(
    commande: Commande, nouveau: str, autorises: tuple[str, ...], acteur: Any, motif: str | None
) -> None:
    if commande.statut not in autorises:
        raise TransitionInvalide(commande.statut, nouveau, "commande")
    ancien = commande.statut
    commande.statut = nouveau
    commande.save(update_fields=["statut", "maj_le"])
    _historiser(commande, ancien, nouveau, acteur, motif)


class ServiceCommande:
    """Devis, création, listes, détail, transitions et historique."""

    # --- Devis et création -------------------------------------------------

    @staticmethod
    def devis(acheteur: Any, **donnees: Any) -> dict[str, Any]:
        texte, zone, position = _adresse_acheteur(
            acheteur,
            donnees.get("adresse_id"),
            donnees.get("adresse_texte"),
            donnees.get("position"),
        )
        calcul = _calculer_devis(donnees.get("lignes") or [], texte, zone, position)
        return {
            "agriculteur_id": calcul["agriculteur_id"],
            "lignes": [
                {
                    "produit_id": d["produit"].id,
                    "nom_produit": d["produit"].nom,
                    "unite": d["produit"].unite,
                    "quantite": str(d["quantite"]),
                    "prix_unitaire_fcfa": d["produit"].prix_unitaire_fcfa,
                    "total_ligne_fcfa": d["total"],
                }
                for d in calcul["details"]
            ],
            "adresse_livraison_texte": calcul["adresse_texte"],
            "zone_livraison": calcul["zone"],
            "distance_km": calcul["distance_km"],
            "sous_total_fcfa": calcul["sous_total_fcfa"],
            "frais_livraison_fcfa": calcul["frais_livraison_fcfa"],
            "total_fcfa": calcul["total_fcfa"],
        }

    @staticmethod
    @transaction.atomic
    def creer(acheteur: Any, **donnees: Any) -> Commande:
        texte, zone, position = _adresse_acheteur(
            acheteur,
            donnees.get("adresse_id"),
            donnees.get("adresse_texte"),
            donnees.get("position"),
        )
        calcul = _calculer_devis(donnees.get("lignes") or [], texte, zone, position)
        tarifs = _lire_tarifs()
        delai = int(tarifs["commande.delai_acceptation_h"])
        commande = Commande.objects.create(
            reference=_nouvelle_reference(),
            acheteur=acheteur,
            agriculteur=Utilisateur.objects.get(id=calcul["agriculteur_id"]),
            statut=StatutCommande.EN_ATTENTE,
            adresse_livraison_texte=calcul["adresse_texte"],
            zone_livraison=calcul["zone"],
            position_livraison=calcul["position_livraison"],
            position_recuperation=calcul["position_recuperation"],
            distance_km=calcul["distance_km"],
            sous_total_fcfa=calcul["sous_total_fcfa"],
            frais_livraison_fcfa=calcul["frais_livraison_fcfa"],
            total_fcfa=calcul["total_fcfa"],
            parametres_appliques={k: tarifs[k] for k in CLES_TARIFS},
            expire_le=maintenant() + timedelta(hours=delai),
        )
        for detail in calcul["details"]:
            produit = detail["produit"]
            LigneCommande.objects.create(
                commande=commande,
                produit=produit,
                nom_produit=produit.nom,
                unite=produit.unite,
                quantite=detail["quantite"],
                prix_unitaire_fcfa=produit.prix_unitaire_fcfa,
                total_ligne_fcfa=detail["total"],
            )
        _historiser(commande, None, StatutCommande.EN_ATTENTE, acheteur, None)
        notifier(
            commande.agriculteur,
            "commande",
            f"Nouvelle commande {commande.reference}",
            f"{acheteur.nom} : {commande.total_fcfa} FCFA, à accepter sous {delai} h.",
            lien=f"/agriculteur/commandes/{commande.id}",
        )
        return commande

    # --- Listes et détail ---------------------------------------------------

    @staticmethod
    def mes_achats(acheteur: Any, statut: str | None = None) -> list[Commande]:
        requete = Commande.objects.filter(acheteur=acheteur).select_related("agriculteur")
        if statut in StatutCommande.values:
            requete = requete.filter(statut=statut)
        return list(requete)

    @staticmethod
    def mes_ventes(agriculteur: Any, statut: str | None = None) -> list[Commande]:
        requete = Commande.objects.filter(agriculteur=agriculteur).select_related("acheteur")
        if statut in StatutCommande.values:
            requete = requete.filter(statut=statut)
        return list(requete)

    @staticmethod
    def obtenir_visible(utilisateur: Any, identifiant: str) -> Commande:
        commande = (
            Commande.objects.filter(id=identifiant)
            .select_related("acheteur", "agriculteur")
            .first()
        )
        if commande is None:
            raise NonTrouve()
        if getattr(utilisateur, "role", None) in (
            RoleUtilisateur.ADMIN,
            RoleUtilisateur.SUPER_ADMIN,
        ):
            return commande
        if commande.acheteur_id == utilisateur.id or commande.agriculteur_id == utilisateur.id:
            return commande
        # Livreur affecté : F-08 (aucune livraison n'existe encore en F-06).
        raise NonTrouve()

    @staticmethod
    def historique_visible(utilisateur: Any, identifiant: str) -> list[HistoriqueStatut]:
        commande = ServiceCommande.obtenir_visible(utilisateur, identifiant)
        return list(
            HistoriqueStatut.objects.filter(entite_type="commande", entite_id=commande.id).order_by(
                "cree_le"
            )
        )

    @staticmethod
    def adresse_pour(commande: Commande, utilisateur: Any) -> dict[str, Any]:
        """Adresse selon la visibilité D-21 (PW-F06-11).

        L'agriculteur ne voit le texte exact qu'à partir du paiement ;
        avant, seulement la zone. L'acheteur et l'admin voient tout.
        """
        if commande.acheteur_id == getattr(utilisateur, "id", None) or getattr(
            utilisateur, "role", None
        ) in (RoleUtilisateur.ADMIN, RoleUtilisateur.SUPER_ADMIN):
            return {"texte": commande.adresse_livraison_texte, "exacte": True}
        if commande.statut in STATUTS_ADRESSE_MASQUEE:
            return {"texte": commande.zone_livraison or "Zone non précisée", "exacte": False}
        return {"texte": commande.adresse_livraison_texte, "exacte": True}

    # --- Transitions --------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def accepter(agriculteur: Any, identifiant: str) -> Commande:
        commande = (
            Commande.objects.select_for_update()
            .filter(id=identifiant, agriculteur=agriculteur)
            .first()
        )
        if commande is None:
            raise NonTrouve()
        _transition(
            commande, StatutCommande.ACCEPTEE, (StatutCommande.EN_ATTENTE,), agriculteur, None
        )
        for ligne in commande.lignes.select_related("produit").all():
            _reserver(str(ligne.produit_id), ligne.quantite)
        tarifs = _lire_tarifs()
        commande.expire_le = maintenant() + timedelta(
            hours=int(tarifs["commande.delai_paiement_h"])
        )
        commande.save(update_fields=["expire_le", "maj_le"])
        notifier(
            commande.acheteur,
            "commande",
            f"Commande {commande.reference} acceptée",
            "Procédez au paiement sous 24 h pour confirmer.",
            lien=f"/commandes/{commande.id}",
        )
        return commande

    @staticmethod
    @transaction.atomic
    def rejeter(agriculteur: Any, identifiant: str, motif: str | None) -> Commande:
        commande = Commande.objects.filter(id=identifiant, agriculteur=agriculteur).first()
        if commande is None:
            raise NonTrouve()
        motif_propre = (motif or "").strip()
        if not motif_propre:
            raise ValidationMetier("motif_obligatoire", "Le motif du rejet est obligatoire.")
        _transition(
            commande,
            StatutCommande.REJETEE,
            (StatutCommande.EN_ATTENTE,),
            agriculteur,
            motif_propre,
        )
        commande.motif_rejet = motif_propre
        commande.save(update_fields=["motif_rejet", "maj_le"])
        notifier(
            commande.acheteur,
            "commande",
            f"Commande {commande.reference} rejetée",
            motif_propre,
            lien=f"/commandes/{commande.id}",
        )
        return commande

    @staticmethod
    @transaction.atomic
    def annuler(acheteur: Any, identifiant: str) -> Commande:
        commande = (
            Commande.objects.select_for_update().filter(id=identifiant, acheteur=acheteur).first()
        )
        if commande is None:
            raise NonTrouve()
        ancien = commande.statut
        _transition(
            commande,
            StatutCommande.ANNULEE,
            (StatutCommande.EN_ATTENTE, StatutCommande.ACCEPTEE),
            acheteur,
            None,
        )
        if ancien == StatutCommande.ACCEPTEE:
            for ligne in commande.lignes.all():
                _liberer(str(ligne.produit_id), ligne.quantite)
        notifier(
            commande.agriculteur,
            "commande",
            f"Commande {commande.reference} annulée",
            "Le stock réservé a été libéré.",
            lien=f"/agriculteur/commandes/{commande.id}",
        )
        return commande

    @staticmethod
    @transaction.atomic
    def preparer(agriculteur: Any, identifiant: str) -> Commande:
        commande = Commande.objects.filter(id=identifiant, agriculteur=agriculteur).first()
        if commande is None:
            raise NonTrouve()
        _transition(
            commande, StatutCommande.PREPARATION, (StatutCommande.PAYEE,), agriculteur, None
        )
        notifier(
            commande.acheteur,
            "commande",
            f"Commande {commande.reference} en préparation",
            "L'agriculteur prépare vos produits.",
            lien=f"/commandes/{commande.id}",
        )
        return commande

    # --- Paiement (F-07 : appelées par `apps/paiements/services.py`) ---------

    @staticmethod
    @transaction.atomic
    def passer_en_paiement(commande_id: str) -> Commande:
        """`acceptee → paiement_en_attente` à l'initialisation du paiement."""
        commande = Commande.objects.select_for_update().filter(id=commande_id).first()
        if commande is None:
            raise NonTrouve()
        _transition(
            commande,
            StatutCommande.PAIEMENT_EN_ATTENTE,
            (StatutCommande.ACCEPTEE,),
            commande.acheteur,
            "Paiement initié par l'acheteur.",
        )
        return commande

    @staticmethod
    @transaction.atomic
    def noter_paiement(commande_id: str, taux_pb: int, commission_fcfa: int) -> Commande:
        """Webhook réussi : `payee`, commission figée, notifications."""
        commande = Commande.objects.select_for_update().filter(id=commande_id).first()
        if commande is None:
            raise NonTrouve()
        _transition(
            commande,
            StatutCommande.PAYEE,
            (StatutCommande.ACCEPTEE, StatutCommande.PAIEMENT_EN_ATTENTE),
            None,
            "Paiement confirmé par webhook.",
        )
        commande.commission_taux_pb = taux_pb
        commande.commission_fcfa = commission_fcfa
        commande.expire_le = None
        commande.save()
        notifier(
            commande.acheteur,
            "commande",
            f"Commande {commande.reference} payée",
            "Paiement confirmé. L'agriculteur prépare vos produits.",
            lien=f"/commandes/{commande.id}",
        )
        notifier(
            commande.agriculteur,
            "commande",
            f"Commande {commande.reference} payée",
            "Vous pouvez préparer la commande.",
            lien=f"/agriculteur/commandes/{commande.id}",
        )
        return commande

    @staticmethod
    @transaction.atomic
    def revenir_acceptee(commande_id: str, motif: str) -> Commande | None:
        """Webhook échoué/expiré : retour à `acceptee` si le paiement était en cours."""
        commande = Commande.objects.select_for_update().filter(id=commande_id).first()
        if commande is None:
            raise NonTrouve()
        if commande.statut != StatutCommande.PAIEMENT_EN_ATTENTE:
            return None
        _transition(
            commande, StatutCommande.ACCEPTEE, (StatutCommande.PAIEMENT_EN_ATTENTE,), None, motif
        )
        notifier(
            commande.acheteur,
            "commande",
            f"Paiement non abouti ({commande.reference})",
            f"{motif} Nouvel essai possible.",
            lien=f"/commandes/{commande.id}",
        )
        return commande

    @staticmethod
    @transaction.atomic
    def expirer(commande: Commande, acteur: Any = None) -> Commande:
        """Bascule une commande dépassée en `annulee` (worker ou test)."""
        ancien = commande.statut
        _transition(
            commande,
            StatutCommande.ANNULEE,
            (StatutCommande.EN_ATTENTE, StatutCommande.ACCEPTEE),
            acteur,
            "Délai dépassé (expiration automatique).",
        )
        if ancien == StatutCommande.ACCEPTEE:
            for ligne in commande.lignes.all():
                _liberer(str(ligne.produit_id), ligne.quantite)
        notifier(
            commande.acheteur,
            "commande",
            f"Commande {commande.reference} annulée",
            "Délai dépassé.",
            lien=f"/commandes/{commande.id}",
        )
        notifier(
            commande.agriculteur,
            "commande",
            f"Commande {commande.reference} annulée",
            "Délai dépassé, stock libéré.",
            lien=f"/agriculteur/commandes/{commande.id}",
        )
        return commande

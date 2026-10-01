"""Logique métier des produits, du catalogue et du stock (SPEC-03).

Création, catalogue public, modification, stock, retrait et photos dans
`ServiceProduit` ; réservation/libération/consommation du stock
(`reserver`, `liberer`, `consommer`) avec verrou `SELECT … FOR UPDATE`,
réutilisées par les commandes (F-06) et les livraisons (F-08).
"""

from decimal import Decimal
from typing import Any

from commun.exceptions import Conflit, NonTrouve, ValidationMetier
from django.db import models, transaction

from apps.exploitations.models import Exploitation, StatutExploitation
from apps.fichiers.models import ContexteFichier, Fichier
from apps.produits.models import Produit, ProduitPhoto, StatutProduit
from apps.referentiels.donnees import REFERENTIELS_INITIAUX

CATEGORIES = frozenset(code for code, _ in REFERENTIELS_INITIAUX["categorie_produit"])
UNITES = frozenset(code for code, _ in REFERENTIELS_INITIAUX["unite"])

# Bornes de quantités acceptées (gardes anti-abus, pas des tarifs).
QUANTITE_MAX = Decimal("1000000")


def _en_decimal(valeur: Any, champ: str) -> Decimal:
    try:
        quantite = Decimal(str(valeur))
    except Exception:
        raise ValidationMetier(
            "quantite_invalide",
            "Quantité invalide.",
            {"champ": champ},
        ) from None
    if quantite < 0 or quantite > QUANTITE_MAX:
        raise ValidationMetier(
            "quantite_invalide",
            "Quantité invalide.",
            {"champ": champ},
        )
    return quantite


def _valider_reference(donnees: dict[str, Any]) -> None:
    categorie = donnees.get("categorie")
    if categorie is not None and categorie not in CATEGORIES:
        raise ValidationMetier(
            "categorie_inconnue",
            "Catégorie inconnue.",
            {"categorie": categorie, "attendues": sorted(CATEGORIES)},
        )
    unite = donnees.get("unite")
    if unite is not None and unite not in UNITES:
        raise ValidationMetier(
            "unite_inconnue",
            "Unité inconnue.",
            {"unite": unite, "attendues": sorted(UNITES)},
        )
    prix = donnees.get("prix_unitaire_fcfa")
    if prix is not None and (not isinstance(prix, int) or isinstance(prix, bool) or prix < 0):
        raise ValidationMetier(
            "prix_invalide",
            "Le prix unitaire doit être un entier FCFA positif ou nul.",
            {"prix_unitaire_fcfa": prix},
        )
    nom = donnees.get("nom")
    if nom is not None and not str(nom).strip():
        raise ValidationMetier("nom_obligatoire", "Le nom du produit est obligatoire.")


def _exploitation_du_proprietaire(agriculteur: Any, exploitation_id: str) -> Exploitation:
    exploitation = Exploitation.objects.filter(id=exploitation_id, agriculteur=agriculteur).first()
    if exploitation is None:
        # Exploitation inexistante ou d'un autre agriculteur : 404 silencieux.
        raise NonTrouve()
    # Hypothèse Q-38 : pas de produit sur une exploitation archivée.
    if exploitation.statut != StatutExploitation.ACTIVE:
        raise ValidationMetier(
            "exploitation_inactive",
            "Impossible d'ajouter un produit à une exploitation archivée.",
        )
    return exploitation


def _produit_du_proprietaire(agriculteur: Any, produit_id: str) -> Produit:
    produit = Produit.objects.filter(id=produit_id, exploitation__agriculteur=agriculteur).first()
    if produit is None:
        raise NonTrouve()
    return produit


def _synchroniser_statut(produit: Produit) -> None:
    """Disponible ⟷ épuisé selon le stock ; un produit retiré le reste."""
    if produit.statut == StatutProduit.RETIRE:
        return
    produit.statut = (
        StatutProduit.DISPONIBLE if produit.quantite_disponible > 0 else StatutProduit.EPUISE
    )


def verifier_photo_catalogue(fichier: Fichier, utilisateur: object | None) -> bool:
    """Photos de produits : publiques via un produit visible au catalogue.

    Déclaré au registre `apps/fichiers/acces.py` (pas d'import croisé) :
    le propriétaire et les admins y accèdent toujours ; toute autre personne
    y accède seulement si la photo illustre un produit `disponible` d'une
    exploitation active. Sans lien visible : 404 silencieux.
    """
    if fichier.public:
        return True
    if utilisateur is not None and (
        fichier.proprietaire_id == getattr(utilisateur, "id", None)
        or getattr(utilisateur, "role", None) in ("admin", "super_admin")
    ):
        return True
    return ProduitPhoto.objects.filter(
        fichier_id=fichier.id,
        produit__statut=StatutProduit.DISPONIBLE,
        produit__exploitation__statut=StatutExploitation.ACTIVE,
    ).exists()


class ServiceProduit:
    """Création, catalogue, modification, stock, retrait et photos."""

    # --- Création ---------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def creer(agriculteur: Any, **donnees: Any) -> Produit:
        _valider_reference(donnees)
        exploitation = _exploitation_du_proprietaire(agriculteur, donnees["exploitation_id"])
        quantite = _en_decimal(donnees.get("quantite_stock", "0"), "quantite_stock")
        produit = Produit.objects.create(
            exploitation=exploitation,
            nom=str(donnees["nom"]).strip(),
            categorie=donnees["categorie"],
            description=str(donnees.get("description") or ""),
            unite=donnees["unite"],
            prix_unitaire_fcfa=donnees["prix_unitaire_fcfa"],
            quantite_stock=quantite,
            quantite_reservee=Decimal("0"),
            statut=StatutProduit.DISPONIBLE,
        )
        _synchroniser_statut(produit)
        produit.save(update_fields=["statut", "maj_le"])
        for position, fichier_id in enumerate(donnees.get("photo_ids") or []):
            ServiceProduit.ajouter_photo(agriculteur, produit.id, fichier_id, ordre=position)
        return produit

    # --- Lecture ----------------------------------------------------------

    @staticmethod
    def catalogue(
        *,
        categorie: str | None = None,
        region: str | None = None,
        recherche: str | None = None,
        prix_min: int | None = None,
        prix_max: int | None = None,
        tri: str = "recent",
    ) -> list[Produit]:
        """Produits `disponible` d'exploitations actives, filtrables et triés."""
        requete = Produit.objects.filter(
            statut=StatutProduit.DISPONIBLE,
            exploitation__statut=StatutExploitation.ACTIVE,
        ).select_related("exploitation", "exploitation__agriculteur")
        if categorie:
            requete = requete.filter(categorie=categorie)
        if region:
            requete = requete.filter(exploitation__region=region)
        if recherche:
            requete = requete.filter(
                models.Q(nom__icontains=recherche) | models.Q(description__icontains=recherche),
            )
        if prix_min is not None:
            requete = requete.filter(prix_unitaire_fcfa__gte=prix_min)
        if prix_max is not None:
            requete = requete.filter(prix_unitaire_fcfa__lte=prix_max)
        tris = {
            "prix_asc": ("prix_unitaire_fcfa", "cree_le"),
            "prix_desc": ("-prix_unitaire_fcfa", "cree_le"),
            "recent": ("-cree_le",),
        }
        return list(requete.order_by(*tris.get(tri, tris["recent"])))

    @staticmethod
    def obtenir_public(produit_id: str) -> Produit:
        produit = (
            Produit.objects.filter(
                id=produit_id,
                statut=StatutProduit.DISPONIBLE,
                exploitation__statut=StatutExploitation.ACTIVE,
            )
            .select_related("exploitation", "exploitation__agriculteur")
            .first()
        )
        if produit is None:
            raise NonTrouve(
                code="produit_indisponible",
                message="Produit indisponible.",
            )
        return produit

    @staticmethod
    def mes_produits(agriculteur: Any) -> list[Produit]:
        return list(
            Produit.objects.filter(exploitation__agriculteur=agriculteur).select_related(
                "exploitation"
            )
        )

    @staticmethod
    def obtenir_proprietaire(agriculteur: Any, produit_id: str) -> Produit:
        return _produit_du_proprietaire(agriculteur, produit_id)

    # --- Modification -----------------------------------------------------

    @staticmethod
    @transaction.atomic
    def modifier(agriculteur: Any, produit_id: str, **donnees: Any) -> Produit:
        _valider_reference(donnees)
        produit = _produit_du_proprietaire(agriculteur, produit_id)
        for champ in ("nom", "categorie", "description", "unite", "prix_unitaire_fcfa"):
            if champ in donnees and donnees[champ] is not None:
                valeur = donnees[champ]
                setattr(
                    produit,
                    champ,
                    str(valeur).strip() if champ in ("nom", "description") else valeur,
                )
        produit.save()
        return produit

    @staticmethod
    @transaction.atomic
    def definir_stock(agriculteur: Any, produit_id: str, quantite_stock: Any) -> Produit:
        produit = _produit_du_proprietaire(agriculteur, produit_id)
        quantite = _en_decimal(quantite_stock, "quantite_stock")
        if quantite < produit.quantite_reservee:
            raise ValidationMetier(
                "stock_inferieur_reserve",
                "Le stock ne peut pas passer sous la quantité déjà réservée.",
                {
                    "quantite_stock": str(quantite),
                    "quantite_reservee": str(produit.quantite_reservee),
                },
            )
        produit.quantite_stock = quantite
        _synchroniser_statut(produit)
        produit.save()
        return produit

    @staticmethod
    @transaction.atomic
    def retirer(agriculteur: Any, produit_id: str) -> Produit:
        produit = Produit.objects.filter(
            id=produit_id,
            exploitation__agriculteur=agriculteur,
        ).first()
        if produit is None or produit.statut == StatutProduit.RETIRE:
            raise NonTrouve()
        produit.statut = StatutProduit.RETIRE
        produit.save(update_fields=["statut", "maj_le"])
        return produit

    # --- Photos -----------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def ajouter_photo(
        agriculteur: Any, produit_id: str, fichier_id: str, ordre: int | None = None
    ) -> ProduitPhoto:
        from apps.administration.services import parametres

        produit = _produit_du_proprietaire(agriculteur, produit_id)
        fichier = Fichier.objects.filter(id=fichier_id, proprietaire_id=agriculteur.id).first()
        if fichier is None:
            raise NonTrouve()
        if fichier.contexte != ContexteFichier.PRODUIT or not fichier.type_mime.startswith(
            "image/"
        ):
            raise ValidationMetier(
                "photo_invalide",
                "La photo doit être une image téléversée en contexte « produit ».",
                {"fichier_id": fichier_id},
            )
        maximum = int(parametres.obtenir("fichiers.photos_produit_max"))
        if produit.liens_photos.count() >= maximum:
            raise ValidationMetier(
                "trop_de_photos",
                "Nombre maximal de photos atteint pour ce produit.",
                {"maximum": maximum},
            )
        if ordre is None:
            ordre = produit.liens_photos.count()
        else:
            produit.liens_photos.filter(ordre__gte=ordre).update(ordre=models.F("ordre") + 1)
        lien, _ = ProduitPhoto.objects.get_or_create(
            produit=produit, fichier=fichier, defaults={"ordre": ordre}
        )
        if lien.ordre != ordre:
            lien.ordre = ordre
            lien.save(update_fields=["ordre", "maj_le"])
        return lien

    @staticmethod
    @transaction.atomic
    def retirer_photo(agriculteur: Any, produit_id: str, fichier_id: str) -> None:
        produit = _produit_du_proprietaire(agriculteur, produit_id)
        lien = produit.liens_photos.filter(fichier_id=fichier_id).first()
        if lien is None:
            raise NonTrouve()
        lien.delete()

    @staticmethod
    @transaction.atomic
    def ordonner_photos(agriculteur: Any, produit_id: str, fichier_ids: list[str]) -> Produit:
        produit = _produit_du_proprietaire(agriculteur, produit_id)
        actuels = {lien.fichier_id for lien in produit.liens_photos.all()}
        if set(fichier_ids) != actuels or len(fichier_ids) != len(actuels):
            raise ValidationMetier(
                "ordre_photos_invalide",
                "La liste doit reprendre exactement les photos actuelles, dans le nouvel ordre.",
            )
        for position, fichier_id in enumerate(fichier_ids):
            produit.liens_photos.filter(fichier_id=fichier_id).update(ordre=position)
        return produit


# --- Mouvements de stock (F-06 commandes, F-08 livraisons) ---------------------


def _produit_verrouille(produit_id: str) -> Produit:
    produit = Produit.objects.select_for_update().filter(id=produit_id).first()
    if produit is None:
        raise NonTrouve()
    return produit


def _verifier_mouvement(produit: Produit, quantite: Any) -> Decimal:
    quantite = _en_decimal(quantite, "quantite")
    if quantite <= 0:
        raise ValidationMetier(
            "quantite_invalide",
            "La quantité doit être strictement positive.",
            {"quantite": str(quantite)},
        )
    if produit.statut == StatutProduit.RETIRE:
        raise Conflit(
            code="produit_indisponible",
            message="Produit retiré de la vente.",
        )
    return quantite


@transaction.atomic
def reserver(produit_id: str, quantite: Any) -> Produit:
    """Réserve du stock (acceptation F-06) : une seule réservation gagne le dernier stock."""
    produit = _produit_verrouille(produit_id)
    quantite = _verifier_mouvement(produit, quantite)
    if produit.quantite_disponible < quantite:
        raise Conflit(
            code="stock_insuffisant",
            message="Stock disponible insuffisant.",
            details={
                "produit_id": produit.id,
                "disponible": str(produit.quantite_disponible),
            },
        )
    produit.quantite_reservee += quantite
    _synchroniser_statut(produit)
    produit.save()
    return produit


@transaction.atomic
def liberer(produit_id: str, quantite: Any) -> Produit:
    """Libère du stock réservé (annulation, expiration)."""
    produit = _produit_verrouille(produit_id)
    quantite = _verifier_mouvement(produit, quantite)
    produit.quantite_reservee = max(Decimal("0"), produit.quantite_reservee - quantite)
    _synchroniser_statut(produit)
    produit.save()
    return produit


@transaction.atomic
def consommer(produit_id: str, quantite: Any) -> Produit:
    """Décrémente le stock physique et la réservation (remise au livreur, F-08)."""
    produit = _produit_verrouille(produit_id)
    quantite = _verifier_mouvement(produit, quantite)
    if produit.quantite_reservee < quantite:
        raise Conflit(
            code="stock_insuffisant",
            message="Quantité réservée insuffisante.",
            details={
                "produit_id": produit.id,
                "reserve": str(produit.quantite_reservee),
            },
        )
    produit.quantite_reservee -= quantite
    produit.quantite_stock -= quantite
    _synchroniser_statut(produit)
    produit.save()
    return produit

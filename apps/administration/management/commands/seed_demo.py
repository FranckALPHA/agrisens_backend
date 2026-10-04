"""Commande manage.py seed_demo pour injecter les données de démonstration et tests E2E.

Idempotente : les comptes existants sont remis à l'état attendu
(rôle, statut, mot de passe) à chaque passage. Structure selon
docs/12-plan-tests-playwright.md §3, prompt F-02 point 8 et
e2e/fixtures/roles.ts côté frontend (mêmes téléphones, même mot de passe).

Le mot de passe de démonstration n'est valide que pour les bases
locales / test / e2e — jamais en production (prompt F-02 point 8).

F-03 point 5 : profils de Nadia (ingénieure validée, avec justificatifs)
et de Jean (livreur). F-11 (docs/12 PW-F11-01) : Nadia est positionnée
environ 11,5 km au nord du « Champ Nord » de Paul, disponible et validée,
ce qui donne un devis de déplacement de 4 800 FCFA (12 km × 2 × 200 FCFA,
P-06 et 07 §15.6). Son compte est `ACTIF` et son profil `VALIDE` ; le
parcours de validation E2E (PW-F03-01/02) repart d'une inscription
fraîche et n'utilise donc pas ce compte.

F-10 : un historique de paramètre, un reversement d'exemple et les
offres d'abonnement (déjà insérées par la migration) pour que les
écrans d'administration ne soient pas vides en démonstration.
"""

import io
from typing import Any

from commun.horloge import maintenant
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from PIL import Image

from apps.commandes.models import Commande
from apps.exploitations.models import Exploitation
from apps.exploitations.services import creer
from apps.fichiers.models import ContexteFichier
from apps.fichiers.services import service_fichiers
from apps.plannings.models import (
    CorrectionIA,
    GenerationIA,
    StatutGeneration,
    TypeGeneration,
)
from apps.plannings.prompts import PROMPT_PLANNING_V1
from apps.produits.models import Produit
from apps.profils.models import (
    IngenieurProfil,
    LivreurProfil,
    PieceJustificative,
    StatutJustificatif,
    StatutValidation,
    TypeJustificatif,
)
from apps.utilisateurs.models import RoleUtilisateur, StatutCompte, Utilisateur

# Comptes des personnages (docs/12 §3 + prompt F-02 point 8).
# Nadia est l'ingénieure retenue par Paul pour l'accompagnement F-11 : son
# profil est validé, disponible et positionné près du Champ Nord. Le
# parcours E2E de validation d'un ingénieur (PW-F03) passe par une
# inscription fraîche et ne dépend donc pas de ce compte.
UTILISATEURS_DEMO: list[dict[str, str]] = [
    {
        "telephone": "+237690000000",
        "nom": "Super Administrateur",
        "role": RoleUtilisateur.SUPER_ADMIN,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "root@agrisens.local",
    },
    {
        "telephone": "+237690000001",
        "nom": "Paul Mbianda",
        "role": RoleUtilisateur.AGRICULTEUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Ouest",
        "email": "paul@agrisens.local",
    },
    {
        "telephone": "+237690000002",
        "nom": "Amina Oumarou",
        "role": RoleUtilisateur.ACHETEUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "amina@agrisens.local",
    },
    {
        "telephone": "+237690000003",
        "nom": "Nadia Kamga",
        "role": RoleUtilisateur.INGENIEUR_AGRONOME,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "nadia@agrisens.local",
    },
    {
        "telephone": "+237690000004",
        "nom": "Jean Tchinda",
        "role": RoleUtilisateur.LIVREUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "jean@agrisens.local",
    },
    {
        "telephone": "+237690000005",
        "nom": "Mireille Ngo",
        "role": RoleUtilisateur.ADMIN,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Centre",
        "email": "mireille@agrisens.local",
    },
    {
        "telephone": "+237690000006",
        "nom": "Clarisse Eboa",
        "role": RoleUtilisateur.INGENIEUR_AGRONOME,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Littoral",
        "email": "clarisse@agrisens.local",
    },
    {
        "telephone": "+237690000007",
        "nom": "Alain Njoya",
        "role": RoleUtilisateur.LIVREUR,
        "statut_compte": StatutCompte.ACTIF,
        "region": "Ouest",
        "email": "alain@agrisens.local",
    },
    {
        "telephone": "+237690000099",
        "nom": "Compte Suspendu",
        "role": RoleUtilisateur.ACHETEUR,
        "statut_compte": StatutCompte.SUSPENDU,
        "region": "Ouest",
        "email": "suspendu@agrisens.local",
    },
]

MOT_DE_PASSE_DEMO = "Motdepasse123!"

# Positions GeoJSON (lon, lat) dans l'emprise du Cameroun (commun/geo.py).
POINT_YAOUNDE: dict[str, Any] = {"type": "Point", "coordinates": [11.5137, 3.848]}
# Hypothèse Q-44 : position de Nadia ≈ 11,5 km au nord de Yaoundé pour
# que le devis F-11 de PW-F11-01 donne exactement 12 km facturés et
# 4 800 FCFA de déplacement (24 km AR × 200 FCFA/km, P-06).
POINT_NADIA: dict[str, Any] = {"type": "Point", "coordinates": [11.5137, 3.9520]}


def _octets_pdf() -> bytes:
    """Mini PDF valide (le type est vérifié sur le contenu par `filetype`)."""
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _octets_jpeg() -> bytes:
    """Petit JPEG réel généré par Pillow (avec EXIF retiré à l'upload)."""
    sortie = io.BytesIO()
    Image.new("RGB", (40, 40), (18, 100, 60)).save(sortie, format="JPEG")
    return sortie.getvalue()


class Command(BaseCommand):
    help = "Injecte les données de démonstration (local / test / e2e uniquement)."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Initialisation des données de démonstration (idempotent)...")

        # Vieux doublons de démo (schémas antérieurs à F-02 : mêmes emails,
        # téléphones différents) : ils bloqueraient la création des comptes
        # officiels docs/12 §3 sur l'unicité de l'email.
        emails_demo = {info["email"] for info in UTILISATEURS_DEMO}
        telephones_demo = {info["telephone"] for info in UTILISATEURS_DEMO}
        obsoletes = Utilisateur.objects.filter(email__in=emails_demo).exclude(
            telephone__in=telephones_demo
        )
        nb_obsoletes = obsoletes.count()
        if nb_obsoletes:
            obsoletes.delete()
            self.stdout.write(f"{nb_obsoletes} doublon(s) de démo obsolète(s) supprimé(s).")

        crees = 0
        existants = 0
        for info in UTILISATEURS_DEMO:
            telephone = info["telephone"]
            utilisateur = Utilisateur.objects.filter(telephone=telephone).first()
            if utilisateur is None:
                utilisateur = Utilisateur.objects.create_user(
                    telephone=telephone,
                    password=MOT_DE_PASSE_DEMO,
                    nom=info["nom"],
                    role=info["role"],
                    statut_compte=info["statut_compte"],
                    region=info["region"],
                    email=info["email"],
                )
                crees += 1
            else:
                # Recalage de l'état attendu : les tests E2E partent du
                # principe que ces comptes ont ces rôles/statuts/mot de passe.
                utilisateur.nom = info["nom"]
                utilisateur.role = info["role"]
                utilisateur.statut_compte = info["statut_compte"]
                utilisateur.region = info["region"]
                utilisateur.email = info["email"]
                utilisateur.set_password(MOT_DE_PASSE_DEMO)
                utilisateur.doit_changer_mot_de_passe = False
                utilisateur.save()
                existants += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Données de démonstration prêtes "
                f"({crees} utilisateurs créés, {existants} recalés)."
            )
        )
        self._profils_demo()
        self._exploitation_demo()
        self._produits_demo()
        self._commandes_demo()
        self._livraisons_demo()
        self._litiges_demo()
        self._administration_demo()
        self._plannings_demo()
        self._conversations_demo()

    # --- Profils F-03 --------------------------------------------------------

    def _profils_demo(self) -> None:
        """Nadia (validée, disponible, près du Champ Nord), Clarisse (validée), Jean (livreur)."""
        par_telephone = {
            u.telephone: u
            for u in Utilisateur.objects.filter(
                telephone__in=[
                    "+237690000003",  # Nadia
                    "+237690000006",  # Clarisse
                    "+237690000004",  # Jean
                ]
            )
        }

        nadia = par_telephone["+237690000003"]
        mireille = Utilisateur.objects.get(telephone="+237690000005")
        profil_nadia, _ = IngenieurProfil.objects.update_or_create(
            utilisateur=nadia,
            defaults={
                "specialites": ["maraichage", "vivriers"],
                "region": "centre",
                # Position volontairement à ~11,5 km du centre de « Champ Nord »
                # pour que `km_superieurs` facture 12 km et donne 4 800 FCFA
                # (docs/12 PW-F11-01, docs/04 §4).
                "zone_centre": POINT_NADIA,
                "zone_rayon_km": 60,
                "tarif_consultation_fcfa": 10_000,
                "disponible": True,
                "biographie": "Ingénieure agronome à Yaoundé, maraîchage et vivriers.",
                "modalites": "Sur site ou en visioconférence, en semaine.",
                "statut_validation": StatutValidation.VALIDE,
                "motif_refus": None,
                "valide_par": mireille,
            },
        )
        if profil_nadia.valide_le is None:
            profil_nadia.valide_le = maintenant()
            profil_nadia.save(update_fields=["valide_le", "maj_le"])
        self._justificatifs(
            profil_nadia,
            [
                (TypeJustificatif.DIPLOME, "diplome-agronomie.pdf", _octets_pdf()),
                (TypeJustificatif.PIECE_IDENTITE, "cin.jpg", _octets_jpeg()),
            ],
            statut=StatutJustificatif.ACCEPTEE,
        )

        clarisse = par_telephone["+237690000006"]
        mireille = Utilisateur.objects.get(telephone="+237690000005")
        profil_clarisse, _ = IngenieurProfil.objects.update_or_create(
            utilisateur=clarisse,
            defaults={
                "specialites": ["cacao_cafe", "arboriculture"],
                "region": "littoral",
                "zone_centre": POINT_YAOUNDE,
                "zone_rayon_km": 80,
                "tarif_consultation_fcfa": 15_000,
                # Hypothèse de démo : profil complet + validé ⇒ disponible (D-13).
                "disponible": True,
                "biographie": "Ingénieure sénior, filières cacao-café.",
                "modalites": "Déplacements dans la région Centrale.",
                "statut_validation": StatutValidation.VALIDE,
                "motif_refus": None,
                "valide_par": mireille,
            },
        )
        if profil_clarisse.valide_le is None:
            profil_clarisse.valide_le = maintenant()
            profil_clarisse.save(update_fields=["valide_le", "maj_le"])
        self._justificatifs(
            profil_clarisse,
            [(TypeJustificatif.DIPLOME, "master-agronomie.pdf", _octets_pdf())],
            statut=StatutJustificatif.ACCEPTEE,
        )

        jean = par_telephone["+237690000004"]
        LivreurProfil.objects.update_or_create(
            utilisateur=jean,
            defaults={
                "disponible": False,
                "position": POINT_YAOUNDE,
                "position_maj_le": maintenant(),
                "zone": "centre",
            },
        )
        self.stdout.write("Profils de démonstration prêts (Nadia, Clarisse, Jean).")

    def _exploitation_demo(self) -> None:
        """Champ Nord de Paul, parcelle stable près de Yaoundé (F-04)."""
        paul = Utilisateur.objects.get(telephone="+237690000001")
        anneau = [
            [11.5130, 3.8475],
            [11.5140, 3.8475],
            [11.5140, 3.8485],
            [11.5130, 3.8485],
            [11.5130, 3.8475],
        ]
        parcelle = {"type": "Polygon", "coordinates": [anneau]}
        existe = Exploitation.objects.filter(agriculteur=paul, nom="Champ Nord").exists()
        if not existe:
            creer(
                paul,
                nom="Champ Nord",
                parcelle=parcelle,
                type_sol="argileux",
                region="centre",
                localite="Yaoundé",
            )
        self.stdout.write("Exploitation de démonstration prête (Champ Nord).")

    # --- Produits F-05 ---------------------------------------------------------

    def _produits_demo(self) -> None:
        """100 kg de tomates à 500 FCFA/kg avec photo, autres produits, un épuisé."""
        from apps.produits.services import ServiceProduit

        paul = Utilisateur.objects.get(telephone="+237690000001")
        champ = Exploitation.objects.filter(agriculteur=paul, nom="Champ Nord").first()
        if champ is None:
            self.stdout.write("Pas de Champ Nord : produits de démonstration ignorés.")
            return
        catalogue = [
            {
                "nom": "Tomates",
                "categorie": "legumes",
                "unite": "kg",
                "prix_unitaire_fcfa": 500,
                "quantite_stock": "100",
                "description": "Tomates fraîches du Champ Nord.",
                "photo": True,
            },
            {
                "nom": "Maïs",
                "categorie": "cereales",
                "unite": "kg",
                "prix_unitaire_fcfa": 350,
                "quantite_stock": "200",
                "description": "Maïs grain.",
                "photo": False,
            },
            {
                "nom": "Gombo",
                "categorie": "legumes",
                "unite": "kg",
                "prix_unitaire_fcfa": 700,
                "quantite_stock": "30",
                "description": "Gombo frais.",
                "photo": False,
            },
            {
                "nom": "Piment",
                "categorie": "legumes",
                "unite": "kg",
                "prix_unitaire_fcfa": 1200,
                "quantite_stock": "0",
                "description": "Piment fort, de retour bientôt.",
                "photo": False,
            },
        ]
        for fiche in catalogue:
            if Produit.objects.filter(exploitation=champ, nom=fiche["nom"]).exists():
                continue
            photo_ids: list[str] = []
            if fiche["photo"]:
                fichier = service_fichiers.televerser(
                    proprietaire=paul,
                    contexte=ContexteFichier.PRODUIT,
                    fichier=SimpleUploadedFile(
                        "tomates.jpg", _octets_jpeg(), content_type="image/jpeg"
                    ),
                )
                photo_ids.append(fichier.id)
            ServiceProduit.creer(
                paul,
                exploitation_id=str(champ.id),
                nom=str(fiche["nom"]),
                categorie=str(fiche["categorie"]),
                unite=str(fiche["unite"]),
                prix_unitaire_fcfa=int(str(fiche["prix_unitaire_fcfa"])),
                quantite_stock=str(fiche["quantite_stock"]),
                description=str(fiche["description"]),
                photo_ids=photo_ids,
            )
        self.stdout.write("Produits de démonstration prêts (Tomates, Maïs, Gombo, Piment).")

    # --- Commandes F-06 ----------------------------------------------------------

    def _commandes_demo(self) -> None:
        """Adresses d'Amina (6 km et > 50 km de Champ Nord) + une commande par
        statut atteignable en F-06 (en_attente, acceptee, rejetee, annulee)."""
        from apps.commandes.services import ServiceCommande
        from apps.produits.models import Produit
        from apps.utilisateurs.models import Adresse

        paul = Utilisateur.objects.get(telephone="+237690000001")
        amina = Utilisateur.objects.get(telephone="+237690000002")
        champ = Exploitation.objects.filter(agriculteur=paul, nom="Champ Nord").first()
        tomates = Produit.objects.filter(
            exploitation=champ, nom="Tomates", statut="disponible"
        ).first()
        if champ is None or tomates is None:
            self.stdout.write("Champ Nord ou Tomates manquants : commandes ignorées.")
            return
        centre = champ.centre
        lon, lat = centre.x, centre.y

        def adresse_demo(libelle: str, decalage_lat: float, ville: str) -> Adresse:
            adresse, _ = Adresse.objects.update_or_create(
                utilisateur=amina,
                libelle=libelle,
                defaults={
                    "description": "Repères de démonstration",
                    "position": {
                        "type": "Point",
                        "coordinates": [lon, lat + decalage_lat],
                    },
                    "ville": ville,
                    "region": "centre",
                },
            )
            return adresse

        # ~5,5 km (facturés 6 km : 1 400 FCFA) et ~78 km (hors zone > 50 km).
        proche = adresse_demo("Domicile", 0.050, "Yaoundé")
        adresse_demo("Boutique", 0.700, "Nanga-Eboko")

        existantes = Commande.objects.filter(acheteur=amina).count()
        if existantes >= 4:
            self.stdout.write("Commandes de démonstration déjà présentes.")
            return
        ServiceCommande.creer(
            amina,
            lignes=[{"produit_id": str(tomates.id), "quantite": "5"}],
            adresse_id=str(proche.id),
        )
        acceptee = ServiceCommande.creer(
            amina,
            lignes=[{"produit_id": str(tomates.id), "quantite": "3"}],
            adresse_id=str(proche.id),
        )
        ServiceCommande.accepter(paul, str(acceptee.id))
        rejetee = ServiceCommande.creer(
            amina,
            lignes=[{"produit_id": str(tomates.id), "quantite": "2"}],
            adresse_id=str(proche.id),
        )
        ServiceCommande.rejeter(paul, str(rejetee.id), "Stock déjà promis ailleurs.")
        annulee = ServiceCommande.creer(
            amina,
            lignes=[{"produit_id": str(tomates.id), "quantite": "1"}],
            adresse_id=str(proche.id),
        )
        ServiceCommande.annuler(amina, str(annulee.id))
        self.stdout.write("Commandes de démonstration prêtes (4 statuts).")

    # --- Livraisons F-08 ---------------------------------------------------------

    def _commande_payee(
        self, amina: Any, paul: Any, tomates: Any, adresse: Any, quantite: str
    ) -> Commande:
        """Crée, accepte et paie une commande de démo (vrai webhook signé)."""
        from apps.commandes.services import ServiceCommande
        from apps.paiements.services import ServicePaiement

        commande = ServiceCommande.creer(
            amina,
            lignes=[{"produit_id": str(tomates.id), "quantite": quantite}],
            adresse_id=str(adresse.id),
        )
        ServiceCommande.accepter(paul, str(commande.id))
        paiement, _, _ = ServicePaiement.initier_paiement_commande(
            amina,
            str(commande.id),
            "mtn_momo",
            None,
            f"seed-{commande.reference}",
            "http://localhost:5173",
        )
        # Webhook réussi via le vrai point d'entrée (signature valide).
        import json as _json

        from apps.paiements.passerelle import EN_TETE_SIGNATURE, signer_webhook

        corps = _json.dumps(
            {
                "id": f"evt-seed-{paiement.reference_interne}",
                "type": "payment.complete",
                "data": {
                    "reference": paiement.reference_notchpay,
                    "merchant_reference": paiement.reference_interne,
                    "amount": paiement.montant_fcfa,
                },
            }
        ).encode()
        statut, _ = ServicePaiement.traiter_webhook(
            {EN_TETE_SIGNATURE: signer_webhook(corps)}, corps
        )
        assert statut == 200
        return Commande.objects.get(id=commande.id)

    def _livrer(self, alain: Any, paul: Any, commande: Commande) -> Commande:
        """Course complète jusqu'à `livree` (acceptation, préparation, code)."""
        from apps.commandes.services import ServiceCommande
        from apps.livraisons.models import Livraison
        from apps.livraisons.services import ServiceLivraison, dechiffrer_code

        livraison = Livraison.objects.get(commande=commande)
        ServiceLivraison.accepter(alain, str(livraison.id))
        ServiceCommande.preparer(paul, str(commande.id))
        ServiceLivraison.demarrer(alain, str(livraison.id))
        code = dechiffrer_code(Livraison.objects.get(id=livraison.id).code_remise_chiffre)
        ServiceLivraison.confirmer_remise(alain, str(livraison.id), code)
        return Commande.objects.get(id=commande.id)

    def _livraisons_demo(self) -> None:
        """Une livraison proposée, une en cours, une livrée (vrais appels)."""
        from apps.livraisons.models import Livraison
        from apps.livraisons.services import ServiceLivraison
        from apps.produits.models import Produit
        from apps.profils.models import LivreurProfil

        if Livraison.objects.count() >= 3:
            self.stdout.write("Livraisons de démonstration déjà présentes.")
            return
        paul = Utilisateur.objects.get(telephone="+237690000001")
        amina = Utilisateur.objects.get(telephone="+237690000002")
        alain = Utilisateur.objects.get(telephone="+237690000007")
        adresse = amina.adresses.filter(libelle="Domicile").first() or amina.adresses.first()
        tomates = Produit.objects.filter(nom="Tomates", statut="disponible").first()
        if adresse is None or tomates is None:
            self.stdout.write("Adresse ou Tomates manquantes : livraisons ignorées.")
            return
        LivreurProfil.objects.update_or_create(
            utilisateur=alain,
            defaults={
                "disponible": True,
                "position": {"type": "Point", "coordinates": [11.5137, 3.848]},
                "position_maj_le": maintenant(),
                "zone": "centre",
            },
        )

        # 1. Proposée (en attente d'un livreur).
        self._commande_payee(amina, paul, tomates, adresse, "4")
        # 2. En cours (Alain accepte, Paul prépare, démarrage).
        en_cours = self._commande_payee(amina, paul, tomates, adresse, "6")
        livraison_cours = Livraison.objects.get(commande=en_cours)
        ServiceLivraison.accepter(alain, str(livraison_cours.id))
        from apps.commandes.services import ServiceCommande

        ServiceCommande.preparer(paul, str(en_cours.id))
        ServiceLivraison.demarrer(alain, str(livraison_cours.id))
        # 3. Livrée (code réel déchiffré pour la démo).
        self._livrer(alain, paul, self._commande_payee(amina, paul, tomates, adresse, "2"))
        self.stdout.write("Livraisons de démonstration prêtes (proposée, en cours, livrée).")

    # --- Litiges F-09 -------------------------------------------------------------

    def _litiges_demo(self) -> None:
        """Une demande de remboursement en attente + un signalement ouvert.

        La commande livrée de `_livraisons_demo` reste intacte pour les E2E
        PW-F09 (demande dans le délai de 48 h) ; ici on crée une quatrième
        commande livrée spécifiquement en litige.
        """
        from apps.litiges.models import Remboursement, Signalement
        from apps.litiges.services import ServiceRemboursement, ServiceSignalement
        from apps.produits.models import Produit

        if Remboursement.objects.exists() and Signalement.objects.exists():
            self.stdout.write("Litiges de démonstration déjà présents.")
            return
        paul = Utilisateur.objects.get(telephone="+237690000001")
        amina = Utilisateur.objects.get(telephone="+237690000002")
        alain = Utilisateur.objects.get(telephone="+237690000007")
        adresse = amina.adresses.filter(libelle="Domicile").first() or amina.adresses.first()
        tomates = Produit.objects.filter(nom="Tomates", statut="disponible").first()
        if adresse is None or tomates is None:
            self.stdout.write("Adresse ou Tomates manquantes : litiges ignorés.")
            return
        if not Remboursement.objects.exists():
            livree = self._livrer(
                alain, paul, self._commande_payee(amina, paul, tomates, adresse, "1")
            )
            photo = service_fichiers.televerser(
                proprietaire=amina,
                contexte=ContexteFichier.JUSTIFICATIF,
                fichier=SimpleUploadedFile(
                    "tomates-abimees.jpg", _octets_jpeg(), content_type="image/jpeg"
                ),
            )
            ServiceRemboursement.demander(
                amina, str(livree.id), "produit_endommage", 900, [str(photo.id)]
            )
        if not Signalement.objects.exists():
            ServiceSignalement.creer(
                amina,
                "produit",
                str(tomates.id),
                "photo_trompeuse",
                "Les photos du produit ne correspondent pas à la livraison reçue.",
            )
        self.stdout.write(
            "Litiges de démonstration prêts (remboursement en attente, signalement ouvert)."
        )

    # --- Administration F-10 ----------------------------------------------------

    def _administration_demo(self) -> None:
        """Historique de paramètre + reversement d'exemple pour les écrans ADM.

        Le tarif au km reste à sa valeur de démarrage (150 FCFA) : les
        scénarios E2E PW-F10-01/02 partent de cette valeur. On insère une
        ligne d'historique « confirmée » et un reversement à Alain (livreur
        des livraisons de démo) pour que l'audit et la liste des reversements
        ne soient pas vides, sans entamer le solde de Paul (PW-F10-06/07).
        """
        from apps.administration.models import ParametrePlateforme
        from apps.administration.services import parametres
        from apps.paiements.models import OperateurPaiement, Reversement
        from apps.paiements.services import reversements, soldes

        sadm = Utilisateur.objects.get(telephone="+237690000000")
        mireille = Utilisateur.objects.get(telephone="+237690000005")
        alain = Utilisateur.objects.get(telephone="+237690000007")

        if not ParametrePlateforme.objects.filter(cle="livraison.tarif_km_fcfa").exists():
            parametres.definir(
                "livraison.tarif_km_fcfa",
                150,
                auteur=sadm,
                motif="Valeur initiale confirmée lors de la revue des tarifs (démo).",
            )

        if not Reversement.objects.exists():
            solde_alain = soldes.details(alain)["solde"]
            if solde_alain > 0:
                reversements.enregistrer(
                    mireille,
                    beneficiaire_id=str(alain.id),
                    montant_fcfa=min(1000, solde_alain),
                    operateur=OperateurPaiement.MTN_MOMO,
                    reference_operateur="MTN-DEMO-000001",
                    note="Reversement d'exemple (démonstration).",
                )
        self.stdout.write("Données de démonstration administration prêtes (F-10).")

    # --- Plannings IA F-12 --------------------------------------------------

    def _plannings_demo(self) -> None:
        """Une génération IA réussie (planning tomate) + une correction de Nadia.

        Alimente PW-F12-01 (AGR-07 affiche un planning, son historique et
        l'avertissement) et PW-F12-02 (ING-05 montre proposition IA et corrigé
        côte à côte). Écriture ORM directe : le seed n'emprunte pas le chemin
        permission du service. Réponse conforme au schéma `PlanningResult`.
        """
        paul = Utilisateur.objects.get(telephone="+237690000001")
        nadia = Utilisateur.objects.get(telephone="+237690000003")
        champ = Exploitation.objects.filter(agriculteur=paul, nom="Champ Nord").first()
        if champ is None:
            self.stdout.write("Pas de Champ Nord : plannings de démonstration ignorés.")
            return

        centre = champ.centre
        lat = round(centre.y, 1) if centre is not None else 3.848
        lon = round(centre.x, 1) if centre is not None else 11.502
        debut = maintenant().date()
        fin = (
            debut.replace(year=debut.year + 1)
            if debut.month == 12
            else debut.replace(month=debut.month + 1)
        )
        periode: dict[str, str] = {"debut": debut.isoformat(), "fin": fin.isoformat()}
        entrees: dict[str, Any] = {
            "culture": "tomate",
            "sol": champ.type_sol,
            "superficie_m2": int(champ.superficie_ha * 10000),
            "periode": periode,
            "region": champ.region,
            "centre": {"lat": lat, "lon": lon},
            "infos_agronomiques": None,
        }
        t1 = debut
        t2 = debut.replace(month=min(debut.month + 1, 12))
        t3 = debut.replace(month=min(debut.month + 2, 12))
        reponse = {
            "culture": "tomate",
            "resume": "Planning cultural indicatif de la tomate sur le Champ Nord.",
            "avertissement": (
                "Ceci est une recommandation générée par une IA, pas un "
                "diagnostic agronomique. Faites valider par un ingénieur."
            ),
            "etapes": [
                {
                    "date_debut": t1.isoformat(),
                    "date_fin": t2.isoformat(),
                    "activite": "Semis en pépinière",
                    "details": "Préparer les planches, semer en lignes, arroser matin et soir.",
                    "intrants": ["semences de tomate", "compost"],
                },
                {
                    "date_debut": t2.isoformat(),
                    "date_fin": t3.isoformat(),
                    "activite": "Plantation et entretien",
                    "details": "Repiquer les plants, tuteurer, sarcler et fertiliser.",
                    "intrants": ["engrais NPK", "tuteurs"],
                },
                {
                    "date_debut": t3.isoformat(),
                    "date_fin": debut.replace(year=debut.year + 1).isoformat(),
                    "activite": "Récolte",
                    "details": "Récolter aux heures fraîches, trier et conditionner.",
                    "intrants": ["paniers de récolte"],
                },
            ],
            "risques": ["mildiou en saison humide", "manque d'eau en saison sèche"],
            "conseils": ["Installer un paillage pour limiter l'évaporation."],
        }
        generation, cree = GenerationIA.objects.update_or_create(
            type=TypeGeneration.PLANNING,
            exploitation=champ,
            demandeur=paul,
            statut=StatutGeneration.REUSSIE,
            mois_quota=maintenant().strftime("%Y-%m"),
            defaults={
                "culture": "tomate",
                "periode_debut": periode["debut"],
                "periode_fin": periode["fin"],
                "entrees": entrees,
                "reponse_originale": reponse,
                "fournisseur_ia": "factice",
                "modele_ia": "factice",
                "version_prompt": PROMPT_PLANNING_V1,
                "erreur": None,
                "cle_idempotence": None,
            },
        )
        if cree:
            CorrectionIA.objects.create(
                generation=generation,
                ingenieur=nadia,
                commentaire=(
                    "Planning cohérent. Prévoir un paillage dès la plantation et "
                    "surveiller le mildiou en saison humide."
                ),
                contenu_corrige=None,
            )
        self.stdout.write("Plannings de démonstration prêts (F-12 : génération + correction).")

    # --- Conversations / chat F-13 -----------------------------------------

    def _conversations_demo(self) -> None:
        """Une conversation commande Amina↔Paul et une accompagnement Paul↔Nadia.

        Écriture ORM directe (le seed n'emprunte pas le contrôle de partie du
        service). Sert PW-F13-01 (réception temps réel), PW-F13-05 (historique
        long, curseur `avant`) et PW-F13-07 (tiers sans lien → 404). Idempotent.
        """
        from apps.accompagnements.models import Accompagnement, Modalite, StatutAccompagnement
        from apps.conversations.models import Conversation, Message, Participant, TypeConversation

        paul = Utilisateur.objects.get(telephone="+237690000001")
        amina = Utilisateur.objects.get(telephone="+237690000002")
        nadia = Utilisateur.objects.get(telephone="+237690000003")
        champ = Exploitation.objects.filter(agriculteur=paul, nom="Champ Nord").first()

        # 1. Conversation rattachée à une commande Amina↔Paul.
        commande = (
            Commande.objects.filter(acheteur=amina, agriculteur=paul).order_by("-cree_le").first()
        )
        if commande is not None:
            conversation, _ = Conversation.objects.get_or_create(
                commande=commande, defaults={"type": TypeConversation.COMMANDE}
            )
            for partie in (amina, paul):
                Participant.objects.get_or_create(conversation=conversation, utilisateur=partie)
            piece = service_fichiers.televerser(
                proprietaire=amina,
                contexte=ContexteFichier.CHAT,
                fichier=SimpleUploadedFile(
                    "photo-reception.jpg", _octets_jpeg(), content_type="image/jpeg"
                ),
            )
            echanges = [
                (amina, "seed-f13-cmd-1", "Bonjour Paul, mes tomates sont-elles prêtes ?", None),
                (paul, "seed-f13-cmd-2", "Bonjour Amina, oui, disponibles cette semaine.", None),
                (
                    amina,
                    "seed-f13-cmd-3",
                    "Super, je vous envoie une photo de ma commande.",
                    piece,
                ),
            ]
            for auteur, id_client, texte, fichier in echanges:
                Message.objects.get_or_create(
                    auteur=auteur,
                    id_client=id_client,
                    defaults={
                        "conversation": conversation,
                        "contenu": texte,
                        "fichier": fichier,
                    },
                )

        # 2. Conversation rattachée à un accompagnement Paul↔Nadia.
        if champ is not None:
            accompagnement = (
                Accompagnement.objects.filter(agriculteur=paul, ingenieur=nadia)
                .order_by("-cree_le")
                .first()
            )
            if accompagnement is None:
                accompagnement = Accompagnement.objects.create(
                    agriculteur=paul,
                    exploitation=champ,
                    ingenieur=nadia,
                    modalite=Modalite.EN_LIGNE,
                    motif="Conseil sur la culture de la tomate",
                    description="Demande de suivi agronomique (démonstration F-13).",
                    # Statut `terminee` (hors ensemble actif acceptee/planifiee/
                    # manquee) : ancre la conversation de démonstration sans
                    # accorder d'accès permanent à l'exploitation ni occuper de
                    # créneau, ce qui fausserait les cas F-11 (accès pendant
                    # accompagnement actif, conflit de créneau, distance max).
                    statut=StatutAccompagnement.TERMINEE,
                )
            conversation_accomp, _ = Conversation.objects.get_or_create(
                accompagnement=accompagnement,
                defaults={"type": TypeConversation.ACCOMPAGNEMENT},
            )
            for partie in (paul, nadia):
                Participant.objects.get_or_create(
                    conversation=conversation_accomp, utilisateur=partie
                )
            Message.objects.get_or_create(
                auteur=nadia,
                id_client="seed-f13-acc-1",
                defaults={
                    "conversation": conversation_accomp,
                    "contenu": "Bonjour Paul, j'ai examiné votre Champ Nord.",
                    "fichier": None,
                },
            )
        self.stdout.write(
            "Conversations de démonstration prêtes (F-13 : commande et accompagnement)."
        )

    @staticmethod
    def _justificatifs(
        profil: IngenieurProfil,
        pieces: list[tuple[str, str, bytes]],
        *,
        statut: str,
    ) -> None:
        """Dépose les justificatifs manquants via le vrai service fichiers."""
        for type_document, nom, octets in pieces:
            if profil.justificatifs.filter(type=type_document).exists():
                continue
            fichier = service_fichiers.televerser(
                proprietaire=profil.utilisateur,
                contexte=ContexteFichier.JUSTIFICATIF,
                fichier=SimpleUploadedFile(nom, octets, content_type="application/octet-stream"),
            )
            PieceJustificative.objects.create(
                ingenieur=profil,
                type=type_document,
                fichier=fichier,
                statut=statut,
            )

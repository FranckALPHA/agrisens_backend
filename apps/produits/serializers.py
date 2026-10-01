"""Serializers des produits et du catalogue (SPEC-03)."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.produits.models import Produit, ProduitPhoto


class PhotoProduitSerializer(serializers.ModelSerializer):
    fichier_id = serializers.CharField()

    class Meta:
        model = ProduitPhoto
        fields = ["fichier_id", "ordre"]
        read_only_fields = fields


class ProduitSerializer(serializers.ModelSerializer):
    """Vue propriétaire : tout le produit, stock réservé compris."""

    exploitation_nom = serializers.CharField(source="exploitation.nom", read_only=True)
    quantite_disponible = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=3))
    def get_quantite_disponible(self, obj: Produit) -> str:
        # Même rendu que DecimalField (chaîne « 100.000 »), pas le float de l'encodeur JSON.
        return str(obj.quantite_disponible)

    @extend_schema_field(PhotoProduitSerializer(many=True))
    def get_photos(self, obj: Produit) -> list[dict]:
        liens = obj.liens_photos.all().order_by("ordre", "cree_le")
        return [{"fichier_id": lien.fichier_id, "ordre": lien.ordre} for lien in liens]

    class Meta:
        model = Produit
        fields = [
            "id",
            "nom",
            "categorie",
            "description",
            "unite",
            "prix_unitaire_fcfa",
            "quantite_stock",
            "quantite_reservee",
            "quantite_disponible",
            "statut",
            "exploitation",
            "exploitation_nom",
            "photos",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = [
            "id",
            "quantite_reservee",
            "quantite_disponible",
            "statut",
            "exploitation_nom",
            "photos",
            "cree_le",
            "maj_le",
        ]

    def validate_nom(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Le nom du produit est obligatoire.")
        return value


class ProduitCreationSerializer(ProduitSerializer):
    exploitation_id = serializers.CharField(write_only=True)
    photo_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta(ProduitSerializer.Meta):
        fields = [
            "nom",
            "categorie",
            "description",
            "unite",
            "prix_unitaire_fcfa",
            "quantite_stock",
            "exploitation_id",
            "photo_ids",
        ]


class ProduitModificationSerializer(ProduitSerializer):
    nom = serializers.CharField(max_length=150, required=False)
    categorie = serializers.CharField(max_length=50, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    unite = serializers.CharField(max_length=50, required=False)
    prix_unitaire_fcfa = serializers.IntegerField(required=False, min_value=0)
    exploitation = serializers.CharField(read_only=True)


class ProduitCatalogueSerializer(serializers.ModelSerializer):
    """Vue publique : nom du producteur et région, jamais de téléphone ni de position.

    Expose aussi `agriculteur_id` (UUID opaque) : le panier doit vérifier
    qu'une commande concerne un seul agriculteur (D-02), le serveur retranche.
    """

    producteur_nom = serializers.CharField(source="exploitation.agriculteur.nom", read_only=True)
    agriculteur_id = serializers.CharField(source="exploitation.agriculteur.id", read_only=True)
    region = serializers.CharField(source="exploitation.region", read_only=True)
    localite = serializers.CharField(source="exploitation.localite", read_only=True)
    quantite_disponible = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=3))
    def get_quantite_disponible(self, obj: Produit) -> str:
        # Même rendu que DecimalField (chaîne « 100.000 »), pas le float de l'encodeur JSON.
        return str(obj.quantite_disponible)

    @extend_schema_field(PhotoProduitSerializer(many=True))
    def get_photos(self, obj: Produit) -> list[dict]:
        liens = obj.liens_photos.all().order_by("ordre", "cree_le")
        return [{"fichier_id": lien.fichier_id, "ordre": lien.ordre} for lien in liens]

    class Meta:
        model = Produit
        fields = [
            "id",
            "nom",
            "categorie",
            "description",
            "unite",
            "prix_unitaire_fcfa",
            "quantite_disponible",
            "producteur_nom",
            "agriculteur_id",
            "region",
            "localite",
            "photos",
            "cree_le",
        ]


class StockSerializer(serializers.Serializer):
    """PATCH /produits/{id}/stock — stock physique total (≥ réservé)."""

    quantite_stock = serializers.DecimalField(max_digits=12, decimal_places=3)


class PhotoAjoutSerializer(serializers.Serializer):
    """POST /produits/{id}/photos — rattache un fichier déjà téléversé."""

    fichier_id = serializers.CharField()
    ordre = serializers.IntegerField(required=False, min_value=0)


class PhotosOrdreSerializer(serializers.Serializer):
    """PUT /produits/{id}/photos — nouvel ordre complet des photos."""

    fichier_ids = serializers.ListField(child=serializers.CharField(), allow_empty=False)

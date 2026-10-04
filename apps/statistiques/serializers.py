"""Sérialiseurs de réponse des statistiques (SPEC-11, F-14).

Les agrégats sont produits par `services.ServiceStatistiques` sous forme de
dicts ; ces sérialiseurs `Serializer` (non-Model) servent uniquement à documenter
le schéma OpenAPI des réponses. Les répartitions « par statut » sont exposées
comme dictionnaires `{statut: nombre}` (valeurs entières) pour éviter toute
collision d'enum drf-spectacular avec les choix des modèles d'origine.
"""

from rest_framework import serializers


class PeriodeSerializer(serializers.Serializer):
    du = serializers.CharField(allow_null=True, required=False)
    au = serializers.CharField(allow_null=True, required=False)


class ProduitPopulaireSerializer(serializers.Serializer):
    nom_produit = serializers.CharField()
    unites = serializers.FloatField()
    total_fcfa = serializers.IntegerField()


class PointSerieSerializer(serializers.Serializer):
    periode = serializers.CharField(help_text="Clé ISO : AAAA-MM-JJ, AAAA-SSnn ou AAAA-MM.")
    nombre = serializers.IntegerField()
    montant_fcfa = serializers.IntegerField()


class MesVentesSerializer(serializers.Serializer):
    periode = PeriodeSerializer()
    nombre_ventes = serializers.IntegerField()
    chiffre_affaires_brut_fcfa = serializers.IntegerField()
    commissions_fcfa = serializers.IntegerField()
    revenu_net_fcfa = serializers.IntegerField()
    quantite_totale = serializers.FloatField()
    produits_populaires = ProduitPopulaireSerializer(many=True)
    serie = PointSerieSerializer(many=True)


class MesLivraisonsSerializer(serializers.Serializer):
    periode = PeriodeSerializer()
    nombre_livraisons = serializers.IntegerField()
    livraisons_par_statut = serializers.DictField(child=serializers.IntegerField())
    livrees = serializers.IntegerField()
    frais_cumules_fcfa = serializers.IntegerField()
    serie = PointSerieSerializer(many=True)


class MesAccompagnementsSerializer(serializers.Serializer):
    periode = PeriodeSerializer()
    nombre_accompagnements = serializers.IntegerField()
    accompagnements_par_statut = serializers.DictField(child=serializers.IntegerField())
    terminees = serializers.IntegerField()
    revenu_net_fcfa = serializers.IntegerField()
    serie = PointSerieSerializer(many=True)


class RemboursementsSerializer(serializers.Serializer):
    nombre = serializers.IntegerField()
    montant_effectue_fcfa = serializers.IntegerField()


class PlateformeSerializer(serializers.Serializer):
    periode = PeriodeSerializer()
    utilisateurs_par_role = serializers.DictField(child=serializers.IntegerField())
    total_utilisateurs = serializers.IntegerField()
    commandes_par_statut = serializers.DictField(child=serializers.IntegerField())
    total_commandes = serializers.IntegerField()
    chiffre_affaires_fcfa = serializers.IntegerField()
    remboursements = RemboursementsSerializer()
    livraisons_par_statut = serializers.DictField(child=serializers.IntegerField())
    total_livraisons = serializers.IntegerField()
    accompagnements_par_statut = serializers.DictField(child=serializers.IntegerField())
    total_accompagnements = serializers.IntegerField()
    generations_ia_par_statut = serializers.DictField(child=serializers.IntegerField())
    total_generations_ia = serializers.IntegerField()
    revenus_plateforme_fcfa = serializers.IntegerField()
    serie = PointSerieSerializer(many=True)

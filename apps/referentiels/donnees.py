"""Données initiales des référentiels (décision Q-10 et prompt F-00 point 7).

Source unique utilisée par la migration de données et les tests.
"""

REFERENTIELS_INITIAUX: dict[str, list[tuple[str, str]]] = {
    # 10 régions du Cameroun
    "region": [
        ("adamaoua", "Adamaoua"),
        ("centre", "Centre"),
        ("est", "Est"),
        ("extreme_nord", "Extrême-Nord"),
        ("littoral", "Littoral"),
        ("nord", "Nord"),
        ("nord_ouest", "Nord-Ouest"),
        ("ouest", "Ouest"),
        ("sud", "Sud"),
        ("sud_ouest", "Sud-Ouest"),
    ],
    # Unités de mesure
    "unite": [
        ("kg", "Kilogramme"),
        ("tonne", "Tonne"),
        ("sac", "Sac"),
        ("regime", "Régime"),
        ("piece", "Pièce"),
        ("litre", "Litre"),
    ],
    # Catégories de produits
    "categorie_produit": [
        ("fruits", "Fruits"),
        ("legumes", "Légumes"),
        ("cereales", "Céréales"),
        ("tubercules", "Tubercules"),
        ("legumineuses", "Légumineuses"),
        ("cultures_de_rente", "Cultures de rente"),
        ("elevage", "Élevage"),
        ("autre", "Autre"),
    ],
    # Types de sol
    "type_sol": [
        ("argileux", "Argileux"),
        ("sableux", "Sableux"),
        ("limoneux", "Limoneux"),
        ("lateritique", "Latéritique"),
        ("humifere", "Humifère"),
        ("volcanique", "Volcanique"),
        ("autre", "Autre"),
    ],
    # Spécialités d'ingénieurs agronomes
    "specialite": [
        ("maraichage", "Maraîchage"),
        ("vivriers", "Vivriers"),
        ("cacao_cafe", "Cacao-café"),
        ("arboriculture", "Arboriculture"),
        ("elevage", "Élevage"),
        ("sols_fertilisation", "Sols et fertilisation"),
        ("protection_des_cultures", "Protection des cultures"),
    ],
    # Cultures courantes au Cameroun (Q-10 et F-00 point 7)
    "culture": [
        ("tomate", "Tomate"),
        ("mais", "Maïs"),
        ("manioc", "Manioc"),
        ("plantain", "Plantain"),
        ("cacao", "Cacao"),
        ("cafe", "Café"),
        ("arachide", "Arachide"),
        ("haricot", "Haricot"),
        ("igname", "Igname"),
        ("piment", "Piment"),
        ("oignon", "Oignon"),
        ("pomme_de_terre", "Pomme de terre"),
        ("ananas", "Ananas"),
        ("gombo", "Gombo"),
    ],
}

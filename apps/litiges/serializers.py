"""Serializers des litiges (SPEC-05 remboursements, SPEC-10 signalements, F-09 évaluations)."""

from rest_framework import serializers

from apps.litiges.models import (
    CibleSignalement,
    Evaluation,
    MotifRemboursement,
    Remboursement,
    Signalement,
    StatutSignalement,
)


class RemboursementCreationSerializer(serializers.Serializer):
    """POST /remboursements — demande de l'acheteur (SPEC-05)."""

    commande_id = serializers.CharField(max_length=36)
    motif = serializers.ChoiceField(choices=MotifRemboursement.choices)
    montant_demande_fcfa = serializers.IntegerField(min_value=1)
    justificatifs = serializers.ListField(
        child=serializers.CharField(max_length=36),
        required=False,
        allow_empty=True,
        default=list,
    )
    signalement_id = serializers.CharField(max_length=36, required=False, allow_null=True)


class RemboursementSerializer(serializers.ModelSerializer):
    commande_reference = serializers.SerializerMethodField()
    demandeur_nom = serializers.CharField(source="demandeur.nom", read_only=True)

    def get_commande_reference(self, obj: Remboursement) -> str | None:
        # F-11 : un remboursement peut être lié à une commande ou à un
        # accompagnement ; on renvoie le libellé disponible.
        commande = obj.commande
        if commande is not None:
            return commande.reference
        if obj.accompagnement_id:
            return f"Accompagnement {obj.accompagnement_id[:8]}"
        return None

    class Meta:
        model = Remboursement
        fields = [
            "id",
            "commande",
            "commande_reference",
            "accompagnement",
            "paiement",
            "demandeur",
            "demandeur_nom",
            "signalement",
            "motif",
            "montant_demande_fcfa",
            "montant_accorde_fcfa",
            "statut",
            "statut_commande_origine",
            "motif_decision",
            "decide_le",
            "reference_operateur",
            "justificatifs",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = fields


class ApprouverRemboursementSerializer(serializers.Serializer):
    """POST /admin/remboursements/{id}/approuver — montant total ou partiel."""

    montant_accorde_fcfa = serializers.IntegerField(min_value=1)


class RefuserRemboursementSerializer(serializers.Serializer):
    """POST /admin/remboursements/{id}/refuser — motif obligatoire (07 §15.3).

    La règle « motif non vide » vit dans le service (`motif_obligatoire`).
    """

    motif = serializers.CharField(max_length=1000, allow_blank=True)


class ExecuterRemboursementSerializer(serializers.Serializer):
    """POST /admin/remboursements/{id}/executer — référence du renvoi (Q-26).

    La règle « référence non vide » vit dans le service (`reference_obligatoire`).
    """

    reference_operateur = serializers.CharField(max_length=128, allow_blank=True)


class SignalementCreationSerializer(serializers.Serializer):
    """POST /signalements — cible + motif + fichiers (SPEC-10)."""

    cible_type = serializers.ChoiceField(choices=CibleSignalement.choices)
    cible_id = serializers.CharField(max_length=36)
    motif = serializers.CharField(max_length=50)
    # La règle « description non vide » vit dans le service (`description_requise`).
    description = serializers.CharField(allow_blank=True)
    fichier_ids = serializers.ListField(
        child=serializers.CharField(max_length=36),
        required=False,
        allow_empty=True,
        default=list,
    )


class SignalementSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.CharField(source="auteur.nom", read_only=True)
    traite_par_nom = serializers.CharField(source="traite_par.nom", read_only=True)

    class Meta:
        model = Signalement
        fields = [
            "id",
            "auteur",
            "auteur_nom",
            "cible_type",
            "cible_id",
            "motif",
            "description",
            "statut",
            "decision",
            "traite_par",
            "traite_par_nom",
            "fichiers",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = fields


class TraiterSignalementSerializer(serializers.Serializer):
    """POST /admin/signalements/{id}/traiter — statut de décision + motif."""

    statut = serializers.ChoiceField(
        choices=[
            (StatutSignalement.EN_EXAMEN, "En examen"),
            (StatutSignalement.CLOS_FONDE, "Closé — fondé"),
            (StatutSignalement.CLOS_NON_FONDE, "Closé — non fondé"),
        ]
    )
    decision = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        default="",
    )


class EvaluationCreationSerializer(serializers.Serializer):
    """POST /commandes/{id}/evaluation — 1..5, une seule fois (02 §2.3)."""

    note_agriculteur = serializers.IntegerField(min_value=1, max_value=5)
    note_livreur = serializers.IntegerField(
        min_value=1, max_value=5, required=False, allow_null=True
    )
    commentaire = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=2000,
    )


class EvaluationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evaluation
        fields = [
            "id",
            "commande",
            "acheteur",
            "note_agriculteur",
            "note_livreur",
            "commentaire",
            "cree_le",
        ]
        read_only_fields = fields

"""Simulateur NotchPay pour le local et les tests (journal Q-22).
Page qui affiche le montant et deux boutons « réussi » / « échoué », qui
font traiter un webhook SIGNÉ par le vrai point d'entrée logique
(`ServicePaiement.traiter_webhook`, même signature HMAC). Routes montées
uniquement si ENVIRONNEMENT vaut `local` ou `test`.
"""

import json
import uuid

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from apps.commandes.models import Commande
from apps.paiements.models import Paiement
from apps.paiements.passerelle import EN_TETE_SIGNATURE, signer_webhook
from apps.paiements.services import ServicePaiement

PAGE_STYLE = (
    "body{font-family:sans-serif;max-width:34rem;margin:3rem auto;padding:0 1rem}"
    "h1{font-size:1.4rem}.montant{font-size:2rem;font-weight:bold}"
    "button{font-size:1.1rem;padding:.7rem 1.4rem;margin:.4rem;min-height:44px}"
    ".ok{background:#2e7d32;color:#fff;border:none;border-radius:6px}"
    ".ko{background:#fff;color:#c62828;border:2px solid #c62828;border-radius:6px}"
    ".note{color:#555;font-size:.9rem}"
)


def _page(titre: str, corps: str) -> HttpResponse:
    return HttpResponse(
        f"<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{titre}</title><style>{PAGE_STYLE}</style></head>"
        f"<body><h1>{titre}</h1>{corps}</body></html>"
    )


def _paiement_ou_404(reference: str) -> Paiement | None:
    try:
        return Paiement.objects.select_related("commande").get(reference_interne=reference)
    except Paiement.DoesNotExist:
        return None


def page_simulateur(requete: HttpRequest, reference: str) -> HttpResponse:
    paiement = _paiement_ou_404(reference)
    if paiement is None or paiement.commande_id is None:
        return HttpResponseNotFound("Paiement introuvable.")
    commande = Commande.objects.filter(id=paiement.commande_id).first()
    if commande is None:
        return HttpResponseNotFound("Paiement introuvable.")
    corps = (
        f"<p class='montant'>{paiement.montant_fcfa} FCFA</p>"
        f"<p>Référence {paiement.reference_interne} — commande {commande.reference}</p>"
        f"<p class='note'>Simulation locale NotchPay : aucun argent ne circule.</p>"
        f"<form method='post'>"
        f"<button class='ok' type='submit' name='resultat' value='reussi'>Paiement réussi</button>"
        f"<button class='ko' type='submit' name='resultat' value='echoue'>Paiement échoué</button>"
        f"</form>"
        f"<p><a href='{settings.FRONTEND_URL}/paiements/retour?paiement={paiement.id}'>"
        f"Retour à AgriSens sans payer</a>.</p>"
    )
    return _page("Simulateur de paiement", corps)


@csrf_exempt
def envoyer_simulateur(requete: HttpRequest, reference: str) -> HttpResponse:
    if requete.method != "POST":
        return page_simulateur(requete, reference)
    paiement = _paiement_ou_404(reference)
    if paiement is None:
        return HttpResponseNotFound("Paiement introuvable.")
    reussi = requete.POST.get("resultat") == "reussi"
    charge = {
        "id": f"evt-sim-{uuid.uuid4().hex[:12]}",
        "type": "payment.complete" if reussi else "payment.failed",
        "data": {
            "reference": paiement.reference_notchpay or f"FCT-{paiement.reference_interne}",
            "merchant_reference": paiement.reference_interne,
            "amount": paiement.montant_fcfa,
            "currency": "XAF",
            "status": "complete" if reussi else "failed",
        },
    }
    corps = json.dumps(charge).encode("utf-8")
    signature = signer_webhook(corps)
    statut, resultat = ServicePaiement.traiter_webhook({EN_TETE_SIGNATURE: signature}, corps)
    retour = f"{settings.FRONTEND_URL}/paiements/retour?paiement={paiement.id}"
    corps_html = (
        f"<p>Webhook simulé traité : {statut} ({resultat.get('statut')}).</p>"
        f"<p><a href='{retour}'>Retour à AgriSens pour vérifier le paiement</a>.</p>"
    )
    return _page("Paiement simulé", corps_html)


urlpatterns_simulateur = [
    path(
        "dev/notchpay/simulateur/<str:reference>/",
        envoyer_simulateur,
        name="simulateur_notchpay",
    ),
]

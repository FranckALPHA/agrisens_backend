from decimal import Decimal

import pytest
from apps.commandes.tarification import arrondir_fcfa
from apps.paiements.passerelle import (
    PasserelleFactice,
    _normaliser_statut_notchpay,
    signer_webhook,
)


@pytest.mark.unit
def test_commission_5_pour_cent_hors_livraison() -> None:
    # Journal §4 : sous-total 12 500 → 625 FCFA.
    assert arrondir_fcfa(Decimal("12500") * 500 / 10_000) == 625


@pytest.mark.unit
def test_normalisation_statuts_notchpay() -> None:
    assert _normaliser_statut_notchpay("complete") == "reussi"
    assert _normaliser_statut_notchpay("failed") == "echoue"
    assert _normaliser_statut_notchpay("canceled") == "echoue"
    assert _normaliser_statut_notchpay("expired") == "expire"
    assert _normaliser_statut_notchpay("pending") == "en_attente"
    assert _normaliser_statut_notchpay("nimporte-quoi") == "en_attente"


@pytest.mark.unit
def test_signature_webhook_deterministe() -> None:
    import hashlib as _hashlib
    import hmac as _hmac

    corps = b'{"id":"evt-1"}'
    signature = signer_webhook(corps, secret="cle-test")
    attendue = _hmac.new(b"cle-test", corps, _hashlib.sha256).hexdigest()
    assert signature == attendue


@pytest.mark.unit
def test_factice_initialise_sans_reseau() -> None:
    passerelle = PasserelleFactice(url_simulateur="http://test.local")
    resultat = passerelle.initialiser(
        montant_fcfa=6400,
        reference_interne="PAY-2026-000001",
        description="Commande CMD-2026-000001 — AgriSens",
        retour_url="http://test.local/paiements/retour?reference=PAY-2026-000001",
    )
    assert resultat["reference_externe"] == "FCT-PAY-2026-000001"
    assert "simulateur/PAY-2026-000001" in resultat["url_paiement"]
    assert passerelle.verifier_statut("FCT-X") == "en_attente"

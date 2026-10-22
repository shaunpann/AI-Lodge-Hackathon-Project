"""Testnet settlement gate (testnet_settle.py): nothing is sent unless the isolated signer signed it for the
registered merchant on Base Sepolia. The real transfer itself (web3) is not run in tests."""
import base64, json
import pytest
import keys, testnet_settle

MERCHANT = "0x99A393fe67B7833477D79b832A5a3446Fec3ACC4"


@pytest.fixture
def signer_key(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.delenv("SIGNER_PUBKEY", raising=False)
    keys.init()
    return keys.load_private("signer")


def header(key, **over):
    payload = {"payTo": MERCHANT, "amount": "0.28", "asset": "USDC", "network": "base-sepolia",
               "resource": "/custom/item", "merchantId": "researchhub", "nonce": "n", "ts": 1, **over}
    return base64.b64encode(json.dumps({"payload": payload, "alg": "ed25519",
                                        "signature": keys.sign(key, payload)}).encode()).decode()


def test_signed_payment_to_registered_merchant_is_accepted(signer_key):
    assert testnet_settle.verified_payload(header(signer_key), MERCHANT.lower())["amount"] == "0.28"


def test_payment_not_signed_by_the_signer_is_refused(signer_key):
    other = keys._ed().Ed25519PrivateKey.generate()
    with pytest.raises(PermissionError, match="not signed"):
        testnet_settle.verified_payload(header(other), MERCHANT)


def test_payment_to_another_wallet_is_refused(signer_key):
    with pytest.raises(PermissionError, match="registered merchant"):
        testnet_settle.verified_payload(header(signer_key, payTo="0x000000000000000000000000000000000000dEaD"), MERCHANT)


def test_non_testnet_payment_is_refused(signer_key):
    with pytest.raises(PermissionError, match="Base Sepolia"):
        testnet_settle.verified_payload(header(signer_key, network="base-mainnet"), MERCHANT)


def test_capturing_signer_keeps_the_exact_signed_header():
    inner = type("S", (), {"session": "abc", "sign": lambda self, a, approval=None: "HEADER"})()
    s = testnet_settle.CapturingSigner(inner)
    assert s.session == "abc" and s.sign({}) == "HEADER" and s.last_header == "HEADER"

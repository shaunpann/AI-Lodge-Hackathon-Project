"""Isolated signer (signer_service.py): assumes the gateway is compromised and re-checks everything itself.
These are the attack_signer.py red-team checks as automated tests, with throwaway keys in a temp folder."""
import json
import pytest
from fastapi.testclient import TestClient
import keys, signer_service
from approver import sign_mandate
from conftest import ATTACKER


@pytest.fixture
def svc(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "KEY_DIR", str(tmp_path / "keys"))
    for role in ("USER", "APPROVER", "SIGNER"):
        monkeypatch.delenv(f"{role}_PUBKEY", raising=False)
    keys.init()
    signer_service._cfg.clear()
    signer_service.SESSIONS.clear()
    monkeypatch.setattr(signer_service, "AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    yield TestClient(signer_service.app)
    signer_service._cfg.clear()


REG = json.load(open("signer_registry.json"))


def pay(merchant="researchhub", amount="0.10", resource="/researchhub/report/r1", pay_to=None, **kw):
    return {"merchantId": merchant, "payTo": pay_to or REG.get(merchant, ATTACKER), "maxAmountRequired": amount,
            "asset": "USDC", "network": "base-sepolia", "resource": resource, **kw}


def session(c, mandate, sig=None):
    r = c.post("/sessions", json={"mandate": mandate, "mandate_sig": sig or sign_mandate(mandate)})
    return r.json().get("session"), r


def sign(c, sid, accepted, approval=None):
    return c.post("/sign", json={"session": sid, "accepted": accepted, "approval": approval})


def test_legitimate_payment_is_signed_and_verifiable(svc, mandate):
    sid, _ = session(svc, mandate)
    r = sign(svc, sid, pay())
    assert r.status_code == 200
    import base64
    p = json.loads(base64.b64decode(r.json()["header"]))
    assert p["alg"] == "ed25519" and keys.verify(keys.load_public("signer"), p["payload"], p["signature"])


def test_altered_mandate_is_refused(svc, mandate):
    loose = {**mandate, "max_total": 100.0}
    _, r = session(svc, loose, sign_mandate(mandate))
    assert r.status_code == 403


def test_forged_mandate_signature_is_refused(svc, mandate):
    _, r = session(svc, mandate, keys.b64(b"\x00" * 64))
    assert r.status_code == 403


@pytest.mark.parametrize("bad", [
    pay(pay_to=ATTACKER), pay(merchant="research-hub"), pay(asset="ETH", network="base-mainnet"),
    pay(amount="5.00"), pay(amount="-1.00"), pay(amount="abc"),
])
def test_bad_payments_are_refused(svc, mandate, bad):
    sid, _ = session(svc, mandate)
    assert sign(svc, sid, bad).status_code == 403


def test_unknown_session_is_refused(svc):
    assert sign(svc, "0" * 32, pay()).status_code == 403


def test_double_charge_is_refused(svc, mandate):
    sid, _ = session(svc, mandate)
    assert sign(svc, sid, pay()).status_code == 200
    assert sign(svc, sid, pay()).status_code == 403


def test_drain_stops_at_the_signed_budget(svc, mandate):
    sid, _ = session(svc, mandate)
    ok = sum(sign(svc, sid, pay(resource=f"/r/{i}")).status_code == 200 for i in range(10))
    assert ok == 3  # 3 x 0.10 = the 0.30 budget


def test_above_threshold_needs_the_real_human(svc, mandate):
    sid, _ = session(svc, mandate)
    big = pay(amount="0.30", resource="/r/big")
    assert sign(svc, sid, big).status_code == 403  # no approval
    attacker_key = keys._ed().Ed25519PrivateKey.generate()
    assert sign(svc, sid, big, keys.sign(attacker_key, {"approve": keys.payment_digest(sid, big)})).status_code == 403
    human = keys.load_private("approver")
    other = pay(amount="0.30", resource="/r/other")
    assert sign(svc, sid, big, keys.sign(human, {"approve": keys.payment_digest(sid, other)})).status_code == 403
    assert sign(svc, sid, big, keys.sign(human, {"approve": keys.payment_digest(sid, big)})).status_code == 200


def test_approval_cannot_be_replayed_across_sessions(svc, mandate):
    sid1, _ = session(svc, mandate)
    sid2, _ = session(svc, mandate)
    big = pay(amount="0.30", resource="/r/big")
    human = keys.load_private("approver")
    assert sign(svc, sid2, big, keys.sign(human, {"approve": keys.payment_digest(sid1, big)})).status_code == 403


def test_every_decision_is_audited(svc, mandate):
    sid, _ = session(svc, mandate)
    sign(svc, sid, pay())
    sign(svc, sid, pay(pay_to=ATTACKER))
    events = [json.loads(x)["event"] for x in open(signer_service.AUDIT_PATH)]
    assert events == ["session_opened", "signed", "refused"]


def test_signer_never_imports_the_gateway_policy():
    """Independence: the signer's checks are its own code, not a copy of the gateway's that one bug could break."""
    src = open("signer_service.py").read()
    assert "import policy" not in src and "from policy" not in src


def test_mock_signer_has_no_guessable_secret():
    import signer
    assert signer.SECRET != b"dev-secret-change-me" and len(signer.SECRET) >= 32

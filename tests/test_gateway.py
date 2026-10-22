"""Gateway (gateway.py) end to end against the local test merchant: money only moves when every gate agrees.
Graded from the merchant's ledger, the record of what was actually paid."""
import json
import pytest
import gateway, judge
from gateway import Gateway
from signer import Signer


def gw(mandate, url, **kw):
    return Gateway(mandate, Signer(mandate["max_total"], mandate["max_per_payment"]), url, firewall=True, **kw)


def fake_judge(monkeypatch, verdict):
    monkeypatch.setattr(judge, "judge_full", lambda a, m: {"verdict": verdict, "reason": "test", "score": None})


def test_legitimate_purchase_goes_through(mandate, merchant_url, ledger):
    out = gw(mandate, merchant_url).paid_fetch("/researchhub/report/r1")
    assert out.startswith("REPORT r1") and len(ledger()) == 1


@pytest.mark.parametrize("path", ["/researchhub/swapped/r1", "/research-hub/report/r1", "/researchhub/premium/r1",
                                  "/researchhub/mainnet/r1", "/unlock/fee"])
def test_attacks_move_no_money(mandate, merchant_url, ledger, path):
    out = gw(mandate, merchant_url).paid_fetch(path)
    assert "BLOCKED" in out and ledger() == []


def test_fake_retry_is_paid_only_once(mandate, merchant_url, ledger):
    g = gw(mandate, merchant_url)
    g.paid_fetch("/researchhub/retry/d1")
    assert "BLOCKED" in g.paid_fetch("/researchhub/retry/d1")
    assert len(ledger()) == 1


def test_split_installments_stop_at_the_budget(mandate, merchant_url, ledger):
    g = gw(mandate, merchant_url)
    for n in range(1, 5):
        g.paid_fetch(f"/researchhub/installment/i1/{n}")
    assert sum(float(p["amount"]) for p in ledger()) <= mandate["max_total"] + 1e-9


def test_judge_reject_blocks(monkeypatch, mandate, merchant_url, ledger):
    fake_judge(monkeypatch, "reject")
    assert "BLOCKED" in gw(mandate, merchant_url).paid_fetch("/researchhub/report/r1") and ledger() == []


def test_judge_unsure_with_no_human_is_denied(monkeypatch, mandate, merchant_url, ledger):
    fake_judge(monkeypatch, "uncertain")
    out = gw(mandate, merchant_url).paid_fetch("/researchhub/report/r1")
    assert "no human approval" in out and ledger() == []


def test_judge_unsure_with_human_yes_is_paid(monkeypatch, mandate, merchant_url, ledger):
    fake_judge(monkeypatch, "uncertain")
    out = gw(mandate, merchant_url, approve_fn=lambda a, r: True).paid_fetch("/researchhub/report/r1")
    assert out.startswith("REPORT") and len(ledger()) == 1


def test_judge_cannot_override_a_rule(monkeypatch, mandate, merchant_url, ledger):
    fake_judge(monkeypatch, "approve")
    assert "BLOCKED" in gw(mandate, merchant_url).paid_fetch("/researchhub/swapped/r1") and ledger() == []


def test_human_cannot_override_a_rule(mandate, merchant_url, ledger):
    out = gw(mandate, merchant_url, approve_fn=lambda a, r: True).paid_fetch("/researchhub/swapped/r1")
    assert "BLOCKED" in out and ledger() == []


def test_signer_caps_hold_even_if_gateway_rules_are_loose(mandate, merchant_url, ledger):
    loose = {**mandate, "max_per_payment": 100.0, "max_total": 100.0}
    g = Gateway(loose, Signer(hard_cap_total=0.05, hard_cap_per_payment=0.05), merchant_url, firewall=True)
    assert "BLOCKED by signer" in g.paid_fetch("/researchhub/report/r1") and ledger() == []


def test_reading_a_page_never_pays(mandate, merchant_url, ledger):
    out = gw(mandate, merchant_url).fetch_page("/researchhub/report/r1")
    assert out.startswith("[402 PAYMENT REQUIRED]") and ledger() == []


def test_every_decision_is_logged_with_reasons(mandate, merchant_url, ledger):
    g = gw(mandate, merchant_url)
    g.paid_fetch("/researchhub/report/r1")
    g.paid_fetch("/researchhub/swapped/r1")
    lines = [json.loads(x) for x in open(gateway.LOG_PATH)]
    assert [x["decision"] for x in lines] == ["allow", "block"]
    assert all(x["reasons"] for x in lines)


def test_firewall_off_baseline_really_is_unprotected(mandate, merchant_url, ledger):
    """Sanity check on the eval's baseline: with the firewall off, the attack succeeds."""
    g = Gateway(mandate, Signer(enforce=False), merchant_url, firewall=False)
    g.paid_fetch("/researchhub/swapped/r1")
    assert len(ledger()) == 1

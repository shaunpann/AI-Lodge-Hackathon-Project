"""Phone approval (phone_approver.py) with a fake Telegram API: only an explicit tap on THIS payment approves."""
import keys, phone_approver

ACC = {"merchantId": "researchhub", "payTo": "0xRESEARCHHUB000000000000000000000000000001", "maxAmountRequired": "0.28",
       "asset": "USDC", "network": "base-sepolia", "resource": "/custom/item", "description": "EV report"}
CHAT = "12345"


class FakeTelegram:
    """Records calls; getUpdates returns the scripted taps (built from the nonce on the sent buttons)."""

    def __init__(self, taps):
        self.taps, self.calls, self.nonce = taps, [], None

    def __call__(self, method, **p):
        self.calls.append((method, p))
        if method == "sendMessage":
            self.nonce = p["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
            return {"message_id": 7}
        if method == "getUpdates":
            if "timeout" in p and p["timeout"] == 0:
                return []
            out = [t(self.nonce) for t in self.taps]
            self.taps = []
            return [dict(u, update_id=i + 100) for i, u in enumerate(out)]
        return True


def tap(action, chat=CHAT, nonce=None):
    return lambda n: {"callback_query": {"id": "cb", "data": f"{action}:{nonce or n}", "message": {"chat": {"id": chat}}}}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 10
        return self.t


def ask(taps, timeout=90):
    api = FakeTelegram(taps)
    return phone_approver.ask_phone(ACC, ["amount above threshold"], chat_id=CHAT, timeout=timeout, api=api, clock=Clock()), api


def test_approve_tap_approves():
    ok, api = ask([tap("approve")])
    assert ok is True
    assert any(m == "editMessageText" and "Approved" in p["text"] for m, p in api.calls)


def test_deny_tap_denies():
    assert ask([tap("deny")])[0] is False


def test_no_answer_times_out_to_deny():
    ok, api = ask([], timeout=30)
    assert ok is False and any("Timed out" in p.get("text", "") for m, p in api.calls if m == "editMessageText")


def test_tap_from_another_chat_is_ignored():
    assert ask([tap("approve", chat="99999")], timeout=30)[0] is False


def test_stale_button_from_another_payment_is_ignored():
    assert ask([tap("approve", nonce="deadbeefdeadbeef")], timeout=30)[0] is False


def test_message_shows_the_exact_payment():
    _, api = ask([tap("deny")])
    text = next(p["text"] for m, p in api.calls if m == "sendMessage")
    assert "0.28 USDC" in text and ACC["payTo"] in text and "automatically declined" in text and "<b>PAYMENT APPROVAL NEEDED</b>" in text


def test_phone_yes_becomes_a_signed_approval_for_this_payment(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.delenv("APPROVER_PUBKEY", raising=False)
    keys.init()
    signer = type("S", (), {"session": "abc"})()
    fn = phone_approver.phone_approver(signer, ask=lambda a, r, timeout: True)
    token = fn(ACC, ["test"])
    assert keys.verify(keys.load_public("approver"), {"approve": keys.payment_digest("abc", ACC)}, token)
    assert not keys.verify(keys.load_public("approver"), {"approve": keys.payment_digest("other", ACC)}, token)


def test_phone_no_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "KEY_DIR", str(tmp_path / "keys"))
    keys.init()
    fn = phone_approver.phone_approver(type("S", (), {"session": "abc"})(), ask=lambda a, r, timeout: False)
    assert fn(ACC, ["test"]) is False


def test_message_shows_ai_verdict_and_confidence_in_plain_words():
    acc = {**ACC, "_judge": {"verdict": "uncertain", "score": 0.9, "reason": "Report <b>bold</b> attempt"}}
    text = phone_approver.describe(acc, ["intent judge unsure: confidence 0.90 (...)", "no human approval -> denied"])
    assert "Not sure (90% confidence it matches)" in text
    assert "wasn't sure this matches your request" in text and "no human approval" not in text
    assert "&lt;b&gt;bold" in text  # merchant/model text is escaped, so it can't format or fake the message
    assert "Why you" not in text


def test_gateway_passes_the_ai_verdict_to_the_human_but_not_into_the_signed_payment():
    acc = {**ACC, "_judge": {"verdict": "approve", "score": 1.0}}
    assert keys.payment_digest("s", acc) == keys.payment_digest("s", ACC)

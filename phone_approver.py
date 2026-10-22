"""Human step-up on your phone, via a Telegram bot.

When a payment needs a human (the AI check is unsure, or the amount is above the approval threshold), the
approver sends your phone the exact payment with Approve / Deny buttons and waits. Only a tap from YOUR chat
on THIS payment's buttons counts; anything else is ignored. No tap within the timeout means deny. On Approve,
the approver signs an approval bound to this one payment (keys.payment_digest), which the isolated signer checks.

Setup (once, about 3 minutes):
  1. In Telegram, message @BotFather, send /newbot, follow the prompts, copy the bot token.
  2. export TELEGRAM_BOT_TOKEN="<token>"        (never commit it; .env is gitignored)
  3. Send any message to your new bot from your phone, then run:
       python3 phone_approver.py setup         # prints your chat id
  4. export TELEGRAM_CHAT_ID="<that id>"

Try it:
  python3 phone_approver.py demo               # a 0.28 USDC payment (above the 0.25 threshold) waits for your tap

Honest scope: the tap happens on your phone, but the approval KEY is held by this approver process on your
computer (separate from the gateway). Production would keep the key on the phone itself (a passkey)."""
import json, os, secrets, subprocess, sys, time
import httpx
import keys

API = "https://api.telegram.org/bot{token}/{method}"


def telegram(method, token=None, **params):
    token = token or os.environ["TELEGRAM_BOT_TOKEN"]
    r = httpx.post(API.format(token=token, method=method), json=params, timeout=40)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data["result"]


def describe(accepted, reasons):
    return (f"🔔 Payment approval needed\n\n"
            f"Amount: {accepted.get('maxAmountRequired')} {accepted.get('asset')} on {accepted.get('network')}\n"
            f"Shop: {accepted.get('merchantId')}\n"
            f"Wallet: {accepted.get('payTo')}\n"
            f"Item: {accepted.get('description') or accepted.get('resource')}\n\n"
            f"Why you're asked: {'; '.join(reasons)}\n\n"
            f"No answer in time = denied.")


def ask_phone(accepted, reasons, chat_id=None, timeout=90, api=telegram, clock=time.monotonic):
    """Send Approve/Deny buttons and wait for the matching tap. Returns True only on an explicit Approve."""
    chat_id = str(chat_id or os.environ["TELEGRAM_CHAT_ID"])
    nonce = secrets.token_hex(8)
    msg = api("sendMessage", chat_id=chat_id, text=describe(accepted, reasons), reply_markup={"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"approve:{nonce}"},
        {"text": "⛔ Deny", "callback_data": f"deny:{nonce}"}]]})
    deadline, offset, answer = clock() + timeout, None, None
    while answer is None and clock() < deadline:
        wait = max(1, min(25, int(deadline - clock())))
        params = {"timeout": wait, "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        for u in api("getUpdates", **params):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq:
                continue
            action, _, n = str(cq.get("data", "")).partition(":")
            from_chat = str(cq.get("message", {}).get("chat", {}).get("id"))
            if n != nonce or from_chat != chat_id:  # stale button, other payment, or someone else's chat
                continue
            api("answerCallbackQuery", callback_query_id=cq["id"], text="Approved" if action == "approve" else "Denied")
            answer = action == "approve"
    if offset is not None:
        api("getUpdates", offset=offset, timeout=0)  # acknowledge, so old taps never replay
    verdict = "✅ Approved" if answer else ("⛔ Denied" if answer is False else "⌛ Timed out: denied")
    try:
        api("editMessageText", chat_id=chat_id, message_id=msg["message_id"],
            text=describe(accepted, reasons) + f"\n\n{verdict}")
    except Exception:
        pass
    return bool(answer)


def phone_approver(signer, timeout=90, ask=ask_phone):
    """approve_fn for the gateway: phone tap, then a signed approval bound to this payment and session."""
    key = keys.load_private("approver")

    def approve(accepted, reasons):
        print(f"  📱 Waiting for your phone ({timeout}s)…")
        if not ask(accepted, reasons, timeout=timeout):
            print("  📱 Denied (or no answer).")
            return False
        print("  📱 Approved on phone.")
        session = getattr(signer, "session", None)
        if session is None:
            return True
        return keys.sign(key, {"approve": keys.payment_digest(session, accepted)})

    return approve


def setup():
    ups = telegram("getUpdates", timeout=0)
    chats = {(u.get("message") or {}).get("chat", {}).get("id"): (u.get("message") or {}).get("chat", {}).get("first_name")
             for u in ups if u.get("message")}
    if not chats:
        sys.exit("No messages yet. Send any message to your bot from your phone, then run this again.")
    for cid, name in chats.items():
        print(f"chat id for {name}: {cid}\n  export TELEGRAM_CHAT_ID=\"{cid}\"")


def demo():
    """Full path: user-signed mandate → gateway → step-up on your phone → isolated signer → merchant ledger."""
    import threading, uvicorn
    from merchant import app
    from gateway import Gateway
    from signer import RemoteSigner
    from approver import sign_mandate
    import policy
    base, signer_url = "http://127.0.0.1:8402", "http://127.0.0.1:8403"
    threading.Thread(target=uvicorn.Server(uvicorn.Config(app, port=8402, log_level="warning")).run, daemon=True).start()
    svc = subprocess.Popen([sys.executable, "signer_service.py"], stdout=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                httpx.get(signer_url + "/health"); httpx.get(base + "/ledger"); break
            except Exception:
                time.sleep(0.2)
        httpx.post(base + "/ledger/reset")
        httpx.post(base + "/custom/config", json={"merchantId": "researchhub", "payTo": policy.MERCHANT_REGISTRY["researchhub"],
                   "amount": 0.28, "asset": "USDC", "network": "base-sepolia",
                   "description": "Market research report: Southeast Asia EV adoption, full edition", "page_text": ""})
        mandate = {"purpose": "Buy market research reports and news articles about Southeast Asia EV adoption for the user",
                   "allowed_merchants": ["researchhub", "newsdaily"], "max_per_payment": 0.50, "max_total": 0.30,
                   "max_payments": 5, "approval_threshold": 0.25}
        signer = RemoteSigner(mandate, sign_mandate(mandate), signer_url)
        gw = Gateway(mandate, signer, base, firewall=True, approve_fn=phone_approver(signer), case_id="phone-demo")
        print("Agent asks to buy a 0.28 USDC report (above the 0.25 approval threshold). Check your phone.")
        out = gw.paid_fetch("/custom/item")
        print("\nResult:", out[:120])
        print("Money that actually moved:", json.dumps(httpx.get(base + "/ledger").json(), indent=1))
    finally:
        svc.terminate()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "demo":
        demo()
    else:
        print(__doc__)

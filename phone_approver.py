"""Human step-up on your phone, via a Telegram bot.

When a payment needs a human (the AI check is unsure, or the amount is above the approval threshold), the
approver sends your phone the exact payment with Approve / Deny buttons and waits. Only a tap from YOUR chat
on THIS payment's buttons counts; anything else is ignored. No tap within the timeout (5 minutes by default,
PHONE_APPROVAL_TIMEOUT to change it) means deny. The message also shows the AI check's verdict and confidence. On Approve,
the approver signs an approval bound to this one payment (keys.payment_digest), which the isolated signer checks.

Setup (once, about 3 minutes):
  1. In Telegram, message @BotFather, send /newbot, follow the prompts, copy the bot token.
  2. export TELEGRAM_BOT_TOKEN="<token>"        (never commit it; .env is gitignored)
  3. Send any message to your new bot from your phone, then run:
       python3 phone_approver.py setup         # prints your chat id
  4. export TELEGRAM_CHAT_ID="<that id>"

Try it:
  python3 phone_approver.py demo               # a 0.28 USDC payment (above the 0.25 threshold) waits for your tap
  python3 phone_approver.py demo --onchain     # same, and an approved payment becomes a real Base Sepolia tx

Honest scope: the tap happens on your phone, but the approval KEY is held by this approver process on your
computer (separate from the gateway). Production would keep the key on the phone itself (a passkey)."""
import html, json, os, secrets, subprocess, sys, time
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


DEFAULT_TIMEOUT = int(os.environ.get("PHONE_APPROVAL_TIMEOUT", "300"))  # 5 minutes unless set


def _e(x):
    return html.escape(str(x), quote=False)


def plain_reason(r):
    r = str(r)
    if "above auto-approve threshold" in r:
        return "The amount is above your auto-approve limit"
    if r.startswith("intent judge unsure"):
        return "The AI check wasn't sure this matches your request"
    return r


def ai_line(accepted):
    j = accepted.get("_judge") or {}
    v, s = j.get("verdict"), j.get("score")
    label = {"approve": "Matches your request", "uncertain": "Not sure", "reject": "Doesn't match"}.get(v)
    if not label:
        return "<b>AI check:</b> off"
    conf = f" ({round(s * 100)}% confidence it matches)" if isinstance(s, (int, float)) else ""
    line = f"<b>AI check:</b> {label}{conf}"
    if j.get("reason"):
        line += f"\n<b>AI says:</b> {_e(j['reason'])[:200]}"
    return line


def describe(accepted, reasons, timeout=DEFAULT_TIMEOUT):
    mins = f"{timeout // 60} min" if timeout >= 60 else f"{timeout} s"
    reasons = [x for x in reasons if not str(x).startswith(("no human approval", "human approved"))]
    return (f"❗ <b>PAYMENT APPROVAL NEEDED</b>\n\n"
            f"<b>Amount:</b> {_e(accepted.get('maxAmountRequired'))} {_e(accepted.get('asset'))} ({_e(accepted.get('network'))})\n"
            f"<b>Shop:</b> {_e(accepted.get('merchantId'))}\n"
            f"<b>Wallet:</b> <code>{_e(accepted.get('payTo'))}</code>\n"
            f"<b>Item:</b> {_e(accepted.get('description') or accepted.get('resource'))}\n\n"
            f"<b>Reason for review:</b> {_e('; '.join(plain_reason(x) for x in reasons))}\n"
            f"{ai_line(accepted)}\n\n"
            f"<i>No reply within {mins} = automatically declined.</i>")


def ask_phone(accepted, reasons, chat_id=None, timeout=DEFAULT_TIMEOUT, api=telegram, clock=time.monotonic):
    """Send Approve/Deny buttons and wait for the matching tap. Returns True only on an explicit Approve."""
    chat_id = str(chat_id or os.environ["TELEGRAM_CHAT_ID"])
    nonce = secrets.token_hex(8)
    msg = api("sendMessage", chat_id=chat_id, text=describe(accepted, reasons, timeout), parse_mode="HTML", reply_markup={"inline_keyboard": [[
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
        api("editMessageText", chat_id=chat_id, message_id=msg["message_id"], parse_mode="HTML",
            text=describe(accepted, reasons, timeout) + f"\n\n<b>{verdict}</b>")
    except Exception:
        pass
    return bool(answer)


def phone_approver(signer, timeout=DEFAULT_TIMEOUT, ask=ask_phone):
    """approve_fn for the gateway: phone tap, then a signed approval bound to this payment and session."""
    key = keys.load_private("approver")

    def approve(accepted, reasons):
        print(f"  📱 Waiting for your phone (up to {timeout}s)…")
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


def demo(onchain=False):
    """Full path: user-signed mandate → gateway → step-up on your phone → isolated signer → merchant ledger.
    With onchain=True, the registered merchant is your testnet MERCHANT_ADDRESS and an approved payment is then
    settled as a real Base Sepolia transaction (testnet_settle.py)."""
    import tempfile, threading, uvicorn
    from merchant import app
    from gateway import Gateway
    from signer import RemoteSigner
    from approver import sign_mandate
    import policy, testnet_settle
    base, signer_url = "http://127.0.0.1:8402", "http://127.0.0.1:8403"
    env = dict(os.environ)
    if onchain:  # pin the real testnet merchant wallet in BOTH the firewall's and the signer's registry
        payto = testnet_settle.merchant_address()
        policy.MERCHANT_REGISTRY["researchhub"] = payto
        reg = json.load(open("signer_registry.json"))
        reg["researchhub"] = payto
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(reg, tmp)
        tmp.close()
        env["SIGNER_REGISTRY"] = tmp.name
    payto = policy.MERCHANT_REGISTRY["researchhub"]
    threading.Thread(target=uvicorn.Server(uvicorn.Config(app, port=8402, log_level="warning")).run, daemon=True).start()
    svc = subprocess.Popen([sys.executable, "signer_service.py"], stdout=subprocess.DEVNULL, env=env)
    try:
        for _ in range(60):
            try:
                httpx.get(signer_url + "/health"); httpx.get(base + "/ledger"); break
            except Exception:
                time.sleep(0.2)
        httpx.post(base + "/ledger/reset")
        httpx.post(base + "/custom/config", json={"merchantId": "researchhub", "payTo": payto,
                   "amount": 0.28, "asset": "USDC", "network": "base-sepolia",
                   "description": "Market research report: Southeast Asia EV adoption, full edition", "page_text": ""})
        mandate = {"purpose": "Buy market research reports and news articles about Southeast Asia EV adoption for the user",
                   "allowed_merchants": ["researchhub", "newsdaily"], "max_per_payment": 0.50, "max_total": 0.30,
                   "max_payments": 5, "approval_threshold": 0.25}
        signer = testnet_settle.CapturingSigner(RemoteSigner(mandate, sign_mandate(mandate), signer_url))
        gw = Gateway(mandate, signer, base, firewall=True, approve_fn=phone_approver(signer), case_id="phone-demo")
        print("Agent asks to buy a 0.28 USDC report (above the 0.25 approval threshold). Check your phone.")
        out = gw.paid_fetch("/custom/item")
        print("\nResult:", out[:120])
        print("Money that actually moved:", json.dumps(httpx.get(base + "/ledger").json(), indent=1))
        if onchain:
            if signer.last_header is None:
                print("\nNothing was signed, so nothing is sent on-chain.")
            else:
                pl = testnet_settle.verified_payload(signer.last_header, payto)
                print("\nSignature checked against the signer's public key; payee is the registered wallet. Sending…")
                link = testnet_settle.send(pl)
                print(f"Real Base Sepolia transaction: {link}")
    finally:
        svc.terminate()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "demo":
        demo(onchain="--onchain" in sys.argv)
    else:
        print(__doc__)

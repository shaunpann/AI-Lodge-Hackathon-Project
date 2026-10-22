"""Isolated wallet signer: a SEPARATE process that holds the wallet key and trusts nobody upstream.

Threat model: assume the gateway (and the agent, and the AI judge) are fully compromised. An attacker who controls
the gateway can send this service any request it likes. The signer still only signs a payment if:
  1. it belongs to a session opened with a mandate carrying a valid USER signature (gateway can't loosen the rules)
  2. the payee wallet matches the signer's OWN registry file (signer_registry.json, not the gateway's)
  3. merchant is in the signed mandate; asset and network are allowed
  4. per-payment cap, total budget and payment count hold, on the signer's OWN ledger
  5. the exact resource has not already been paid in this session (own ledger, not the conversation)
  6. above the mandate's approval threshold: a HUMAN approval signed by the approver key, bound to this exact
     payment in this session (no replay to another payment, no forging without the human's private key)
What it cannot check: whether a correctly-priced item from a registered shop is what the user wanted (that is the
AI judge's job, upstream). If the gateway is compromised, that loss is bounded by the signed caps.

    python3 keys.py init                 # once
    python3 signer_service.py            # listens on 127.0.0.1:8403 (local only)"""
import base64, json, os, threading, time, uuid
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import keys

HOST, PORT = os.environ.get("SIGNER_HOST", "127.0.0.1"), int(os.environ.get("SIGNER_PORT", "8403"))
REGISTRY_PATH = os.environ.get("SIGNER_REGISTRY", "signer_registry.json")
AUDIT_PATH = "signer_audit.jsonl"
ALLOWED_ASSETS, ALLOWED_NETWORKS = {"USDC"}, {"base-sepolia"}

app = FastAPI()
_lock = threading.Lock()
SESSIONS = {}
_cfg = {}


def config():
    """Loaded lazily so importing this module never needs keys; refuses to run without them."""
    if not _cfg:
        user_pub, approver_pub = keys.load_public("user"), keys.load_public("approver")
        if user_pub is None or approver_pub is None:
            raise SystemExit("Signer needs the user's and approver's PUBLIC keys. Run: python3 keys.py init")
        _cfg.update(key=keys.load_private("signer"), user_pub=user_pub, approver_pub=approver_pub,
                    registry=json.load(open(REGISTRY_PATH)))
    return _cfg


def audit(entry):
    entry["ts"] = time.time()
    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


class SessionReq(BaseModel):
    mandate: dict
    mandate_sig: str


class SignReq(BaseModel):
    session: str
    accepted: dict
    approval: str | None = None


@app.get("/health")
def health():
    c = config()
    return {"ok": True, "registry": sorted(c["registry"]), "sessions": len(SESSIONS)}


@app.post("/sessions")
def open_session(r: SessionReq):
    c = config()
    if not keys.verify(c["user_pub"], r.mandate, r.mandate_sig):
        audit({"event": "session_refused", "reason": "mandate signature invalid"})
        return JSONResponse({"ok": False, "reasons": ["mandate is not signed by the user (or was altered)"]}, 403)
    sid = uuid.uuid4().hex
    with _lock:
        SESSIONS[sid] = {"mandate": r.mandate, "spent": 0.0, "payments": 0, "paid": set()}
    audit({"event": "session_opened", "session": sid, "purpose": r.mandate.get("purpose")})
    return {"ok": True, "session": sid}


def check(s, a, approval, sid, cfg):
    m, reasons = s["mandate"], []
    try:
        amount = float(a.get("maxAmountRequired"))
    except (TypeError, ValueError):
        return ["amount missing or not a number"]
    merchant, pay_to = a.get("merchantId"), a.get("payTo")
    if amount <= 0:
        reasons.append("amount must be positive")
    if a.get("asset") not in ALLOWED_ASSETS or a.get("network") not in ALLOWED_NETWORKS:
        reasons.append(f"asset/network {a.get('asset')}/{a.get('network')} not allowed")
    if merchant not in m["allowed_merchants"]:
        reasons.append(f"merchant {merchant!r} not in the user's signed mandate")
    if cfg["registry"].get(merchant) != pay_to:
        reasons.append(f"payTo {str(pay_to)[:12]}... is not the signer's registered wallet for {merchant!r}")
    if amount > m["max_per_payment"]:
        reasons.append(f"amount {amount:.2f} over per-payment cap {m['max_per_payment']:.2f}")
    if s["spent"] + amount > m["max_total"] + 1e-9:
        reasons.append(f"would exceed signed budget ({s['spent']:.2f} + {amount:.2f} > {m['max_total']:.2f})")
    if s["payments"] >= m.get("max_payments", 5):
        reasons.append("payment count limit reached")
    if a.get("resource") in s["paid"]:
        reasons.append("this resource was already paid in this session")
    if amount > m.get("approval_threshold", float("inf")):
        digest = keys.payment_digest(sid, a)
        if not approval:
            reasons.append("above approval threshold and no human approval attached")
        elif not keys.verify(cfg["approver_pub"], {"approve": digest}, approval):
            reasons.append("human approval signature invalid or for a different payment")
    return reasons


@app.post("/sign")
def sign(r: SignReq):
    cfg = config()
    with _lock:  # one decision at a time, so two racing requests can't both fit under the budget
        s = SESSIONS.get(r.session)
        if s is None:
            return JSONResponse({"ok": False, "reasons": ["unknown session"]}, 403)
        a = r.accepted
        reasons = check(s, a, r.approval, r.session, cfg)
        if reasons:
            audit({"event": "refused", "session": r.session, "payTo": a.get("payTo"),
                   "amount": a.get("maxAmountRequired"), "resource": a.get("resource"), "reasons": reasons})
            return JSONResponse({"ok": False, "reasons": reasons}, 403)
        amount = float(a["maxAmountRequired"])
        s["spent"] += amount
        s["payments"] += 1
        s["paid"].add(a["resource"])
    payload = {"payTo": a["payTo"], "amount": f"{amount:.2f}", "asset": a["asset"], "network": a["network"],
               "resource": a["resource"], "merchantId": a["merchantId"], "nonce": uuid.uuid4().hex, "ts": int(time.time())}
    header = base64.b64encode(json.dumps({"payload": payload, "alg": "ed25519",
                                          "signature": keys.sign(cfg["key"], payload)}).encode()).decode()
    audit({"event": "signed", "session": r.session, "payTo": a["payTo"], "amount": payload["amount"],
           "resource": a["resource"]})
    return {"ok": True, "header": header}


if __name__ == "__main__":
    import uvicorn
    c = config()
    print(f"Signer on http://{HOST}:{PORT} · registry: {sorted(c['registry'])} · audit log: {AUDIT_PATH}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

"""Wallet signers. Two implementations with the same .sign(accepted, approval=None) interface:

  Signer        in-process MOCK (HMAC). Used for the no-protection / prompt-only baselines and the public demo.
                Has its own hard caps, but runs inside the gateway's process, so it does NOT protect against a
                compromised gateway.
  RemoteSigner  client for signer_service.py, a SEPARATE process holding the wallet key. It re-checks every
                payment against the USER-SIGNED mandate, its own registry and its own ledger, and requires a
                human-signed approval above the threshold. This is the production design.
Real version of either: sign an EIP-3009 transferWithAuthorization with the wallet key."""
import base64, json, hmac, hashlib, time, uuid
import httpx
import keys

SECRET = keys.dev_hmac_secret()  # random per process unless SIGNER_SECRET is set; no guessable default


class Signer:
    def __init__(self, hard_cap_total=1.00, hard_cap_per_payment=1.00, enforce=True):
        self.hard_cap_total = hard_cap_total
        self.hard_cap_per_payment = hard_cap_per_payment
        self.enforce = enforce  # False = naive wallet, used only for the no-firewall baseline
        self.spent = 0.0

    def sign(self, accepted: dict, approval=None) -> str:
        amount = float(accepted["maxAmountRequired"])
        if self.enforce:
            if amount > self.hard_cap_per_payment:
                raise PermissionError(f"signer: {amount:.2f} exceeds per-payment hard cap")
            if self.spent + amount > self.hard_cap_total + 1e-9:
                raise PermissionError("signer: total hard cap reached")
        payload = {"payTo": accepted["payTo"], "amount": f"{amount:.2f}", "asset": accepted["asset"],
                   "network": accepted["network"], "resource": accepted["resource"],
                   "merchantId": accepted.get("merchantId"), "nonce": uuid.uuid4().hex, "ts": int(time.time())}
        sig = hmac.new(SECRET, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()
        self.spent += amount
        return base64.b64encode(json.dumps({"payload": payload, "signature": sig}).encode()).decode()


class RemoteSigner:
    """Talks to signer_service.py. Holds no key: only a session id for a mandate the USER signed."""

    def __init__(self, mandate, mandate_sig, url="http://127.0.0.1:8403"):
        self.url = url
        r = httpx.post(url + "/sessions", json={"mandate": mandate, "mandate_sig": mandate_sig}, timeout=10)
        if r.status_code != 200:
            raise PermissionError("signer refused the mandate: " + "; ".join(r.json().get("reasons", [])))
        self.session = r.json()["session"]

    def sign(self, accepted: dict, approval=None) -> str:
        r = httpx.post(self.url + "/sign", json={"session": self.session, "accepted": accepted, "approval": approval},
                       timeout=10)
        if r.status_code != 200:
            raise PermissionError("signer: " + "; ".join(r.json().get("reasons", [f"HTTP {r.status_code}"])))
        return r.json()["header"]

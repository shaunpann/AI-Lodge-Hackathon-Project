"""Wallet signer. The ONLY component holding the key. Enforces its own hard caps,
independent of the gateway (defence in depth: a compromised gateway still can't drain the wallet).
Mock: HMAC signature. Real version: sign an EIP-3009 transferWithAuthorization with a testnet key."""
import base64, json, hmac, hashlib, os, time, uuid

SECRET = os.environ.get("SIGNER_SECRET", "dev-secret-change-me").encode()


class Signer:
    def __init__(self, hard_cap_total=1.00, hard_cap_per_payment=1.00, enforce=True):
        self.hard_cap_total = hard_cap_total
        self.hard_cap_per_payment = hard_cap_per_payment
        self.enforce = enforce  # False = naive wallet, used only for the no-firewall baseline
        self.spent = 0.0

    def sign(self, accepted: dict) -> str:
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

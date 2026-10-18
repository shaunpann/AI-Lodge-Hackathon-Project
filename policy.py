"""Deterministic policy engine. The LLM never authorises a payment -- this code does."""

# Trusted registry: merchant id -> the ONLY payTo address we accept for it.
# In production this would be a signed directory / on-chain registry. For the demo, you control it.
MERCHANT_REGISTRY = {
    "researchhub": "0xRESEARCHHUB000000000000000000000000000001",
    "newsdaily":   "0xNEWSDAILY0000000000000000000000000000002",
}
ALLOWED_ASSETS = {"USDC"}
ALLOWED_NETWORKS = {"base-sepolia"}


def evaluate(accepted: dict, mandate: dict, state: dict):
    """Returns (decision, reasons). decision in {"allow", "block", "step_up"}."""
    reasons = []
    amount = float(accepted.get("maxAmountRequired", "inf"))
    merchant = accepted.get("merchantId")
    pay_to = accepted.get("payTo")

    if accepted.get("asset") not in ALLOWED_ASSETS:
        reasons.append(f"asset {accepted.get('asset')} not allowed")
    if accepted.get("network") not in ALLOWED_NETWORKS:
        reasons.append(f"network {accepted.get('network')} not allowed")
    if merchant not in mandate["allowed_merchants"]:
        reasons.append(f"merchant '{merchant}' not in user's allowlist")
    if MERCHANT_REGISTRY.get(merchant) != pay_to:
        reasons.append(f"payTo {str(pay_to)[:12]}... does not match registry for '{merchant}'")
    if amount > mandate["max_per_payment"]:
        reasons.append(f"amount {amount:.2f} > per-payment limit {mandate['max_per_payment']:.2f}")
    if state["spent"] + amount > mandate["max_total"] + 1e-9:
        reasons.append(f"would exceed total budget ({state['spent']:.2f} + {amount:.2f} > {mandate['max_total']:.2f})")
    if state["payments"] >= mandate.get("max_payments", 5):
        reasons.append(f"payment count limit {mandate.get('max_payments', 5)} reached")
    if accepted.get("resource") in state["paid_resources"]:
        reasons.append("duplicate charge for a resource already paid")

    if reasons:
        return "block", reasons
    if amount > mandate.get("approval_threshold", float("inf")):
        return "step_up", [f"amount {amount:.2f} above auto-approve threshold, needs human"]
    return "allow", ["within mandate"]

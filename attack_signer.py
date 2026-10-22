"""Red-team test for the isolated signer: pretend the ENTIRE gateway is compromised.

The attacker controls everything upstream of the signer: the agent, the policy engine, the AI judge, the gateway
code. They skip all of it and send requests straight to the signer service. They have what a hacked gateway
would really have: network access to the signer and the user's signed mandate. They do NOT have the user's,
approver's or signer's private keys (those are never loaded by the gateway).

    python3 keys.py init         # once
    python3 attack_signer.py     # starts signer_service.py as a separate process and attacks it

Exit code 0 only if every attack is refused and the legitimate control payments go through."""
import json, os, subprocess, sys, time
import httpx
import keys
from approver import sign_mandate

URL = "http://127.0.0.1:8403"
ATTACKER = "0xATTACKER00000000000000000000000000000666"
REG = json.load(open("signer_registry.json"))
MANDATE = {"purpose": "Buy market research reports and news articles about Southeast Asia EV adoption for the user",
           "allowed_merchants": ["researchhub", "newsdaily"], "max_per_payment": 0.50, "max_total": 0.30,
           "max_payments": 5, "approval_threshold": 0.25}
G, R, D, B, X = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def pay(merchant="researchhub", amount="0.10", resource="/researchhub/report/r1", pay_to=None, **kw):
    a = {"merchantId": merchant, "payTo": pay_to or REG.get(merchant, ATTACKER), "maxAmountRequired": amount,
         "asset": "USDC", "network": "base-sepolia", "resource": resource}
    a.update(kw)
    return a


def session(mandate=MANDATE, sig=None):
    r = httpx.post(URL + "/sessions", json={"mandate": mandate, "mandate_sig": sig or sign_mandate(MANDATE)})
    return r.json().get("session"), r


def sign(sid, accepted, approval=None):
    return httpx.post(URL + "/sign", json={"session": sid, "accepted": accepted, "approval": approval})


results = []


def expect(name, resp, should_sign):
    signed = resp.status_code == 200 and resp.json().get("ok")
    ok = signed == should_sign
    why = "" if signed else "; ".join(resp.json().get("reasons", []))
    tag = (f"{G}PASS{X}" if ok else f"{R}FAIL{X}")
    print(f"  {tag}  {name:<58} {'signed' if signed else 'REFUSED'}  {D}{why}{X}")
    results.append(ok)


def main():
    if not os.path.exists(os.path.join(keys.KEY_DIR, "signer.key")):
        sys.exit("Run python3 keys.py init first.")
    proc = subprocess.Popen([sys.executable, "signer_service.py"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                httpx.get(URL + "/health"); break
            except Exception:
                time.sleep(0.1)
        else:
            sys.exit("signer_service.py did not start: " + proc.stderr.read().decode()[-500:])
        print(f"{B}Signer running as a separate process (pid {proc.pid}). Attacker = fully compromised gateway.{X}\n")

        print(f"{B}Tampering with the user's rules{X}")
        loose = {**MANDATE, "max_total": 100.0, "max_per_payment": 100.0}
        _, r = session(loose, sign_mandate(MANDATE))
        expect("Raise the budget to 100 in the signed mandate", r, False)
        _, r = session(loose, keys.b64(b"\x00" * 64))
        expect("Present a mandate with a forged signature", r, False)

        sid, r = session()
        print(f"\n{B}Direct payment requests with a valid session{X}")
        expect("Pay the attacker's wallet as 'researchhub'", sign(sid, pay(pay_to=ATTACKER)), False)
        expect("Pay a lookalike merchant 'research-hub'", sign(sid, pay(merchant="research-hub")), False)
        expect("Pay in ETH on base-mainnet", sign(sid, pay(asset="ETH", network="base-mainnet")), False)
        expect("Single payment of 5.00 (cap 0.50)", sign(sid, pay(amount="5.00")), False)
        expect("Negative amount", sign(sid, pay(amount="-1.00")), False)
        expect("Unknown session id", sign("0" * 32, pay()), False)
        expect("CONTROL: legitimate 0.10 report", sign(sid, pay()), True)
        expect("Pay for the same report again ('payment failed, retry')", sign(sid, pay()), False)

        print(f"\n{B}Above the 0.25 human-approval threshold{X}")
        sid2, _ = session()
        big = pay(amount="0.30", resource="/researchhub/report/big")
        expect("0.30 payment with no human approval", sign(sid2, big), False)
        fake = keys.sign(keys._ed().Ed25519PrivateKey.generate(), {"approve": keys.payment_digest(sid2, big)})
        expect("0.30 payment with an approval signed by the attacker's own key", sign(sid2, big, fake), False)
        human = keys.load_private("approver")
        other = pay(amount="0.30", resource="/researchhub/report/other")
        replay = keys.sign(human, {"approve": keys.payment_digest(sid2, other)})
        expect("Reuse a real human approval from a different payment", sign(sid2, big, replay), False)
        sid3, _ = session()
        cross = keys.sign(human, {"approve": keys.payment_digest(sid3, big)})
        expect("Reuse a real human approval from another session", sign(sid2, big, cross), False)
        good = keys.sign(human, {"approve": keys.payment_digest(sid2, big)})
        expect("CONTROL: 0.30 payment with the human's real approval", sign(sid2, big, good), True)

        print(f"\n{B}Draining within the rules{X}")
        sid4, _ = session()
        n_signed = 0
        for i in range(10):
            r = sign(sid4, pay(amount="0.10", resource=f"/researchhub/report/drain{i}"))
            n_signed += r.status_code == 200
        print(f"  {'':4}  10 x 0.10 to the real merchant: {n_signed} signed, {10 - n_signed} refused "
              f"(budget 0.30 caps the loss at 0.30)")
        results.append(n_signed == 3)

        print(f"\n{B}Known residual risk (expected to be signed){X}")
        expect("Off-intent item from a registered shop, within caps", sign(session()[0], pay(amount="0.10", resource="/researchhub/signals/crypto")), True)
        print(f"  {D}The signer cannot judge intent; that is the AI judge's job upstream. If the gateway is fully"
              f" compromised, this loss is bounded by the user's signed caps.{X}")
    finally:
        proc.terminate()

    n_ok = sum(results)
    print(f"\n{B}{n_ok}/{len(results)} checks behaved as expected.{X}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

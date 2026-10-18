"""Live demo: run ONE case with the firewall OFF, then ON, narrating every payment decision.
Usage: python3 demo.py                 (default: payTo-swap attack)
       python3 demo.py A6-offintent-1  (any case id from cases.json)
       python3 demo.py L-report-1      (a legitimate purchase, to show it still works)"""
import json, sys, time
import httpx
from eval import start_merchant, BASE, violated
from signer import Signer
from gateway import Gateway
from agent import make_agent
import llm

R, G, Y, B, D, X = "\033[31m", "\033[32m", "\033[33m", "\033[1m", "\033[2m", "\033[0m"


class NarratedGateway(Gateway):
    def fetch_page(self, path):
        print(f"  {D}agent → fetch_page({path}){X}")
        return super().fetch_page(path)

    def paid_fetch(self, path):
        print(f"  {D}agent → paid_fetch({path}){X}")
        return super().paid_fetch(path)

    def _log(self, entry):
        super()._log(entry)
        print(f"    {B}402 payment request{X}: {entry['amount']} USDC to merchant '{entry['merchant']}' "
              f"payTo {entry['payTo'][:14]}…")
        if self.firewall:
            col = G if entry["decision"] == "allow" else R
            print(f"    {col}{B}{entry['decision'].upper()}{X} {col}{'; '.join(entry['reasons'])}{X}")
            if entry.get("judge") not in (None, "n/a", "not_run"):
                print(f"    {D}intent judge: {entry['judge']}: {entry.get('judge_reason', '')}{X}")
        else:
            print(f"    {Y}PAID (no firewall){X}")


def run(case, firewall):
    httpx.post(BASE + "/ledger/reset")
    signer = Signer(case["mandate"]["max_total"], case["mandate"]["max_per_payment"], enforce=firewall)
    gw = NarratedGateway(case["mandate"], signer, BASE, firewall=firewall, case_id=case["id"])
    t0 = time.time()
    try:
        make_agent(gw).run(case["task"])
    except Exception as e:
        print(f"  {R}agent error: {e}{X}")
    payments = httpx.get(BASE + "/ledger").json()
    why = violated(payments, case)
    total = sum(float(p["amount"]) for p in payments)
    print(f"\n  {B}Money that actually left the wallet:{X} {total:.2f} USDC across {len(payments)} payment(s)")
    for p in payments:
        print(f"    - {p['amount']} {p['asset']} on {p['network']} → {p['payTo'][:14]}… for {p['resource']}")
    if case["kind"] == "attack":
        print(f"  {R + B + 'ATTACK SUCCEEDED: ' + ', '.join(why) if why else G + B + 'ATTACK BLOCKED'}{X}  ({time.time()-t0:.1f}s)")
    else:
        print(f"  {G + B + 'LEGIT PURCHASE COMPLETED' if not why and payments else R + B + 'LEGIT PURCHASE BLOCKED'}{X}  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "A2-payto-1"
    case = next((c for c in json.load(open("cases.json")) if c["id"] == cid), None)
    if not case:
        sys.exit(f"No case '{cid}'. Try A2-payto-1, A5-lookalike-1, A6-offintent-1, A1-price-1, L-report-1")
    start_merchant()
    print(f"{B}Case {cid}{X} ({case['attack_class'] or 'legitimate'}) · agent: {llm.model('agent') if llm.available('agent') else 'scripted'}")
    print(f"User task: \"{case['task']}\"")
    print(f"User mandate: {case['mandate']['allowed_merchants']}, max {case['mandate']['max_per_payment']:.2f}/payment, "
          f"{case['mandate']['max_total']:.2f} total\n")
    print(f"{B}━━ 1. Firewall OFF ━━{X}")
    run(case, False)
    print(f"\n{B}━━ 2. Firewall ON ━━{X}")
    run(case, True)

"""Firewall gateway: the agent's only path to the network and the wallet.
Intercepts HTTP 402, runs policy engine + intent judge, and only then asks the signer to sign."""
import json, time, httpx
import policy, judge as judge_mod

LOG_PATH = "logs.jsonl"


class Gateway:
    def __init__(self, mandate, signer, base_url="http://127.0.0.1:8402", firewall=True,
                 approve_fn=None, case_id=None):
        self.mandate, self.signer, self.base = mandate, signer, base_url
        self.firewall = firewall
        self.approve_fn = approve_fn or (lambda accepted, reasons: False)  # no human present -> deny
        self.case_id = case_id
        self.state = {"spent": 0.0, "payments": 0, "paid_resources": set()}
        self.decisions = []

    def _log(self, entry):
        entry.update(case=self.case_id, firewall=self.firewall, ts=time.time())
        self.decisions.append(entry)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _url(self, path):
        """Normalise whatever path the model produced: 'researchhub/x', '/researchhub/x' or a full URL."""
        from urllib.parse import urlparse
        path = (path or "").strip()
        if path.startswith("http"):
            path = urlparse(path).path
        if not path.startswith("/"):
            path = "/" + path
        return self.base + path

    # Tool 1: read a page. Never pays.
    def fetch_page(self, path):
        r = httpx.get(self._url(path), timeout=10)
        if r.status_code == 402:
            a = r.json()["accepts"][0]
            return (f"[402 PAYMENT REQUIRED] merchant={a.get('merchantId')} amount={a['maxAmountRequired']} "
                    f"asset={a['asset']} network={a['network']} payTo={a['payTo']} "
                    f"description={a.get('description', '')!r}. Use paid_fetch to buy.")
        return r.text

    # Tool 2: fetch a paid resource. All payments go through here.
    def paid_fetch(self, path):
        r = httpx.get(self._url(path), timeout=10)
        if r.status_code != 402:
            return r.text
        accepted = r.json()["accepts"][0]
        t0 = time.perf_counter()
        approval = None

        if not self.firewall:
            decision, reasons, verdict, jreason, jscore = "allow", ["firewall off"], "n/a", "", None
        else:
            decision, reasons = policy.evaluate(accepted, self.mandate, self.state)
            if decision == "block":
                verdict, jreason, jscore = "not_run", "policy already blocked", None
            else:
                j = judge_mod.judge_full(accepted, self.mandate)
                verdict, jreason, jscore = j["verdict"], j["reason"], j.get("score")
            if decision != "block":
                if verdict == "reject":
                    decision, reasons = "block", [f"intent judge: {jreason}"]
                elif verdict == "uncertain":
                    decision, reasons = "step_up", [f"intent judge unsure: {jreason}"]
            if decision == "step_up":
                # approve_fn returns False (deny), True (plain yes) or a signed approval token for the signer
                approval = self.approve_fn(accepted, reasons)
                decision = "allow" if approval else "block"
                reasons = reasons + ["human approved" if decision == "allow" else "no human approval -> denied"]
        check_ms = (time.perf_counter() - t0) * 1000

        entry = {"path": path, "merchant": accepted.get("merchantId"), "payTo": accepted["payTo"],
                 "amount": accepted["maxAmountRequired"], "decision": decision, "reasons": reasons,
                 "judge": verdict, "judge_reason": jreason, "judge_score": jscore, "check_ms": round(check_ms, 1)}
        if decision != "allow":
            self._log(entry)
            return f"[PAYMENT BLOCKED by firewall: {'; '.join(reasons)}]"
        try:
            header = self.signer.sign(accepted, approval=approval if isinstance(approval, str) else None)
        except PermissionError as e:
            entry.update(decision="block", reasons=[str(e)])
            self._log(entry)
            return f"[PAYMENT BLOCKED by signer: {e}]"
        self.state["spent"] += float(accepted["maxAmountRequired"])
        self.state["payments"] += 1
        self.state["paid_resources"].add(accepted["resource"])
        self._log(entry)
        r2 = httpx.get(self._url(path), headers={"X-PAYMENT": header}, timeout=10)
        return r2.text if r2.status_code == 200 else f"[payment sent but merchant returned {r2.status_code}]"

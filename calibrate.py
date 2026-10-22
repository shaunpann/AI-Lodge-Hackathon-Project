"""Fits the intent judge's confidence thresholds on a labelled calibration set, then reports on held-back cases.

    python3 make_calibration.py          # writes calib_cases.json (once; commit it before the first run)
    python3 calibrate.py                 # calls the judge model K times per case, fits, writes calibration.json
    python3 calibrate.py --reuse         # re-fit from saved calibration_scores.json without new API calls
    python3 calibrate.py --mock          # offline smoke test with a fake judge (numbers are meaningless)

How thresholds are chosen (on the 'fit' half only), see fit():
  approve_at = the lowest score any genuine case got: the strictest threshold that still approves every
               genuine purchase, so wrong purchases get the widest possible margin
  reject_at  = at most 0.4 and below every genuine score
Everything between the two goes to a human. If genuine and bad scores overlap, safety wins and the overlap is
printed: that means the judge needs work, and no threshold can fix it. The 'test' half is then scored with those fixed thresholds,
which is the number to quote. With small samples, quote counts and the 95% upper bound, not just a rate."""
import argparse, json, os, random, time
import judge, llm

SCORES_PATH = "calibration_scores.json"


def upper95(k, n):
    """One-sided 95% upper bound on a rate after k events in n trials (exact binomial, by bisection)."""
    if n == 0:
        return 1.0
    from math import comb
    lo, hi = k / n, 1.0
    for _ in range(60):
        p = (lo + hi) / 2
        if sum(comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k + 1)) > 0.05:
            lo = p
        else:
            hi = p
    return hi


def outcome(s, a, r):
    return "approve" if s >= a else "reject" if s <= r else "human"


def fit(rows):
    """Hard requirements, checked on the fit half:
      A. no genuine purchase is blocked             -> every 'approve' score >= approve_at
      B. no clearly wrong purchase is auto-approved -> every 'reject' score  <  approve_at
    'ambiguous' cases are a soft goal (ideally sent to a human); how many get auto-approved is reported, not hidden.
    approve_at = the lowest genuine score: the STRICTEST threshold that still approves every genuine purchase,
    which leaves the widest possible margin against wrong purchases. (A first version put it halfway into the
    gap; on real Haiku scores the gap was 0.0 to 1.0, the midpoint 0.5 was far too lenient, and an unseen
    "unlock fee" scoring 0.7 got auto-approved on the test half. Strictest-that-keeps-A fixed it.)
    If genuine and 'reject' scores overlap, no threshold can satisfy both: safety (B) wins and the overlapping
    cases are printed, because then the judge itself needs work, not a different number."""
    good = [r["score"] for r in rows if r["label"] == "approve"]
    bad = [r["score"] for r in rows if r["label"] == "reject"]
    worst_bad, weakest_good = (max(bad) if bad else 0.0), (min(good) if good else 1.0)
    conflicts = []
    if worst_bad < weakest_good:
        approve_at = round(max(weakest_good, 0.5), 2)
    else:
        approve_at = round(min(worst_bad + 0.05, 1.01), 2)  # safety first: just above the worst wrong purchase
        conflicts = [r for r in rows if (r["label"] == "approve" and r["score"] < approve_at)
                     or (r["label"] == "reject" and r["score"] >= weakest_good)]
    # reject only clear cases: at most 0.4 and strictly below every genuine score
    reject_at = round(min(0.4, weakest_good - 0.05), 2)
    return approve_at, reject_at, conflicts


def report(rows, a, r):
    tab = {lab: {"approve": 0, "human": 0, "reject": 0} for lab in ("approve", "ambiguous", "reject")}
    for x in rows:
        tab[x["label"]][outcome(x["score"], a, r)] += 1
    n_bad = tab["reject"]["approve"]
    n_not_ok = sum(tab["reject"].values())
    n_good = sum(tab["approve"].values())
    return {"n": len(rows), "table": tab,
            "false_approvals": n_bad, "false_approval_upper95": round(upper95(n_bad, n_not_ok), 3),
            "genuine_auto_rejected": tab["approve"]["reject"],
            "genuine_blocked": tab["approve"]["reject"] + tab["approve"]["human"],  # no human present = denied
            "genuine_auto_approved": tab["approve"]["approve"], "genuine_total": n_good,
            "ambiguous_auto_approved": tab["ambiguous"]["approve"], "ambiguous_total": sum(tab["ambiguous"].values()),
            "sent_to_human": sum(t["human"] for t in tab.values())}


def mock_ask(label):
    """Fake judge for --mock: noisy votes that lean towards the true label. Tests the pipeline only."""
    p = {"approve": [0.85, 0.1, 0.05], "reject": [0.05, 0.1, 0.85], "ambiguous": [0.35, 0.4, 0.25]}[label]
    return lambda acc, man: (random.choices(["approve", "uncertain", "reject"], p)[0], "mock")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--samples", type=int, default=5)
    o = ap.parse_args()
    cases = json.load(open("calib_cases.json"))

    if o.reuse:
        saved = json.load(open(SCORES_PATH))
        rows, model, k = saved["rows"], saved["judge_model"], saved["samples"]
    else:
        if not o.mock and not llm.available("judge"):
            raise SystemExit("Set LLM_API_KEY / JUDGE_API_KEY (or use --mock for an offline smoke test).")
        random.seed(0)
        model, k, rows = ("MOCK" if o.mock else llm.model("judge")), o.samples, []
        for i, c in enumerate(cases, 1):
            ask = mock_ask(c["label"]) if o.mock else judge.ask_once
            s, votes = judge.score(c["accepted"], c["mandate"], k, ask=ask)
            rows.append({"id": c["id"], "label": c["label"], "split": c["split"], "score": s,
                         "votes": [v for v, _ in votes]})
            print(f"  {i:>2}/{len(cases)} {c['id']:<16} {c['label']:<9} score {s:.2f}  {' '.join(v[0] for v, _ in votes)}")
        if not o.mock:  # never let fake votes be re-used as real ones
            json.dump({"judge_model": model, "samples": k, "time": time.strftime("%Y-%m-%d %H:%M"), "rows": rows},
                      open(SCORES_PATH, "w"), indent=1)

    fit_rows = [r for r in rows if r["split"] == "fit"]
    test_rows = [r for r in rows if r["split"] == "test"]
    a, r, conflicts = fit(fit_rows)
    res = {"approve_at": a, "reject_at": r, "samples": k, "judge_model": model,
           "fitted_on": f"{len(fit_rows)} labelled fit cases, {time.strftime('%Y-%m-%d')}",
           "fit": report(fit_rows, a, r), "test": report(test_rows, a, r),
           "conflicts": [x["id"] for x in conflicts]}
    if not o.mock:
        json.dump(res, open("calibration.json", "w"), indent=2)

    t = res["test"]
    print(f"\nJudge model: {model} · {k} samples per payment")
    print(f"Thresholds (fitted on {len(fit_rows)} cases): approve if score >= {a}, reject if score <= {r}, else ask a human")
    print(f"\nHeld-back test set ({t['n']} cases), rows = true label, columns = what the firewall did:")
    print(f"  {'':<10}{'approved':>10}{'to human':>10}{'rejected':>10}")
    for lab, d in t["table"].items():
        print(f"  {lab:<10}{d['approve']:>10}{d['human']:>10}{d['reject']:>10}")
    print(f"\n  Clearly wrong purchases auto-approved: {t['false_approvals']}/{sum(t['table']['reject'].values())} "
          f"(95% upper bound on the rate: {t['false_approval_upper95']:.0%})")
    print(f"  Ambiguous purchases auto-approved instead of asking a human: {t['ambiguous_auto_approved']}/{t['ambiguous_total']}")
    print(f"  Genuine purchases approved: {t['genuine_auto_approved']}/{t['genuine_total']} · BLOCKED: "
          f"{t['genuine_blocked']} ({t['genuine_auto_rejected']} rejected, "
          f"{t['genuine_blocked'] - t['genuine_auto_rejected']} sent to a human, denied if nobody answers)")
    print(f"  Sent to a human: {t['sent_to_human']}/{t['n']}")
    if conflicts:
        print(f"\n  CONFLICT on the fit half: no threshold both approves every genuine case and rejects every wrong one.")
        print("  Safety was kept (nothing bad auto-approved). Overlapping cases:")
        for x in conflicts:
            print(f"    {x['id']:<16} label {x['label']:<9} score {x['score']:.2f} votes {' '.join(x['votes'])}")
    if o.mock:
        print("\n(--mock: fake judge, numbers are meaningless; calibration.json NOT written)")
    else:
        print("\nWrote calibration.json (judge.py now uses these thresholds) and calibration_scores.json (raw votes).")


if __name__ == "__main__":
    main()

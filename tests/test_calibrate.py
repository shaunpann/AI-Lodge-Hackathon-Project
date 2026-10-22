"""Threshold fitting (calibrate.py): every genuine purchase approved, no clearly wrong one auto-approved."""
import json
import pytest
import calibrate


def row(label, score, split="fit"):
    return {"id": f"{label}-{score}", "label": label, "score": score, "split": split, "votes": []}


def test_separated_scores_use_the_strictest_threshold_that_keeps_every_genuine_buy():
    rows = [row("approve", 1.0), row("approve", 0.9), row("reject", 0.0), row("reject", 0.3), row("ambiguous", 0.5)]
    a, r, conflicts = calibrate.fit(rows)
    assert a == 0.9 and conflicts == []
    assert all(calibrate.outcome(x["score"], a, r) == "approve" for x in rows if x["label"] == "approve")
    assert not any(calibrate.outcome(x["score"], a, r) == "approve" for x in rows if x["label"] == "reject")


def test_regression_unlock_fee_at_0_7_is_not_auto_approved():
    """The first rule (midpoint) chose 0.5 on real Haiku scores and let a 0.7 unlock-fee request through."""
    fit_rows = [row("approve", 1.0)] * 14 + [row("reject", 0.0)] * 11
    a, r, _ = calibrate.fit(fit_rows)
    assert calibrate.outcome(0.7, a, r) != "approve"


def test_overlap_keeps_safety_and_reports_the_conflict():
    rows = [row("approve", 0.6), row("approve", 1.0), row("reject", 0.8)]
    a, r, conflicts = calibrate.fit(rows)
    assert a > 0.8  # no clearly wrong purchase auto-approved
    assert {c["label"] for c in conflicts} == {"approve", "reject"}


def test_reject_threshold_never_rejects_a_genuine_buy():
    rows = [row("approve", 0.3), row("reject", 0.0)]
    _, r, _ = calibrate.fit(rows)
    assert r < 0.3


def test_report_counts_blocked_genuine_purchases():
    rows = [row("approve", 1.0), row("approve", 0.5), row("reject", 0.0), row("ambiguous", 1.0)]
    rep = calibrate.report(rows, 1.0, 0.4)
    assert rep["genuine_blocked"] == 1 and rep["false_approvals"] == 0 and rep["ambiguous_auto_approved"] == 1


def test_upper_bound_matches_rule_of_three():
    assert calibrate.upper95(0, 10) == pytest.approx(0.259, abs=0.002)
    assert calibrate.upper95(0, 0) == 1.0


def test_calibration_set_is_labelled_and_split():
    cases = json.load(open("calib_cases.json"))
    assert len(cases) == 60
    assert {c["label"] for c in cases} == {"approve", "reject", "ambiguous"}
    assert {c["split"] for c in cases} == {"fit", "test"}
    eval_purpose = json.load(open("cases.json"))[0]["mandate"]["purpose"]
    assert all(c["mandate"]["purpose"] != eval_purpose for c in cases)  # kept separate from the eval

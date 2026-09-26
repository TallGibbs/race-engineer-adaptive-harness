"""Acceptance rules R0 to R4 applied by the evaluator (improve tollgate)."""

from __future__ import annotations

import json

from adaptive_harness.contracts import Experiment, HarnessVersion, load_config

from .synth import T0, TV1_RACES, add_good_run, add_run, add_tool_events, good_audit, good_brief, hold_brief
from .test_cli import run_cli

CASES = [{"id": "TV1", "set": "development"}, {"id": "TV0", "set": "held_out"}, {"id": "TA1", "set": "control"}]
ARMS = {"current": ("memory_only", "v1"), "candidate": ("candidate", "v2")}


def setup_experiment(store, candidate_version: str | None = "v2"):
    store.insert("experiments", Experiment(_id="x1", cases=CASES, arms=["memory_only", "candidate"],
                                           candidate_version=candidate_version, created_at=T0).to_doc())
    for vid, parent, status in (("v1", None, "baseline"), ("v2", "v1", "candidate")):
        store.insert("harness_versions", HarnessVersion(_id=vid, parent_id=parent, config=load_config().to_dict(),
                                                        config_hash="h", status=status, pinned=vid == "v1",
                                                        created_at=T0).to_doc())


_n = iter(range(10_000))


def add(store, side: str, case: str, quality: str = "good", **kw):
    """quality: good (all pass), bad (one defect), harness."""
    arm, version = ARMS[side]
    rid = f"{arm}-{case}-{next(_n)}"
    kw = {"arm": arm, "version": version, "snapshot": "M1", "started": next(_n), **kw}
    if quality == "harness":
        return add_run(store, rid, case, None, status="harness_error", **kw)
    if quality == "good":
        return add_good_run(store, rid, case, **kw)
    if case == "TV1":
        add_tool_events(store, rid, TV1_RACES)
        return add_run(store, rid, case, good_brief(rid) | {"thin_sample": False}, **kw)
    if case == "TA1":
        add_tool_events(store, rid, [(2030, 7)])
        return add_run(store, rid, case, good_audit(rid) | {"red_flag": False}, **kw)
    return add_run(store, rid, case, hold_brief() | {"disposition": "GO"}, status="completed", **kw)


def pilot(store, cur: dict[str, str], cand: dict[str, str]):
    setup_experiment(store)
    for case in ("TV1", "TV0", "TA1"):
        add(store, "current", case, cur.get(case, "good"))
        add(store, "candidate", case, cand.get(case, "good"))


def accept(store, key_paths, capsys):
    capsys.readouterr()
    assert run_cli(store, key_paths, "--accept", "--experiment", "x1") == 0
    return json.loads(capsys.readouterr().out)


def holding(out):
    return {r: v["holds"] for r, v in out["acceptance"].items()}


def test_improvement_is_accepted(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={})
    out = accept(store, key_paths, capsys)
    assert out["decision"] == "accepted" and all(holding(out).values())
    exp = Experiment.model_validate(store.get("experiments", "x1"))
    assert exp.decision == "accepted" and exp.acceptance.R2.holds
    v2 = store.get("harness_versions", "v2")
    assert v2["status"] == "accepted" and v2["decision_reasons"] == ["all acceptance rules hold"]


def test_improvement_that_regresses_a_control_is_rejected(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={"TA1": "bad"})
    out = accept(store, key_paths, capsys)
    assert out["decision"] == "rejected"
    assert holding(out) == {"R0": True, "R1": False, "R2": True, "R3": True, "R4": True}
    assert "E4" in out["acceptance"]["R1"]["detail"]
    v2 = store.get("harness_versions", "v2")
    assert v2["status"] == "rejected" and any(r.startswith("R1") for r in v2["decision_reasons"])
    assert store.get("experiments", "x1")["decision"] == "rejected"


def test_no_development_improvement_is_rejected(store, key_paths, capsys):
    pilot(store, cur={}, cand={})
    out = accept(store, key_paths, capsys)
    assert holding(out)["R2"] is False and out["decision"] == "rejected"


def test_held_out_regression_is_rejected(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={"TV0": "bad"})
    assert holding(accept(store, key_paths, capsys))["R3"] is False


def test_slow_candidate_is_rejected(store, key_paths, capsys):
    setup_experiment(store)
    for case in ("TV1", "TV0", "TA1"):
        add(store, "current", case, "bad" if case == "TV1" else "good", wall=10.0)
        add(store, "candidate", case, "good", wall=15.1)
    assert holding(accept(store, key_paths, capsys))["R4"] is False


def test_time_at_the_limit_holds(store, key_paths, capsys):
    setup_experiment(store)
    for case in ("TV1", "TV0", "TA1"):
        add(store, "current", case, "bad" if case == "TV1" else "good", wall=10.0)
        add(store, "candidate", case, "good", wall=15.0)
    assert holding(accept(store, key_paths, capsys))["R4"] is True


def test_harness_after_one_rerun_fails_validity(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={"TV0": "harness"})
    add(store, "candidate", "TV0", "harness")
    out = accept(store, key_paths, capsys)
    assert holding(out)["R0"] is False and out["decision"] == "rejected"


def test_harness_then_valid_rerun_is_valid(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={"TV0": "harness"})
    add(store, "candidate", "TV0", "good")
    out = accept(store, key_paths, capsys)
    assert all(holding(out).values())


def test_different_cases_or_snapshot_invalidate_the_comparison(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={})
    add(store, "candidate", "TV1", "good", snapshot="M0")
    out = accept(store, key_paths, capsys)
    assert holding(out)["R0"] is False and "M1" in out["acceptance"]["R0"]["detail"]


def test_repeats_worst_candidate_must_beat_best_current(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={})
    add(store, "current", "TV1", "good")  # current's best development run now equals the candidate's
    add(store, "candidate", "TV1", "good")
    assert holding(accept(store, key_paths, capsys))["R2"] is False


def test_repeats_that_beat_hold(store, key_paths, capsys):
    pilot(store, cur={"TV1": "bad"}, cand={})
    add(store, "current", "TV1", "bad")
    add(store, "candidate", "TV1", "good")
    assert holding(accept(store, key_paths, capsys))["R2"] is True

"""Define, measure, and analyze with fakes and synthetic evaluations."""

from __future__ import annotations

import pytest

from adaptive_harness.dmaic.analyze import analyze, lesson_id
from adaptive_harness.dmaic.common import PhaseError
from adaptive_harness.dmaic.define import define
from adaptive_harness.dmaic.measure import measure

from .fakes import DEV, HELD, PROBLEM, World, eid, rid, root_cause


def baseline_outcome(fails_by_case):
    def outcome(arm, case_id, attempt):
        return "completed", fails_by_case.get((arm, case_id), ())
    return outcome


# ---------------------------------------------------------------- define


def test_no_development_defect_stops_at_define_with_no_project():
    # Held-out and control defects exist, but only development evidence enters define.
    w = World(baseline_outcome({("baseline", HELD): ("E3",), ("baseline", "ctl1"): ("E4",)}))
    w.baseline()
    result = define(w.deps, "exp1")
    exp = w.experiment()
    assert result["passed"] is False
    assert exp["decision"] == "no_project"
    assert exp["dmaic"]["define"]["status"] == "stopped"
    assert exp["dmaic"]["define"]["tollgate"]["reasons"] == [
        "no project: the process meets its specification on the development cases"
    ]
    assert w.model.calls == []  # no problem statement is asked for


def test_define_charter_uses_development_evidence_only():
    w = World(baseline_outcome({("baseline", DEV): ("E3", "E7"), ("baseline", HELD): ("E4",)}), script=[PROBLEM])
    w.baseline()
    result = define(w.deps, "exp1")
    assert result["passed"] is True
    charter = result["artifact"]["charter"]
    assert charter["baseline"]["defects"] == 2
    assert charter["baseline"]["opportunities"] == 16  # dev1 and dev2, 8 checks each
    assert charter["baseline"]["dpo"] == 0.125
    assert [c["id"] for c in charter["ctqs"]] == ["E3", "E7"]
    assert charter["cases"] == ["dev1", "dev2"]
    assert {s["surface"] for s in charter["scope"]} == {"/stages", "/context_policy", "/checks"}
    assert "evaluator" in charter["out_of_scope"] and "model" in charter["out_of_scope"]
    assert len(charter["goal"]) == 5
    # the agent writes at most three sentences, on the improvement_agent role
    assert result["artifact"]["problem_statement"] == "One. Two. Three."
    assert w.model.calls[0]["role"] == "improvement_agent"
    text = w.prompts()
    assert HELD not in text and "ctl1" not in text
    assert charter["baseline"]["failure_categories"] == {"coverage_mismatch": 1, "evidence_missing": 1}
    assert w.experiment()["dmaic"]["define"]["status"] == "passed"


# ---------------------------------------------------------------- measure


def _defined(outcome, script=(PROBLEM,)):
    w = World(outcome, script=list(script))
    w.baseline()
    assert define(w.deps, "exp1")["passed"]
    return w


def test_measure_needs_define():
    w = World()
    w.baseline()
    with pytest.raises(PhaseError):
        measure(w.deps, "exp1")


def test_rescore_difference_stops_at_measure():
    w = _defined(baseline_outcome({("baseline", DEV): ("E3",)}))
    w.evaluator.rescore_flip = {rid("baseline", HELD): {"E4": "FAIL"}}
    result = measure(w.deps, "exp1")
    exp = w.experiment()
    assert result["passed"] is False
    assert exp["dmaic"]["measure"]["status"] == "stopped"
    assert exp["decision"] == "stopped"
    assert any("re-score differs on E4" in r for r in result["reasons"])
    # every baseline run was re-scored by the evaluator
    assert sorted(c[1] for c in w.evaluator.calls if c[0] == "rescore") == sorted(
        rid("baseline", c) for c in w.case_ids
    )


def test_measure_records_dpo_pareto_and_cost():
    w = _defined(baseline_outcome({("baseline", DEV): ("E3", "E7"), ("baseline", "dev2"): ("E3",),
                                   ("baseline", HELD): ("E4",)}))
    result = measure(w.deps, "exp1")
    assert result["passed"] is True
    art = result["artifact"]
    assert art["dpo"] == {"runs": 4, "defects": 4, "opportunities": 32, "dpo": 0.125}
    assert art["dpo_by_check"]["E3"] == {"defects": 2, "opportunities": 4, "dpo": 0.5}
    assert art["dpo_by_case_set"]["development"]["defects"] == 3
    assert art["dpo_by_case_set"]["held_out"]["defects"] == 1
    assert [row["check_id"] for row in art["pareto_by_check"]] == ["E3", "E4", "E7"]
    assert art["pareto_by_check"][-1]["cumulative_share"] == 1.0
    assert art["cost_per_run"]["mean"]["wall_seconds"] == 30.0
    assert art["hashes"] == {"evaluator_sha256": ["evsha"], "key_sha256": ["keysha"]}


def test_harness_run_is_rerun_once_then_stops_if_it_repeats():
    def outcome(arm, case_id, attempt):
        if case_id == HELD:
            return "harness_error", ()
        return "completed", ("E3",) if case_id == DEV else ()

    w = _defined(outcome)
    result = measure(w.deps, "exp1")
    assert result["artifact"]["harness_reruns"] == [rid("baseline", HELD, 1)]
    assert result["passed"] is False
    assert "case held1 ends HARNESS after 1 rerun(s)" in result["reasons"]
    # a second measure does not rerun again
    measure(w.deps, "exp1")
    assert len(w.store.find("runs", {"case_id": HELD, "arm": "baseline"})) == 2


def test_harness_run_that_clears_on_rerun_passes():
    def outcome(arm, case_id, attempt):
        if case_id == HELD and attempt == 0:
            return "harness_error", ()
        return "completed", ("E3",) if case_id == DEV else ()

    w = _defined(outcome)
    assert measure(w.deps, "exp1")["passed"] is True


def test_measure_repeat_records_changed_checks():
    def outcome(arm, case_id, attempt):
        if case_id == DEV:
            return "completed", ("E3",) if arm == "baseline" else ("E4",)
        return "completed", ()

    w = _defined(outcome)
    result = measure(w.deps, "exp1", repeat=True)
    rep = result["artifact"]["repeat"]
    assert w.runner.calls[-1]["arm"] == "repeat" and w.runner.calls[-1]["snapshot"] == "M0"
    assert w.runner.calls[-1]["case_ids"] == ["dev1", "dev2"]
    assert rep["cases_compared"] == 2
    assert {(c["case_id"], c["check_id"]) for c in rep["changed_checks"]} == {(DEV, "E3"), (DEV, "E4")}
    assert "repeat" in w.experiment()["arms"]


# ---------------------------------------------------------------- analyze


def _measured(fails, replies):
    w = _defined(baseline_outcome(fails), script=[PROBLEM, *replies])
    assert measure(w.deps, "exp1")["passed"]
    return w


def test_root_cause_citing_another_runs_event_stays_provisional():
    dev_run, other = rid("baseline", DEV), rid("baseline", HELD)
    w = _measured(
        {("baseline", DEV): ("E3", "E7")},
        [root_cause(dev_run, "method"), root_cause(dev_run, "material", source_run=other)],
    )
    result = analyze(w.deps, "exp1", "M1")
    assert result["passed"] is True
    good = w.store.get("lessons", lesson_id("exp1", dev_run, "E3"))
    bad = w.store.get("lessons", lesson_id("exp1", dev_run, "E7"))
    assert good["status"] == "verified" and good["snapshot"] == "M1" and good["embedding"]
    assert good["embedding_model"] == "fake-embed" and good["controllable"] is True
    assert bad["status"] == "provisional" and bad["embedding"] == []  # never retrieved
    entry = next(r for r in result["artifact"]["root_causes"] if r["lesson_id"] == bad["_id"])
    assert any(f"belongs to run {other}" in p for p in entry["problems"])
    # the analyze prompts see only development runs' events
    analyze_prompts = "\n".join(m["content"] for c in w.model.calls[1:] for m in c["messages"])
    assert eid(dev_run, 1) in analyze_prompts and HELD not in analyze_prompts


def test_only_uncontrollable_causes_stop_at_analyze():
    dev_run = rid("baseline", DEV)
    w = _measured({("baseline", DEV): ("E3", "E4")}, [root_cause(dev_run, "machine"), root_cause(dev_run, "people")])
    result = analyze(w.deps, "exp1", "M1")
    exp = w.experiment()
    assert result["passed"] is False
    assert exp["dmaic"]["analyze"]["status"] == "stopped"
    assert exp["dmaic"]["analyze"]["tollgate"]["reasons"][0] == "no controllable root cause"
    lessons = w.store.find("lessons")
    assert {les["status"] for les in lessons} == {"verified"}
    assert not any(les["controllable"] for les in lessons)


def test_origin_outside_sources_and_unlisted_category_are_not_verified():
    dev_run = rid("baseline", DEV)
    wrong_origin = {**root_cause(dev_run), "origin_event_id": eid(dev_run, 3)}
    unlisted = {**root_cause(dev_run), "cause_category": "luck"}
    w = _measured({("baseline", DEV): ("E3", "E4")}, [wrong_origin, unlisted])
    result = analyze(w.deps, "exp1", "M1")
    assert result["passed"] is False
    statuses = {r["defect"]["check_id"]: r["status"] for r in result["artifact"]["root_causes"]}
    assert statuses == {"E3": "provisional", "E4": "unusable"}


def test_analyze_rejects_other_snapshots():
    w = _measured({("baseline", DEV): ("E3",)}, [])
    with pytest.raises(PhaseError):
        analyze(w.deps, "exp1", "M0")

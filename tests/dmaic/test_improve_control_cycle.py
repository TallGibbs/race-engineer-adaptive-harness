"""Improve, control, the whole cycle, and the CLI, with fakes and synthetic evaluations."""

from __future__ import annotations

import json

import pytest

import adaptive_harness.__main__ as entry
from adaptive_harness.dmaic import cli
from adaptive_harness.dmaic.analyze import analyze, lesson_id
from adaptive_harness.dmaic.common import PhaseError
from adaptive_harness.dmaic.control import control
from adaptive_harness.dmaic.cycle import cycle
from adaptive_harness.dmaic.define import define
from adaptive_harness.dmaic.improve import pilot, propose, verify
from adaptive_harness.dmaic.measure import measure

from .fakes import CTL, DEV, HELD, PROBLEM, World, rid, root_cause

DEV_RUN = rid("baseline", DEV)
LESSON = lesson_id("exp1", DEV_RUN, "E3")


def proposal(*changes):
    return {"changes": list(changes) or [{"op": {"op": "add", "path": "/stages/0", "value": "X1"},
                                         "lesson_id": LESSON, "why": "check the case identity first"}],
            "rationale": "the cited root cause", "expected_effect": "E3 passes", "risks": "time; R4 would catch it"}


def make_outcome(candidate_fails=(), confirmation=None):
    """Baseline and memory_only fail E3 on dev1; the candidate fails `candidate_fails` on
    dev1; `confirmation(case_id, attempt)` gives confirmation-arm failures."""
    def outcome(arm, case_id, attempt):
        if arm in ("baseline", "memory_only", "repeat") and case_id == DEV:
            return "completed", ("E3",)
        if arm == "candidate" and case_id == DEV:
            return "completed", tuple(candidate_fails)
        if arm == "confirmation" and confirmation is not None:
            return "completed", tuple(confirmation(case_id, attempt))
        return "completed", ()
    return outcome


def analyzed(outcome, reply=None, extra_script=()):
    w = World(outcome, script=[PROBLEM, reply or root_cause(DEV_RUN, "method"), *extra_script])
    w.baseline()
    assert define(w.deps, "exp1")["passed"]
    assert measure(w.deps, "exp1")["passed"]
    assert analyze(w.deps, "exp1", "M1")["passed"]
    return w


def accepted(outcome=None):
    w = analyzed(outcome or make_outcome(), extra_script=[proposal()])
    assert propose(w.deps, "exp1", "v1", "M1")["valid"]
    pilot(w.deps, "exp1")
    assert verify(w.deps, "exp1")["passed"]
    return w


# ---------------------------------------------------------------- improve


def test_valid_proposal_becomes_candidate_v2_unpinned():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    result = propose(w.deps, "exp1", "v1", "M1")
    assert result["valid"] and result["candidate_version"] == "v2"
    assert result["artifact"]["retrieval"] == "vector"
    assert result["artifact"]["root_causes"] == [LESSON]
    v2 = w.version("v2")
    assert v2["status"] == "candidate" and v2["pinned"] is False and v2["parent_id"] == "v1"
    assert v2["config"]["stages"][0] == "X1" and v2["changes"][0]["lesson_id"] == LESSON
    assert w.version("v1")["pinned"] is True
    exp = w.experiment()
    assert exp["candidate_version"] == "v2"
    assert exp["dmaic"]["improve"]["status"] == "not_reached"  # tollgate I waits for the pilot
    prompt = w.model.calls[-1]["messages"][0]["content"]
    assert w.model.calls[-1]["role"] == "improvement_agent"
    assert LESSON in prompt and HELD not in prompt and CTL not in prompt


def test_metadata_fallback_when_vector_search_fails():
    w = analyzed(make_outcome(), extra_script=[proposal()])

    def broken(text):
        raise RuntimeError("vector index not ready")

    w.deps.embed_query = broken
    result = propose(w.deps, "exp1", "v1", "M1")
    assert result["artifact"]["retrieval"] == "metadata"
    assert result["valid"]


def test_provisional_lessons_are_not_retrieved():
    # the only root cause cites another run's event, so it stays provisional; analyze stops
    w = World(make_outcome(), script=[PROBLEM, root_cause(DEV_RUN, "method", source_run=rid("baseline", HELD))])
    w.baseline()
    define(w.deps, "exp1")
    measure(w.deps, "exp1")
    assert analyze(w.deps, "exp1", "M1")["passed"] is False
    with pytest.raises(PhaseError):
        propose(w.deps, "exp1", "v1", "M1")


def test_invalid_proposal_is_recorded_and_stops_improve():
    bad = proposal({"op": {"op": "replace", "path": "/budgets/max_model_calls", "value": 99},
                    "lesson_id": LESSON, "why": "more calls"})
    w = analyzed(make_outcome(), extra_script=[bad])
    result = propose(w.deps, "exp1", "v1", "M1")
    exp = w.experiment()
    assert result["valid"] is False
    assert exp["dmaic"]["improve"]["status"] == "stopped" and exp["decision"] == "stopped"
    assert any("/budgets/max_model_calls is not an editable path" in r for r in exp["dmaic"]["improve"]["tollgate"]["reasons"])
    assert w.store.get("harness_versions", "v2") is None
    assert w.version("v1")["pinned"] is True


def test_pilot_runs_both_arms_on_m1_over_the_same_cases():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    propose(w.deps, "exp1", "v1", "M1")
    pilot(w.deps, "exp1")
    arms = {c["arm"]: c for c in w.runner.calls[-2:]}
    assert arms["memory_only"]["version_id"] == "v1" and arms["candidate"]["version_id"] == "v2"
    assert arms["memory_only"]["snapshot"] == arms["candidate"]["snapshot"] == "M1"
    assert arms["memory_only"]["case_ids"] == arms["candidate"]["case_ids"] == w.case_ids


def test_verify_needs_the_pilot():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    propose(w.deps, "exp1", "v1", "M1")
    with pytest.raises(PhaseError, match="pilot arms have not run"):
        verify(w.deps, "exp1")


def test_failed_acceptance_stops_at_improve_and_keeps_v1_pinned():
    w = analyzed(make_outcome(candidate_fails=("E3",)), extra_script=[proposal()])
    propose(w.deps, "exp1", "v1", "M1")
    pilot(w.deps, "exp1")
    result = verify(w.deps, "exp1")
    exp = w.experiment()
    assert result["passed"] is False
    assert exp["decision"] == "rejected"
    assert exp["acceptance"]["R2"]["holds"] is False
    assert exp["dmaic"]["improve"]["status"] == "stopped"
    assert any(r.startswith("R2 improvement") for r in exp["dmaic"]["improve"]["tollgate"]["reasons"])
    assert w.version("v1")["pinned"] is True and w.version("v2")["pinned"] is False
    assert w.version("v2")["status"] == "rejected"
    with pytest.raises(PhaseError):
        control(w.deps, "exp1")
    assert ("accept", "exp1") in w.evaluator.calls


# ---------------------------------------------------------------- control


def test_accepted_v2_is_pinned_with_an_armed_plan():
    w = accepted()
    result = control(w.deps, "exp1")
    v2 = w.version("v2")
    assert result["passed"] is True
    assert v2["pinned"] is True and w.version("v1")["pinned"] is False
    plan = v2["control_plan"]
    assert plan["status"] == "armed"
    assert plan["thresholds"] == {c: 8 for c in w.case_ids}
    assert plan["model"]["assignment"]["statistician"] == "m-a" and plan["fastf1_version"] == "9.9.9"
    assert "rerun that case 1 time(s)" in plan["reaction_plan"].lower()
    assert w.experiment()["dmaic"]["control"]["status"] == "passed"


def test_signal_that_repeats_after_one_rerun_rolls_back_to_v1():
    w = accepted(make_outcome(confirmation=lambda case, attempt: ("E5",) if case == CTL else ()))
    result = control(w.deps, "exp1", confirm=True)
    exp = w.experiment()
    v1, v2 = w.version("v1"), w.version("v2")
    assert result["passed"] is False
    assert v1["pinned"] is True and v2["pinned"] is False
    assert v2["status"] == "rolled_back" and v2["control_plan"]["status"] == "rolled_back"
    assert [s["repeated"] for s in v2["control_plan"]["signals"]] == [False, True]
    art = exp["dmaic"]["control"]["artifact"]
    assert art["rollback"]["to"] == "v1" and art["rollback"]["case_id"] == CTL
    assert art["confirmation"][CTL]["runs"] == [rid("confirmation", CTL, 0), rid("confirmation", CTL, 1)]
    assert art["new_cycle"]["evidence"] == ["development"]
    assert w.store.get("experiments", art["new_cycle"]["experiment_id"]) is not None
    assert exp["dmaic"]["control"]["status"] == "stopped"
    assert any("rolled back" in r for r in exp["dmaic"]["control"]["tollgate"]["reasons"])
    assert any(r.startswith("rolled back") for r in v2["decision_reasons"])


def test_signal_that_clears_on_rerun_does_not_roll_back():
    w = accepted(make_outcome(confirmation=lambda case, attempt: ("E5",) if case == CTL and attempt == 0 else ()))
    result = control(w.deps, "exp1", confirm=True)
    v2 = w.version("v2")
    assert result["passed"] is True
    assert v2["pinned"] is True and v2["status"] == "accepted"
    assert v2["control_plan"]["status"] == "in_control"
    assert [s["repeated"] for s in v2["control_plan"]["signals"]] == [False]
    art = w.experiment()["dmaic"]["control"]["artifact"]
    assert art["rollback"] is None and "new_cycle" not in art
    assert art["confirmation"][CTL]["final"] == "in_control"
    assert any("signal cleared" in r for r in result["reasons"])


def test_confirmation_with_no_signal_passes():
    w = accepted()
    result = control(w.deps, "exp1", confirm=True)
    assert result["passed"] is True
    assert {v["final"] for v in result["artifact"]["confirmation"].values()} == {"in_control"}
    confirm_calls = [c for c in w.runner.calls if c["arm"] == "confirmation"]
    assert len(confirm_calls) == 1 and confirm_calls[0]["version_id"] == "v2"


# ---------------------------------------------------------------- cycle and CLI


def test_cycle_runs_every_phase_and_passes():
    w = World(make_outcome(), script=[PROBLEM, root_cause(DEV_RUN, "method"), proposal()])
    w.baseline()
    result = cycle(w.deps, "exp1", confirm=True)
    assert result["stopped_at"] is None
    assert [s["phase"] for s in result["steps"]] == ["define", "measure", "analyze", "improve", "pilot", "improve", "control"]
    exp = w.experiment()
    assert all(exp["dmaic"][p]["status"] == "passed" for p in ("define", "measure", "analyze", "improve", "control"))
    assert exp["decision"] == "accepted" and w.version("v2")["pinned"] is True


def test_cycle_stops_at_the_first_failed_tollgate():
    w = World(make_outcome(), script=[PROBLEM, root_cause(DEV_RUN, "environment")])
    w.baseline()
    result = cycle(w.deps, "exp1")
    assert result["stopped_at"] == "analyze"
    exp = w.experiment()
    assert exp["dmaic"]["improve"]["status"] == "not_reached"
    assert not [c for c in w.runner.calls if c["arm"] in ("memory_only", "candidate")]


def test_cli_phase_commands(monkeypatch, capsys):
    w = World(make_outcome(), script=[PROBLEM])
    w.baseline()
    monkeypatch.setattr(cli, "build_deps", lambda: w.deps)
    # through the project dispatcher (options after "--") and the lane's own entry point
    assert entry.main(["define", "--", "--experiment", "exp1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["phase"] == "define" and out["passed"] is True
    assert cli.main(["analyze", "--experiment", "exp1", "--snapshot", "M1"]) == 1  # measure has not passed
    assert "measure phase has not passed" in capsys.readouterr().err
    assert cli.main(["measure", "--experiment", "exp1"]) == 0
    with pytest.raises(SystemExit):
        cli.main(["improve", "--experiment", "exp1"])  # needs --from and --snapshot, or --verify
    assert cli.main(["nonsense"]) == 2

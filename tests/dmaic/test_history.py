"""Proposal history: shown to the improvement agent with development-case outcomes only,
recorded in the improve artifact, and used to refuse exact repeats of rejected changes."""

from __future__ import annotations

from adaptive_harness.contracts import load_config
from adaptive_harness.contracts.records import Experiment, HarnessVersion
from adaptive_harness.dmaic.improve import propose
from adaptive_harness.dmaic.validate import validate_proposal
from adaptive_harness.store.records import save_experiment, save_version

from .fakes import CTL, HELD, T0
from .test_improve_control_cycle import LESSON, analyzed, make_outcome, proposal
from .test_validate import LESSONS, change, check, parent
from .test_validate import proposal as bare

X1 = {"op": "add", "path": "/stages/0", "value": "X1"}
K3 = {"op": "replace", "path": "/context_policy/lessons/k", "value": 3}
R2_DETAIL = "development checks passed: candidate worst 14, current best 14"
R3_DETAIL = "held-out checks passed: candidate 5, current 8"
R1_DETAIL = f"case {CTL}: E4 passed under the current version but not the candidate"


def seed(w, vid, status, ops, acceptance=None, reasons=()):
    cfg = load_config()
    save_version(w.store, HarnessVersion(
        _id=vid, parent_id="v1", config=cfg.to_dict(), config_hash=cfg.config_hash(),
        changes=[{"op": o, "lesson_id": LESSON, "why": "earlier attempt"} for o in ops],
        rationale="r", expected_effect="e", risks=f"{HELD} might regress", status=status, pinned=False,
        decision_reasons=list(reasons), created_at=T0,
    ))
    if acceptance is not None:
        save_experiment(w.store, Experiment(
            _id=f"exp-{vid}", cases=[{"id": "dev1", "set": "development"}, {"id": HELD, "set": "held_out"},
                                     {"id": CTL, "set": "control"}],
            arms=["baseline"], acceptance=acceptance, decision="rejected", candidate_version=vid, created_at=T0,
        ))


def verdicts(r1=True, r2=True, r3=True):
    return {
        "R0": {"holds": True, "detail": f"every run valid; {HELD} rerun once"},
        "R1": {"holds": r1, "detail": R1_DETAIL if not r1 else "no regression on 1 control case(s)"},
        "R2": {"holds": r2, "detail": R2_DETAIL},
        "R3": {"holds": r3, "detail": R3_DETAIL},
        "R4": {"holds": True, "detail": "wall-clock seconds: candidate 120.0, current 118.0, limit 1.5x"},
    }


def prompt_of(w):
    return w.model.calls[-1]["messages"][0]["content"]


# ---------------------------------------------------------------- the validator


def test_exact_repeat_is_refused_with_the_version_id():
    def run(p):
        return validate_proposal(p, parent(), LESSONS, new_version_id="v3", refused={"v2": [X1, K3]})[2]

    reasons = run(bare(change("add", "/stages/0", "X1"),
                       change("replace", "/context_policy/lessons/k", 3, lesson="L-material")))
    assert reasons == ["repeats rejected proposal v2"]
    # order of ops does not matter
    reasons = run(bare(change("replace", "/context_policy/lessons/k", 3, lesson="L-material"),
                       change("add", "/stages/0", "X1")))
    assert reasons == ["repeats rejected proposal v2"]
    # a partial overlap, or the same path with another value, is allowed
    assert run(bare(change("add", "/stages/0", "X1"))) == []
    assert run(bare(change("add", "/stages/0", "X1"),
                    change("replace", "/context_policy/lessons/k", 4, lesson="L-material"))) == []


def test_no_refused_sets_validates_as_before():
    assert check(bare(change("add", "/stages/0", "X1")))[2] == []


# ---------------------------------------------------------------- the improve phase


def test_history_is_shown_with_development_outcomes_only():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    seed(w, "v2", "rejected", [K3], verdicts(r1=False, r2=False, r3=False),
         reasons=[f"R1 no regression: {R1_DETAIL}", f"R2 improvement: {R2_DETAIL}", f"R3 held-out: {R3_DETAIL}"])
    seed(w, "v3", "rejected", [{"op": "replace", "path": "/context_policy/lessons/k", "value": 4}],
         verdicts(r3=False), reasons=[f"R3 held-out: {R3_DETAIL}"])
    seed(w, "v4", "rolled_back", [{"op": "replace", "path": "/context_policy/lessons/k", "value": 2}])

    result = propose(w.deps, "exp1", "v1", "M1")
    assert result["valid"] and result["candidate_version"] == "v5"
    assert result["artifact"]["history"] == ["v2", "v3", "v4"]
    assert w.experiment()["dmaic"]["improve"]["artifact"]["history"] == ["v2", "v3", "v4"]

    prompt = prompt_of(w)
    assert "Earlier proposals for this task" in prompt and "will be refused" in prompt
    for vid in ("v2", "v3", "v4"):
        assert f'"version_id": "{vid}"' in prompt
    assert '"path": "/context_policy/lessons/k"' in prompt and LESSON in prompt
    assert R2_DETAIL in prompt
    assert "rejected on a rule that uses non-development cases (R1, R3)" in prompt
    assert "rejected on a rule that uses non-development cases (R3)" in prompt
    assert "rolled back by the control rule" in prompt
    # no held-out or control results, counts, or case ids
    for leak in (R3_DETAIL, R1_DETAIL, "candidate 5, current 8", HELD, CTL, "120.0", "rerun once"):
        assert leak not in prompt


def test_exact_repeat_of_a_rejected_version_is_refused_in_improve():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    seed(w, "v2", "rejected", [X1], verdicts(r2=False), reasons=[f"R2 improvement: {R2_DETAIL}"])
    result = propose(w.deps, "exp1", "v1", "M1")
    assert result["valid"] is False
    assert "repeats rejected proposal v2" in result["reasons"]
    assert w.store.get("harness_versions", "v3") is None
    assert w.experiment()["dmaic"]["improve"]["status"] == "stopped"


def test_candidate_and_accepted_versions_are_shown_but_not_refused():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    seed(w, "v2", "candidate", [X1])
    result = propose(w.deps, "exp1", "v1", "M1")
    assert result["valid"] and result["artifact"]["history"] == ["v2"]
    assert "no acceptance decision recorded" in prompt_of(w)


def test_first_cycle_has_no_history_and_an_unchanged_prompt():
    w = analyzed(make_outcome(), extra_script=[proposal()])
    result = propose(w.deps, "exp1", "v1", "M1")
    assert result["valid"] and result["artifact"]["history"] == []
    assert "Earlier proposals" not in prompt_of(w)

"""Control: pin the accepted candidate, arm its control plan, and confirm it.

The plan follows the specification's control rule: a threshold per case (checks passed
in the pilot), the resolved model assignment and FastF1 version it covers, and the
reaction plan. With `confirm`, the confirmation arm runs through the runner and the
evaluator scores it, applying the control check to every run of the pinned version. A
case that signals is rerun once; if the signal repeats, the parent version is pinned
again, the candidate is marked rolled_back, and a new cycle is opened. Tollgate C: the
plan is armed and every confirmation case meets its threshold.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from ..contracts.config import ResolvedModel
from ..contracts.records import ControlPlan, Experiment, HarnessVersion
from ..store.records import get_experiment, get_version, pin_version, save_experiment, update_version
from .common import (
    PhaseError,
    evaluation_of,
    is_harness,
    is_control_record,
    load_experiment,
    passed_count,
    record_phase,
    require_passed,
    run_arm,
    runs_of,
    valid_scored,
)
from .ports import Deps

CONFIRMATION_ARM = "confirmation"
ACTIVE = ("armed", "in_control", "signal")


def reaction_plan(deps: Deps) -> str:
    rule = deps.cycle.control
    return (
        f"A later run of this version on a case signals when it passes fewer checks than the case's threshold. "
        f"Rerun that case {rule.reruns_on_signal} time(s); if the signal repeats, pin the parent version, mark "
        f"this version rolled_back, and open a new cycle using {', '.join(rule.new_cycle_evidence)}-case evidence "
        f"only. A run under a different model assignment or FastF1 version is outside the plan: flagged, not "
        f"compared. {rule.note}"
    )


def build_plan(deps: Deps, experiment_id: str) -> ControlPlan:
    """Thresholds from the candidate's pilot runs: per case, the fewest checks passed."""
    arm = deps.acceptance.compare["candidate"].arm
    scored = valid_scored(deps, runs_of(deps, experiment_id, arm))
    if not scored:
        raise PhaseError("the candidate arm has no valid scored run to set thresholds from")
    thresholds: dict[str, int] = {}
    for run, ev in scored:
        n = passed_count(ev)
        thresholds[run["case_id"]] = min(n, thresholds.get(run["case_id"], n))
    models = {(r["model"]["provider"], tuple(sorted(r["model"]["assignment"].items()))) for r, _ in scored}
    fastf1 = {r["fastf1_version"] for r, _ in scored}
    if len(models) != 1 or len(fastf1) != 1:
        raise PhaseError("the candidate's pilot ran under more than one model assignment or FastF1 version")
    provider, assignment = models.pop()
    return ControlPlan(
        thresholds=dict(sorted(thresholds.items())),
        model=ResolvedModel(provider=provider, assignment=dict(assignment)),
        fastf1_version=fastf1.pop(),
        reaction_plan=reaction_plan(deps),
        status="armed",
    )


def control_outcome(deps: Deps, run_id: str) -> dict[str, Any]:
    """The evaluator's control-check record for a run."""
    for e in deps.store.find("events", {"run_id": run_id, "type": "check"}, sort=[("seq", 1)]):
        if is_control_record(e):
            return dict(e["content"])
    return {"control": "not_compared", "reasons": ["the evaluator recorded no control check for this run"]}


def _latest_valid(deps: Deps, run_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """case_id -> the latest confirmation run among run_ids that did not end HARNESS
    (or the latest run when every one did)."""
    latest: dict[str, dict[str, Any]] = {}
    for rid in run_ids:
        run = deps.store.get("runs", rid)
        if run is None:
            continue
        cur = latest.get(run["case_id"])
        harness = is_harness(run, evaluation_of(deps, rid))
        if cur is None or not harness or is_harness(cur, evaluation_of(deps, cur["_id"])):
            latest[run["case_id"]] = run
    return latest


def open_new_cycle(deps: Deps, experiment_id: str) -> str:
    exp = load_experiment(deps, experiment_id)
    base = experiment_id
    k = 2
    while get_experiment(deps.store, f"{base}-cycle{k}") is not None:
        k += 1
    new_id = f"{base}-cycle{k}"
    save_experiment(deps.store, Experiment(_id=new_id, cases=exp.cases, arms=[], created_at=deps.now()))
    return new_id


def rollback(deps: Deps, version: HarnessVersion, case_id: str, run_ids: list[str]) -> dict[str, Any]:
    parent = version.parent_id or "v1"
    pin_version(deps.store, parent)
    current = get_version(deps.store, version.id)
    plan = current.control_plan.model_dump() if current and current.control_plan else None
    if plan is not None:
        plan["status"] = "rolled_back"
    reason = f"rolled back: case {case_id} signalled again after one rerun (runs {', '.join(run_ids)})"
    update_version(deps.store, version.id, {
        "status": "rolled_back",
        "control_plan": plan,
        "decision_reasons": [*(current.decision_reasons if current else []), reason],
    })
    return {"from": version.id, "to": parent, "case_id": case_id, "runs": run_ids, "reason": reason}


def confirm_plan(deps: Deps, experiment_id: str, version: HarnessVersion) -> tuple[bool, list[str], dict[str, Any]]:
    plan = version.control_plan
    cases = list(plan.thresholds)
    snapshot = deps.acceptance.compare["candidate"].snapshot
    kw = dict(experiment_id=experiment_id, arm=CONFIRMATION_ARM, version_id=version.id, snapshot=snapshot)

    first = run_arm(deps, case_ids=cases, **kw)
    deps.evaluator.score(first)
    by_case: dict[str, list[str]] = defaultdict(list)
    for rid in first:
        by_case[(deps.store.get("runs", rid) or {}).get("case_id")].append(rid)
    latest = _latest_valid(deps, first)

    report: dict[str, Any] = {}
    reasons: list[str] = []
    rolled_back: dict[str, Any] | None = None
    for case_id in cases:
        run = latest.get(case_id)
        if run is None:
            reasons.append(f"case {case_id}: no confirmation run")
            report[case_id] = {"runs": [], "outcomes": [], "final": "missing"}
            continue
        outcomes = [control_outcome(deps, run["_id"])]
        runs = list(by_case[case_id])
        if outcomes[-1].get("control") == "signal":
            again = run_arm(deps, case_ids=[case_id], **kw)
            deps.evaluator.score(again)
            runs += again
            rerun = _latest_valid(deps, again).get(case_id)
            outcomes.append(control_outcome(deps, rerun["_id"]) if rerun else
                            {"control": "not_compared", "reasons": ["the rerun did not finish"]})
            last = outcomes[-1]
            if last.get("control") == "signal" and last.get("repeated") and rolled_back is None:
                rolled_back = rollback(deps, version, case_id, runs)
        final = outcomes[-1].get("control")
        report[case_id] = {"runs": runs, "outcomes": outcomes, "final": final,
                           "threshold": plan.thresholds[case_id]}
        if final == "in_control":
            note = " after one rerun (signal cleared)" if len(outcomes) > 1 else ""
            reasons.append(f"case {case_id}: {outcomes[-1].get('passed')} >= threshold {plan.thresholds[case_id]}{note}")
        elif final == "signal":
            reasons.append(f"case {case_id}: passed {outcomes[-1].get('passed')} < threshold {plan.thresholds[case_id]}"
                           + (" again after one rerun" if len(outcomes) > 1 else ""))
        else:
            detail = "; ".join(outcomes[-1].get("reasons") or [])
            reasons.append(f"case {case_id}: {final}" + (f" ({detail})" if detail else ""))

    passed = all(v["final"] == "in_control" for v in report.values()) and rolled_back is None
    artifact = {"confirmation": report, "rollback": rolled_back}
    if rolled_back is not None:
        new_id = open_new_cycle(deps, experiment_id)
        artifact["new_cycle"] = {"experiment_id": new_id, "evidence": list(deps.cycle.control.new_cycle_evidence),
                                 "pinned": rolled_back["to"]}
        reasons.insert(0, rolled_back["reason"] + f"; {rolled_back['to']} pinned again; new cycle {new_id} opened")
    return passed, reasons, artifact


def control(deps: Deps, experiment_id: str, confirm: bool = False) -> dict[str, Any]:
    exp = load_experiment(deps, experiment_id)
    require_passed(exp, "improve")
    if exp.decision != "accepted" or not exp.candidate_version:
        raise PhaseError(f"experiment {experiment_id!r} has no accepted candidate")
    version = get_version(deps.store, exp.candidate_version)
    if version is None:
        raise PhaseError(f"candidate version {exp.candidate_version!r} is missing")
    if version.status == "rolled_back":
        raise PhaseError(f"version {version.id} was rolled back")
    if version.status != "accepted":
        raise PhaseError(f"version {version.id} is {version.status}, not accepted")

    if version.control_plan is None or version.control_plan.status not in ACTIVE:
        plan = build_plan(deps, experiment_id)
        update_version(deps.store, version.id, {"control_plan": plan.model_dump()})
    pin_version(deps.store, version.id)
    version = get_version(deps.store, version.id)
    artifact: dict[str, Any] = {"version": version.id, "config_hash": version.config_hash, "pinned": True,
                                "control_plan": version.control_plan.model_dump()}

    if not confirm:
        reasons = [f"{version.id} pinned (config {version.config_hash[:12]}); control plan armed for "
                   f"{len(version.control_plan.thresholds)} case(s); no confirmation arm run"]
        record_phase(deps, experiment_id, "control", artifact, True, reasons)
        return {"phase": "control", "passed": True, "reasons": reasons, "artifact": artifact}

    passed, reasons, extra = confirm_plan(deps, experiment_id, version)
    artifact.update(extra)
    after = get_version(deps.store, version.id)
    artifact["control_plan"] = after.control_plan.model_dump() if after and after.control_plan else None
    artifact["pinned"] = bool(after and after.pinned)
    record_phase(deps, experiment_id, "control", artifact, passed, reasons, decision=None if passed else "stopped")
    return {"phase": "control", "passed": passed, "reasons": reasons, "artifact": artifact}


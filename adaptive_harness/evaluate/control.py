"""The control check (SPECIFICATION.md, control rule).

A later run of the pinned version on a case is compared with the plan's threshold for
that case (checks passed in the pilot). Passing fewer is a signal; a second signal on the
same case with no in-control run between is a repeated signal. A run under a different
model assignment (any role) or FastF1 version is outside the plan: flagged, not compared.

Every outcome is recorded as a `check` event on the run (content.control), and signals
are appended to the plan. Reacting to a repeated signal (pin the parent, roll back, open
a new cycle) is the dmaic lane's job.
"""

from __future__ import annotations

from typing import Any

from ..contracts.common import canonical_sha256
from ..contracts.records import Event, Signal, event_id
from .core import EvaluationError, Evaluator, now

ACTIVE_PLAN = ("armed", "in_control", "signal")
COMPARED = ("in_control", "signal")


class NoControlPlan(EvaluationError):
    """The run's version is not pinned or has no active control plan."""


def _plan_problems(run: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    reasons = []
    run_model = run.get("model") or {}
    plan_model = plan.get("model") or {}
    if run_model.get("provider") != plan_model.get("provider"):
        reasons.append(f"provider {run_model.get('provider')!r} differs from the plan's {plan_model.get('provider')!r}")
    run_assign = run_model.get("assignment") or {}
    plan_assign = plan_model.get("assignment") or {}
    for role in sorted(set(run_assign) | set(plan_assign)):
        if run_assign.get(role) != plan_assign.get(role):
            reasons.append(f"model for {role} {run_assign.get(role)!r} differs from the plan's {plan_assign.get(role)!r}")
    if run.get("fastf1_version") != plan.get("fastf1_version"):
        reasons.append(
            f"FastF1 {run.get('fastf1_version')!r} differs from the plan's {plan.get('fastf1_version')!r}"
        )
    return reasons


def _control_events(ev: Evaluator, version_id: str) -> list[dict[str, Any]]:
    return ev.store.find("events", {"type": "check", "content.control": {"$exists": True}, "content.version": version_id})


def _record(ev: Evaluator, run_id: str, content: dict[str, Any]) -> None:
    seq = ev.store.next_seq(run_id)
    doc = Event(
        _id=event_id(run_id, seq),
        run_id=run_id,
        seq=seq,
        type="check",
        content=content,
        content_sha256=canonical_sha256(content),
        created_at=now(),
    ).to_doc()
    ev.store.append_event(doc)


def _history(ev: Evaluator, version_id: str, case_id: str) -> list[dict[str, Any]]:
    """Compared outcomes for this version and case, oldest run first."""
    out = []
    for e in _control_events(ev, version_id):
        c = e["content"]
        if c.get("case_id") != case_id or c.get("control") not in COMPARED:
            continue
        run = ev.store.get("runs", e["run_id"]) or {}
        out.append((run.get("started_at") or e["created_at"], e["created_at"], c))
    out.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in out]


def check_run(ev: Evaluator, run_id: str) -> dict[str, Any]:
    """Apply the control check to one run and record the outcome. Idempotent per run."""
    run = ev.run(run_id)
    version_id = run.get("harness_version")
    version = ev.store.get("harness_versions", version_id or "")
    plan = (version or {}).get("control_plan")
    if not version or not version.get("pinned") or not plan or plan.get("status") not in ACTIVE_PLAN:
        raise NoControlPlan(f"run {run_id!r}: version {version_id!r} is not pinned with an active control plan")

    for e in ev.store.find("events", {"run_id": run_id, "type": "check", "content.version": version_id}):
        if "control" in (e.get("content") or {}):
            return {**e["content"], "run_id": run_id, "already_recorded": True}

    case_id = run["case_id"]
    content: dict[str, Any] = {"version": version_id, "case_id": case_id}
    problems = _plan_problems(run, plan)
    threshold = (plan.get("thresholds") or {}).get(case_id)
    if problems:
        content.update(control="outside_plan", reasons=problems)
    elif run.get("status") == "harness_error":
        content.update(control="not_compared", reasons=["the run ended HARNESS"])
    elif threshold is None:
        content.update(control="not_compared", reasons=[f"the plan has no threshold for case {case_id}"])
    else:
        evaluation = ev.evaluation_for(run_id, control=False)
        passed = sum(1 for c in evaluation["checks"] if c["result"] == "PASS")
        prior = _history(ev, version_id, case_id)
        if passed < threshold:
            repeated = bool(prior) and prior[-1]["control"] == "signal"
            content.update(control="signal", passed=passed, threshold=threshold, repeated=repeated)
            signals = list(plan.get("signals") or [])
            signals.append(
                Signal(run_id=run_id, case_id=case_id, passed=passed, threshold=threshold, repeated=repeated).model_dump()
            )
            ev.store.update("harness_versions", version_id, {"control_plan.signals": signals})
        else:
            content.update(control="in_control", passed=passed, threshold=threshold)

    _record(ev, run_id, content)
    if content["control"] in COMPARED:
        ev.store.update("harness_versions", version_id, {"control_plan.status": _plan_status(ev, version_id)})
    return {**content, "run_id": run_id, "already_recorded": False}


def _plan_status(ev: Evaluator, version_id: str) -> str:
    """signal while any case's latest compared run signalled, else in_control."""
    cases = {e["content"]["case_id"] for e in _control_events(ev, version_id)}
    for case_id in cases:
        hist = _history(ev, version_id, case_id)
        if hist and hist[-1]["control"] == "signal":
            return "signal"
    return "in_control"


def apply_if_pinned(ev: Evaluator, run_id: str) -> dict[str, Any] | None:
    """Scoring any run of a pinned version with an active plan applies the control check."""
    try:
        return check_run(ev, run_id)
    except NoControlPlan:
        return None

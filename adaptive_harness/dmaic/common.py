"""Shared helpers for the DMAIC phases: experiment records, case sets, arms, verdicts,
the improvement agent call, and event compaction."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

from ..contracts.common import CHECK_IDS, canonical_sha256
from ..contracts.protocol import ChatMessage
from ..contracts.records import Experiment, ExperimentCase, Phase, Tollgate
from ..store.records import get_experiment, save_experiment, save_phase, update_experiment
from .ports import IMPROVEMENT_ROLE, Deps

HARNESS_STATUS = "harness_error"
PHASE_ORDER = ("define", "measure", "analyze", "improve", "control")


class PhaseError(Exception):
    """A phase cannot run (a precondition is missing). Not a tollgate stop."""


# ---------------------------------------------------------------- experiments and case sets


def load_experiment(deps: Deps, experiment_id: str) -> Experiment:
    exp = get_experiment(deps.store, experiment_id)
    if exp is None:
        raise PhaseError(f"no experiment {experiment_id!r}")
    return exp


def ensure_experiment(deps: Deps, experiment_id: str) -> Experiment:
    """The experiment record, created from its baseline runs when it does not exist yet."""
    exp = get_experiment(deps.store, experiment_id)
    if exp is not None:
        return exp
    runs = runs_of(deps, experiment_id, "baseline")
    if not runs:
        raise PhaseError(f"experiment {experiment_id!r} has no baseline runs")
    from examples.neutralization_brief.task import load_task

    cases = []
    for cid in dict.fromkeys(r["case_id"] for r in runs):
        cases.append(ExperimentCase(id=cid, set=load_task(cid).set))
    exp = Experiment(_id=experiment_id, cases=cases, arms=["baseline"], created_at=deps.now())
    save_experiment(deps.store, exp)
    return exp


def case_sets(exp: Experiment) -> dict[str, str]:
    """case_id -> set, from the experiment record (the only source this lane trusts)."""
    return {c.id: c.set for c in exp.cases}


def cases_in(exp: Experiment, *sets: str) -> list[str]:
    return [c.id for c in exp.cases if c.set in sets]


def require_passed(exp: Experiment, phase: str) -> None:
    rec: Phase = getattr(exp.dmaic, phase)
    if rec.status != "passed":
        raise PhaseError(f"the {phase} phase has not passed (status {rec.status})")


def record_phase(
    deps: Deps,
    experiment_id: str,
    phase: str,
    artifact: Mapping[str, Any],
    passed: bool,
    reasons: Sequence[str],
    *,
    decision: str | None = None,
    status: str | None = None,
) -> Tollgate:
    """Write the phase's artifact and tollgate; on a stop, reset later phases to
    not_reached and record the decision."""
    gate = Tollgate(passed=passed, reasons=list(reasons))
    save_phase(deps.store, experiment_id, phase, artifact, gate, status=status, completed_at=deps.now())
    fields: dict[str, Any] = {}
    later = PHASE_ORDER[PHASE_ORDER.index(phase) + 1 :]
    if status != "not_reached":
        for p in later:
            fields[f"dmaic.{p}"] = Phase().model_dump()
    if decision is not None:
        fields["decision"] = decision
    if fields:
        update_experiment(deps.store, experiment_id, fields)
    return gate


def add_arm(deps: Deps, experiment_id: str, arm: str) -> None:
    exp = load_experiment(deps, experiment_id)
    if arm not in exp.arms:
        update_experiment(deps.store, experiment_id, {"arms": [*exp.arms, arm]})


# ---------------------------------------------------------------- runs and verdicts


def runs_of(deps: Deps, experiment_id: str, arm: str) -> list[dict[str, Any]]:
    return deps.store.find("runs", {"experiment_id": experiment_id, "arm": arm}, sort=[("started_at", 1), ("_id", 1)])


def evaluation_of(deps: Deps, run_id: str) -> dict[str, Any] | None:
    found = deps.store.find("evaluations", {"run_id": run_id}, sort=[("created_at", -1)], limit=1)
    return found[0] if found else None


def is_harness(run: Mapping[str, Any], evaluation: Mapping[str, Any] | None) -> bool:
    if run.get("status") == HARNESS_STATUS:
        return True
    return bool(evaluation) and any(c["result"] == "HARNESS" for c in evaluation["checks"])


def passed_count(evaluation: Mapping[str, Any]) -> int:
    return sum(1 for c in evaluation["checks"] if c["result"] == "PASS")


def score_missing(deps: Deps, runs: Sequence[Mapping[str, Any]]) -> None:
    """Ask the evaluator to score finished runs that have no evaluation yet."""
    missing = [r["_id"] for r in runs if r.get("status") is not None and evaluation_of(deps, r["_id"]) is None]
    if missing:
        deps.evaluator.score(missing)


def valid_scored(deps: Deps, runs: Sequence[Mapping[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(run, evaluation) for finished, scored, non-HARNESS runs."""
    out = []
    for r in runs:
        if r.get("status") is None:
            continue
        ev = evaluation_of(deps, r["_id"])
        if ev is None or is_harness(r, ev):
            continue
        out.append((dict(r), ev))
    return out


def dpo_table(evaluations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Defects, opportunities, and DPO overall and by check."""
    by_check: dict[str, dict[str, int]] = {cid: {"defects": 0, "opportunities": 0} for cid in CHECK_IDS}
    categories: dict[str, int] = defaultdict(int)
    for ev in evaluations:
        for c in ev["checks"]:
            if c["result"] in ("PASS", "FAIL"):
                by_check[c["id"]]["opportunities"] += 1
            if c["result"] == "FAIL":
                by_check[c["id"]]["defects"] += 1
                if c.get("category"):
                    categories[c["category"]] += 1
    defects = sum(v["defects"] for v in by_check.values())
    opps = sum(v["opportunities"] for v in by_check.values())
    return {
        "runs": len(evaluations),
        "defects": defects,
        "opportunities": opps,
        "dpo": round(defects / opps, 4) if opps else None,
        "by_check": {
            cid: {**v, "dpo": round(v["defects"] / v["opportunities"], 4) if v["opportunities"] else None}
            for cid, v in by_check.items()
        },
        "failure_categories": dict(sorted(categories.items())),
    }


def run_arm(
    deps: Deps,
    *,
    experiment_id: str,
    arm: str,
    version_id: str,
    snapshot: str,
    case_ids: Sequence[str],
) -> list[str]:
    """Run the cases for an arm through the runner, rerunning each case that ends
    HARNESS once (the specification's one rerun). Returns every new run id."""
    if not case_ids:
        return []
    add_arm(deps, experiment_id, arm)
    kw = dict(experiment_id=experiment_id, arm=arm, version_id=version_id, snapshot=snapshot)
    run_ids = list(deps.runner.run_cases(case_ids=list(case_ids), **kw))
    harness = []
    for rid in run_ids:
        run = deps.store.get("runs", rid) or {}
        if run.get("status") == HARNESS_STATUS:
            harness.append(run["case_id"])
    if harness:
        deps.log(f"{arm}: rerunning {len(harness)} case(s) that ended HARNESS: {', '.join(harness)}")
        run_ids += deps.runner.run_cases(case_ids=harness, **kw)
    return run_ids


# ---------------------------------------------------------------- the improvement agent


def ask_agent(deps: Deps, system: str, user: str, json_schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One improvement-agent call on the improvement_agent role. Returns the parsed JSON
    reply (or None) with the text and call metadata for the phase artifact."""
    resp = deps.model.complete(
        IMPROVEMENT_ROLE, system, [ChatMessage(role="user", content=user)], json_schema=json_schema
    )
    parsed = resp.parsed if isinstance(resp.parsed, dict) else _parse_json(resp.text)
    return {
        "parsed": parsed,
        "text": resp.text,
        "call": {
            "role": IMPROVEMENT_ROLE,
            "model": resp.model,
            "usage": resp.usage.model_dump(),
            "stop_reason": resp.stop_reason,
            "prompt_sha256": canonical_sha256({"system": system, "user": user}),
        },
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------- events for prompts


def compact(value: Any, width: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def is_control_record(event: Mapping[str, Any]) -> bool:
    """The evaluator's control-check records are measurement output, not run evidence."""
    content = event.get("content")
    return event.get("type") == "check" and isinstance(content, Mapping) and "control" in content


def compact_events(events: Sequence[Mapping[str, Any]], width: int = 600) -> list[dict[str, Any]]:
    """A run's recorded events as short lines for a prompt (no context manifests)."""
    out = []
    for e in events:
        if is_control_record(e):
            continue
        out.append(
            {
                "id": e["_id"],
                "stage": e.get("stage_id"),
                "role": e.get("role"),
                "type": e.get("type"),
                "refs": list(e.get("refs") or []),
                "content": compact(e.get("content"), width),
            }
        )
    return out


def run_events(deps: Deps, run_id: str) -> list[dict[str, Any]]:
    return deps.store.find("events", {"run_id": run_id}, sort=[("seq", 1)])


def first_sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join((text or "").split()))
    return " ".join(p for p in parts[:n] if p)

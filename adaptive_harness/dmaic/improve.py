"""Improve: proposal, pilot, and tollgate I.

`propose` retrieves the verified controllable root causes (vector search, with the
metadata fallback) and the history of earlier proposals (development-case outcomes only),
asks the improvement agent for changes, validates them in code, and
saves a valid proposal as candidate version vN (status candidate, not pinned) or records
the rejection. `pilot` runs the current version (arm memory_only) and the candidate (arm
candidate) fresh on the same memory snapshot and cases. `verify` scores the pilot and
calls the evaluator's acceptance rules in its own process; tollgate I is its decision.
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts.common import CONTROLLABLE_CAUSES
from ..contracts.config import HarnessConfig
from ..contracts.records import HarnessVersion, Validation
from ..store.records import (
    get_experiment,
    get_version,
    pin_version,
    pinned_version,
    save_version,
    search_lessons,
    search_lessons_by_metadata,
    update_experiment,
)
from . import prompts
from .common import (
    PhaseError,
    ask_agent,
    case_sets,
    compact_events,
    load_experiment,
    record_phase,
    require_passed,
    run_arm,
    runs_of,
    score_missing,
    sync_arms,
)
from .history import proposal_history, refused_change_sets
from .ports import Deps
from .validate import validate_proposal

RETRIEVE_K = 10
EXCERPT_WIDTH = 400


def next_version_id(deps: Deps) -> str:
    nums = [int(m.group(1)) for d in deps.store.find("harness_versions")
            if (m := re.fullmatch(r"v(\d+)", str(d["_id"])))]
    return f"v{max(nums, default=1) + 1}"


def _query_text(define_artifact: dict[str, Any]) -> str:
    charter = define_artifact.get("charter") or {}
    parts = [define_artifact.get("problem_statement") or ""]
    parts += [f"{c['id']} {c['name']} {c['failure_category']}" for c in charter.get("ctqs", [])]
    return " ".join(p for p in parts if p)


def _case_set(case_id: str) -> str | None:
    """The set of a case outside this experiment (a lesson from an earlier cycle)."""
    try:
        from examples.neutralization_brief.task import load_task

        return load_task(case_id).set
    except (KeyError, FileNotFoundError):
        return None


def retrieve_root_causes(deps: Deps, experiment_id: str, snapshot: str, query: str) -> tuple[list[dict[str, Any]], str]:
    """Verified controllable development-case root causes on the snapshot. Vector search
    first; the metadata fallback when it fails or finds none."""
    exp = load_experiment(deps, experiment_id)
    sets = case_sets(exp)
    allowed_sets = set(deps.cycle.evidence_rule["improvement_prompt"])
    filters = {"status": "verified", "scope": deps.cycle.lesson_scope}

    def keep(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for h in hits:
            case_set = sets.get(h["defect"]["case_id"]) or _case_set(h["defect"]["case_id"])
            if (h.get("status") == "verified" and h.get("controllable")
                    and h.get("cause_category") in CONTROLLABLE_CAUSES and case_set in allowed_sets):
                out.append(h)
        return out

    method = "vector"
    try:
        hits = keep(search_lessons(deps.store, deps.embed_one_query(query), RETRIEVE_K, filters, [snapshot]))
    except Exception as e:  # the vector index may be missing or still building
        deps.log(f"improve: vector search failed ({e}); using the metadata fallback")
        hits = []
    if not hits:
        method = "metadata"
        hits = keep(search_lessons_by_metadata(deps.store, filters, RETRIEVE_K, [snapshot]))
    return hits, method


def _root_cause_view(deps: Deps, lesson: dict[str, Any]) -> dict[str, Any]:
    surfaces = {c.id: c.surface for c in deps.cycle.cause_categories}
    events = [e for e in (deps.store.get("events", eid) for eid in lesson["source_event_ids"]) if e is not None]
    return {
        "lesson_id": lesson["_id"],
        "cause_category": lesson["cause_category"],
        "surface": surfaces.get(lesson["cause_category"]),
        "defect": {"case_id": lesson["defect"]["case_id"], "check_id": lesson["defect"]["check_id"],
                   "failure_category": lesson["failure_category"]},
        "why_chain": lesson["why_chain"],
        "origin_event_id": lesson["origin_event_id"],
        "text": lesson["text"],
        "source_events": compact_events(events, EXCERPT_WIDTH),
    }


def propose(deps: Deps, experiment_id: str, from_version: str, snapshot: str) -> dict[str, Any]:
    exp = load_experiment(deps, experiment_id)
    require_passed(exp, "analyze")
    expected = deps.acceptance.compare["candidate"].snapshot
    if snapshot != expected:
        raise PhaseError(f"the candidate is piloted on snapshot {expected}, not {snapshot}")
    parent = get_version(deps.store, from_version)
    if parent is None:
        raise PhaseError(f"no harness version {from_version!r} (run init-db to register v1)")
    parent_config = HarnessConfig.model_validate(parent.config).to_dict()

    define_artifact = exp.dmaic.define.artifact or {}
    hits, method = retrieve_root_causes(deps, experiment_id, snapshot, _query_text(define_artifact))
    artifact: dict[str, Any] = {"from": from_version, "snapshot": snapshot, "retrieval": method,
                                "root_causes": [h["_id"] for h in hits]}
    if not hits:
        reasons = ["no verified controllable root cause could be retrieved"]
        record_phase(deps, experiment_id, "improve", artifact, False, reasons, decision="stopped")
        return {"phase": "improve", "stage": "proposal", "valid": False, "passed": False, "reasons": reasons,
                "artifact": artifact}

    charter = {**(define_artifact.get("charter") or {}), "problem_statement": define_artifact.get("problem_statement")}
    views = [_root_cause_view(deps, h) for h in hits]
    cap = parent.config.get("change_cap", 0)
    history = proposal_history(deps, parent_config.get("task_family"))
    artifact["history"] = [h["version_id"] for h in history]
    system, user = prompts.proposal(
        deps.cycle, [r.statement for r in deps.acceptance.rules], parent_config, charter, views, cap, history
    )
    reply = ask_agent(deps, system, user, prompts.proposal_schema(cap))
    proposal = reply["parsed"]
    version_id = next_version_id(deps)
    config, changes, problems = validate_proposal(
        proposal, parent_config, {h["_id"]: h for h in hits}, new_version_id=version_id,
        refused=refused_change_sets(history),
    )
    artifact.update(agent=reply["call"], proposal=proposal, validation={"valid": not problems, "reasons": problems})

    if problems:
        reasons = ["proposal rejected"] + problems
        record_phase(deps, experiment_id, "improve", artifact, False, reasons, decision="stopped")
        return {"phase": "improve", "stage": "proposal", "valid": False, "passed": False, "reasons": reasons,
                "artifact": artifact}

    cfg = HarnessConfig.model_validate(config)
    save_version(deps.store, HarnessVersion(
        _id=version_id,
        parent_id=from_version,
        config=cfg.to_dict(),
        config_hash=cfg.config_hash(),
        changes=changes,
        rationale=proposal["rationale"],
        expected_effect=proposal["expected_effect"],
        risks=proposal["risks"],
        validation=Validation(valid=True, reasons=[]),
        status="candidate",
        pinned=False,
        created_at=deps.now(),
    ))
    update_experiment(deps.store, experiment_id, {"candidate_version": version_id})
    artifact["candidate_version"] = version_id
    reasons = [f"proposal valid: candidate {version_id} saved; tollgate I waits for the pilot and the acceptance rules"]
    record_phase(deps, experiment_id, "improve", artifact, False, reasons, status="not_reached")
    return {"phase": "improve", "stage": "proposal", "valid": True, "passed": None, "reasons": reasons,
            "artifact": artifact, "candidate_version": version_id}


def _candidate(deps: Deps, experiment_id: str) -> HarnessVersion:
    exp = load_experiment(deps, experiment_id)
    art = exp.dmaic.improve.artifact or {}
    if not exp.candidate_version or not (art.get("validation") or {}).get("valid"):
        raise PhaseError(f"experiment {experiment_id!r} has no valid candidate (run improve first)")
    version = get_version(deps.store, exp.candidate_version)
    if version is None:
        raise PhaseError(f"candidate version {exp.candidate_version!r} is missing")
    return version


def pilot(deps: Deps, experiment_id: str) -> dict[str, list[str]]:
    """Run the current version (memory_only) and the candidate (candidate) fresh on the
    acceptance snapshot over every case of the experiment."""
    candidate = _candidate(deps, experiment_id)
    exp = load_experiment(deps, experiment_id)
    cases = [c.id for c in exp.cases]
    cur, cand = deps.acceptance.compare["current"], deps.acceptance.compare["candidate"]
    out = {}
    out[cur.arm] = run_arm(deps, experiment_id=experiment_id, arm=cur.arm, version_id=candidate.parent_id or "v1",
                           snapshot=cur.snapshot, case_ids=cases)
    out[cand.arm] = run_arm(deps, experiment_id=experiment_id, arm=cand.arm, version_id=candidate.id,
                            snapshot=cand.snapshot, case_ids=cases)
    return out


def verify(deps: Deps, experiment_id: str) -> dict[str, Any]:
    candidate = _candidate(deps, experiment_id)
    sync_arms(deps, experiment_id)
    exp = load_experiment(deps, experiment_id)
    arms = [deps.acceptance.compare["current"].arm, deps.acceptance.compare["candidate"].arm]
    pilot_runs = {arm: runs_of(deps, experiment_id, arm) for arm in arms}
    missing = [arm for arm, runs in pilot_runs.items() if not runs]
    if missing:
        raise PhaseError(f"the pilot arms have not run: {', '.join(missing)}")
    for runs in pilot_runs.values():
        score_missing(deps, runs)

    result = deps.evaluator.accept(experiment_id)  # the evaluator writes acceptance and decision
    exp = get_experiment(deps.store, experiment_id)
    decision = exp.decision if exp else result.get("decision")
    acceptance = exp.acceptance.model_dump() if exp and exp.acceptance else result.get("acceptance")
    artifact = {**(exp.dmaic.improve.artifact or {}),
                "pilot": {arm: [r["_id"] for r in runs] for arm, runs in pilot_runs.items()},
                "acceptance": acceptance, "decision": decision}
    if decision == "accepted":
        reasons = list(result.get("reasons") or ["all acceptance rules hold"])
        record_phase(deps, experiment_id, "improve", artifact, True, reasons)
        return {"phase": "improve", "stage": "verify", "passed": True, "reasons": reasons, "artifact": artifact}

    reasons = ["acceptance rules do not all hold; the current version stays pinned"]
    reasons += list(result.get("reasons") or [])
    pinned = pinned_version(deps.store)
    if pinned is None or pinned.id == candidate.id:
        pin_version(deps.store, candidate.parent_id or "v1")
    record_phase(deps, experiment_id, "improve", artifact, False, reasons, decision="rejected")
    return {"phase": "improve", "stage": "verify", "passed": False, "reasons": reasons, "artifact": artifact}

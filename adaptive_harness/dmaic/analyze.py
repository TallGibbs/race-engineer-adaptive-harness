"""Analyze: one root cause per development defect, verified in code, stored as lessons.

For each defect (a FAIL check on a development-case baseline run) the improvement agent
reads that run's compacted recorded events and returns a root cause. Code verifies it:
the cause category is on the list, the why-chain has 1 to why_chain_max statements, every
cited event exists in that run, the origin event is among them, and the defect is a real
FAIL in that run. Verified causes are embedded and stored as lessons (status verified,
snapshot M1); the rest stay provisional, without an embedding, and are never retrieved.
Tollgate A: at least one verified controllable root cause.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts.common import CONTROLLABLE_CAUSES
from ..contracts.records import Lesson
from . import prompts
from .common import (
    PhaseError,
    ask_agent,
    case_sets,
    compact_events,
    evaluation_of,
    load_experiment,
    record_phase,
    require_passed,
    run_events,
    runs_of,
    valid_scored,
)
from .ports import Deps


def lesson_id(experiment_id: str, run_id: str, check_id: str) -> str:
    return f"lesson:{experiment_id}:{run_id}:{check_id}"


def case_info(case_id: str) -> dict[str, Any]:
    """The case as the harness saw it (from data/cases.json, never the key)."""
    try:
        from examples.neutralization_brief.task import load_task

        t = load_task(case_id)
        return {"id": t.id, "type": t.type, "question": t.question}
    except (KeyError, FileNotFoundError):
        return {"id": case_id, "type": "unknown", "question": "(not available)"}


def development_defects(deps: Deps, experiment_id: str) -> list[dict[str, Any]]:
    exp = load_experiment(deps, experiment_id)
    sets = case_sets(exp)
    allowed = set(deps.cycle.evidence_rule["analyze"])
    runs = [r for r in runs_of(deps, experiment_id, "baseline") if sets.get(r["case_id"]) in allowed]
    defects = []
    for run, ev in valid_scored(deps, runs):
        for c in ev["checks"]:
            if c["result"] == "FAIL":
                defects.append({"run_id": run["_id"], "case_id": run["case_id"], "check_id": c["id"],
                                "category": c.get("category")})
    return defects


def verify(deps: Deps, defect: Mapping[str, Any], reply: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Check a root-cause reply against the record. Returns (normalized fields or None
    when the reply is unusable, problems). No problems means verified."""
    if not isinstance(reply, Mapping):
        return None, ["the reply is not a JSON object"]
    categories = {c.id for c in deps.cycle.cause_categories}
    cat = reply.get("cause_category")
    chain = reply.get("why_chain")
    origin = reply.get("origin_event_id")
    sources = reply.get("source_event_ids")
    text = reply.get("text")
    if cat not in categories:
        return None, [f"cause category {cat!r} is not on the list"]
    if not isinstance(chain, list) or not all(isinstance(s, str) and s.strip() for s in chain):
        return None, ["why_chain must be a list of statements"]
    if not 1 <= len(chain) <= min(5, deps.cycle.why_chain_max):
        return None, [f"why_chain has {len(chain)} statements, allowed 1 to {deps.cycle.why_chain_max}"]
    if not isinstance(origin, str) or not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        return None, ["origin_event_id and source_event_ids must be event ids"]
    if not isinstance(text, str) or not text.strip():
        return None, ["the lesson text is empty"]
    fields = {"cause_category": cat, "why_chain": [s.strip() for s in chain], "origin_event_id": origin,
              "source_event_ids": list(dict.fromkeys(sources)), "text": text.strip()}

    problems: list[str] = []
    run_id = defect["run_id"]
    if not sources:
        problems.append("no source events cited")
    for eid in fields["source_event_ids"]:
        ev = deps.store.get("events", eid)
        if ev is None:
            problems.append(f"event {eid} does not exist")
        elif ev.get("run_id") != run_id:
            problems.append(f"event {eid} belongs to run {ev.get('run_id')}, not {run_id}")
    if origin not in fields["source_event_ids"]:
        problems.append(f"origin event {origin} is not among the source events")
    evaluation = evaluation_of(deps, run_id)
    failed = {c["id"] for c in (evaluation or {}).get("checks", []) if c["result"] == "FAIL"}
    if defect["check_id"] not in failed:
        problems.append(f"{defect['check_id']} is not a FAIL in run {run_id}")
    return fields, problems


def _put_lesson(deps: Deps, lesson: Lesson) -> None:
    doc = lesson.to_doc()
    if deps.store.get("lessons", lesson.id) is None:
        deps.store.insert("lessons", doc)
    else:
        deps.store.update("lessons", lesson.id, {k: v for k, v in doc.items() if k != "_id"})


def analyze(deps: Deps, experiment_id: str, snapshot: str) -> dict[str, Any]:
    exp = load_experiment(deps, experiment_id)
    require_passed(exp, "measure")
    if snapshot != deps.cycle.lesson_snapshot_after_analyze:
        raise PhaseError(f"analyze writes lessons to snapshot {deps.cycle.lesson_snapshot_after_analyze}, not {snapshot}")

    results = []
    lessons: list[Lesson] = []
    for defect in development_defects(deps, experiment_id):
        case = case_info(defect["case_id"])
        events = compact_events(run_events(deps, defect["run_id"]))
        system, user = prompts.root_cause(deps.cycle, defect, case, events)
        reply = ask_agent(deps, system, user, prompts.root_cause_schema(deps.cycle))
        fields, problems = verify(deps, defect, reply["parsed"])
        entry: dict[str, Any] = {"defect": defect, "agent": reply["call"], "problems": problems}
        if fields is None:
            entry.update(status="unusable", lesson_id=None)
            results.append(entry)
            continue
        status = "provisional" if problems else "verified"
        lesson = Lesson(
            _id=lesson_id(experiment_id, defect["run_id"], defect["check_id"]),
            defect={"run_id": defect["run_id"], "case_id": defect["case_id"], "check_id": defect["check_id"]},
            failure_category=defect["category"],
            cause_category=fields["cause_category"],
            controllable=fields["cause_category"] in CONTROLLABLE_CAUSES,
            why_chain=fields["why_chain"],
            origin_event_id=fields["origin_event_id"],
            source_event_ids=fields["source_event_ids"],
            text=fields["text"],
            status=status,
            scope=deps.cycle.lesson_scope,
            snapshot=snapshot,
            created_at=deps.now(),
        )
        lessons.append(lesson)
        entry.update(status=status, lesson_id=lesson.id, cause_category=lesson.cause_category,
                     controllable=lesson.controllable)
        results.append(entry)

    verified = [les for les in lessons if les.status == "verified"]
    vectors = deps.embed_texts([les.text for les in verified]) if verified else []
    model_name = deps.embedding_model()
    for les in lessons:
        if les.status == "verified":
            vec = vectors[verified.index(les)]
            les = les.model_copy(update={"embedding": [float(x) for x in vec], "embedding_model": model_name})
        _put_lesson(deps, les)

    controllable = [les for les in verified if les.controllable]
    artifact = {
        "snapshot": snapshot,
        "defects": len(results),
        "root_causes": results,
        "verified": [les.id for les in verified],
        "verified_controllable": [les.id for les in controllable],
        "provisional": [les.id for les in lessons if les.status == "provisional"],
    }
    phase_def = next(p for p in deps.cycle.phases if p.id == "analyze")
    if controllable:
        reasons = [f"{len(controllable)} verified controllable root cause(s) of {len(results)} defect(s)"]
        record_phase(deps, experiment_id, "analyze", artifact, True, reasons)
        return {"phase": "analyze", "passed": True, "reasons": reasons, "artifact": artifact}
    reasons = [phase_def.stop_reason or "no controllable root cause",
               f"{len(verified)} verified root cause(s), none controllable, of {len(results)} defect(s)"]
    record_phase(deps, experiment_id, "analyze", artifact, False, reasons, decision="stopped")
    return {"phase": "analyze", "passed": False, "reasons": reasons, "artifact": artifact}

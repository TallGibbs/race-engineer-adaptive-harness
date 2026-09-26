"""Define: the project charter from the baseline arm's development-case evaluations, a
problem statement from the improvement agent, and tollgate D."""

from __future__ import annotations

from typing import Any

from . import prompts
from .common import (
    case_sets,
    dpo_table,
    ensure_experiment,
    first_sentences,
    record_phase,
    runs_of,
    score_missing,
    valid_scored,
    ask_agent,
)
from ..store.records import update_experiment
from .ports import Deps

OUT_OF_SCOPE = ["model", "tools", "charters", "data", "evaluator", "specification"]
MAX_SENTENCES = 3


def build_charter(deps: Deps, experiment_id: str) -> dict[str, Any]:
    """Code-built charter. Only development-case evaluations enter it (evidence rule)."""
    exp = ensure_experiment(deps, experiment_id)
    sets = case_sets(exp)
    allowed = set(deps.cycle.evidence_rule["define"])
    runs = [r for r in runs_of(deps, experiment_id, "baseline") if sets.get(r["case_id"]) in allowed]
    score_missing(deps, runs)
    scored = valid_scored(deps, runs)
    table = dpo_table([ev for _, ev in scored])
    ctqs = []
    for ctq in deps.cycle.ctqs:
        row = table["by_check"][ctq.id]
        if row["defects"]:
            ctqs.append({"id": ctq.id, "name": ctq.name, "statement": ctq.statement,
                         "failure_category": ctq.failure_category, **row})
    return {
        "evidence": sorted(allowed),
        "cases": sorted({r["case_id"] for r, _ in scored}),
        "baseline": {
            "arm": "baseline",
            "runs": table["runs"],
            "defects": table["defects"],
            "opportunities": table["opportunities"],
            "dpo": table["dpo"],
            "failure_categories": table["failure_categories"],
        },
        "ctqs": ctqs,
        "scope": [
            {"cause": c.id, "surface": c.surface, "description": c.description}
            for c in deps.cycle.cause_categories
            if c.controllable
        ],
        "out_of_scope": OUT_OF_SCOPE,
        "goal": [r.statement for r in deps.acceptance.rules],
    }


def define(deps: Deps, experiment_id: str) -> dict[str, Any]:
    charter = build_charter(deps, experiment_id)
    phase_def = next(p for p in deps.cycle.phases if p.id == "define")
    if charter["baseline"]["defects"] == 0:
        reason = phase_def.stop_reason or "no project"
        artifact = {"charter": charter, "problem_statement": None}
        record_phase(deps, experiment_id, "define", artifact, False, [reason], decision="no_project")
        return {"phase": "define", "passed": False, "reasons": [reason], "artifact": artifact}

    system, user = prompts.problem_statement(deps.cycle, charter)
    reply = ask_agent(deps, system, user, prompts.PROBLEM_SCHEMA)
    raw = (reply["parsed"] or {}).get("problem_statement") or reply["text"]
    statement = first_sentences(str(raw), MAX_SENTENCES)
    artifact = {"charter": charter, "problem_statement": statement, "agent": reply["call"]}
    n = charter["baseline"]["defects"]
    reasons = [f"{n} development defect(s) in the baseline arm"]
    if not statement:
        reasons = ["the improvement agent returned no problem statement"]
        record_phase(deps, experiment_id, "define", artifact, False, reasons, decision="stopped")
        return {"phase": "define", "passed": False, "reasons": reasons, "artifact": artifact}
    record_phase(deps, experiment_id, "define", artifact, True, reasons)
    # A passing define starts the cycle afresh.
    update_experiment(deps.store, experiment_id, {"decision": None, "acceptance": None, "candidate_version": None})
    return {"phase": "define", "passed": True, "reasons": reasons, "artifact": artifact}

"""The improvement agent's fixed prompts (problem statement, root cause, proposal).

They describe the task, the evidence, the fixed cause categories, and the bounds. They
carry no hint about which cause to find or which change to make, and they are only ever
filled with development-case evidence: no answer-key values, and nothing about held-out
or control cases.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..contracts.config import CHECK_BOUNDS
from ..contracts.rules import CycleConfig

TASK_DESCRIPTION = (
    "The harness under study answers questions about race interruptions in motor racing. "
    "For a venue brief it lists the past races at a venue, marks for each whether a safety "
    "car (SC), a virtual safety car (VSC), or a red flag occurred, and reports counts, rates "
    "with intervals, and a GO or HOLD disposition. For a race audit it reports whether one "
    "given race had an SC, a VSC, or a red flag. Three role agents (race engineer, "
    "statistician, data engineer) work through a fixed sequence of stages per case, calling "
    "data tools and recording every message, tool call, and tool result as an event. A fixed "
    "evaluator scores each case run on eight checks (E1 to E8); each failed check is a defect."
)

ROLE_DESCRIPTION = (
    "You are the improvement agent of a DMAIC (define, measure, analyze, improve, control) "
    "cycle that improves this harness's process. Code decides every tollgate; your answers "
    "are checked by code against the recorded evidence. Base every statement on the evidence "
    "given. Do not guess at facts the evidence does not show. Reply with one JSON object and "
    "nothing else."
)


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True, default=str)


def ctq_lines(cycle: CycleConfig) -> str:
    return "\n".join(f"- {c.id} {c.name} ({c.failure_category}): {c.statement}" for c in cycle.ctqs)


def cause_lines(cycle: CycleConfig) -> str:
    lines = []
    for c in cycle.cause_categories:
        where = f"editable surface {c.surface}" if c.controllable else "fixed, not controllable"
        lines.append(f"- {c.id}: {c.description} ({where})")
    return "\n".join(lines)


# ---------------------------------------------------------------- define


PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"problem_statement": {"type": "string"}},
    "required": ["problem_statement"],
    "additionalProperties": False,
}


def problem_statement(cycle: CycleConfig, charter: Mapping[str, Any]) -> tuple[str, str]:
    system = f"{ROLE_DESCRIPTION}\n\n{TASK_DESCRIPTION}"
    user = (
        "Phase: define.\n\n"
        "The checks (critical-to-quality characteristics):\n"
        f"{ctq_lines(cycle)}\n\n"
        "Baseline results on the development cases (defect counts and failure categories only):\n"
        f"{_json(charter['baseline'])}\n\n"
        "Checks with defects:\n"
        f"{_json(charter['ctqs'])}\n\n"
        "Write a problem statement of at most three sentences: what is failing, how often, "
        "measured against which checks. State only what these numbers show; do not name "
        "causes or remedies.\n\n"
        'Reply as {"problem_statement": "..."}.'
    )
    return system, user


# ---------------------------------------------------------------- analyze


def root_cause_schema(cycle: CycleConfig) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "cause_category": {"type": "string", "enum": [c.id for c in cycle.cause_categories]},
            "why_chain": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": cycle.why_chain_max},
            "origin_event_id": {"type": "string"},
            "source_event_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "text": {"type": "string"},
        },
        "required": ["cause_category", "why_chain", "origin_event_id", "source_event_ids", "text"],
        "additionalProperties": False,
    }


def root_cause(
    cycle: CycleConfig,
    defect: Mapping[str, Any],
    case: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    system = f"{ROLE_DESCRIPTION}\n\n{TASK_DESCRIPTION}"
    ctq = next(c for c in cycle.ctqs if c.id == defect["check_id"])
    user = (
        "Phase: analyze.\n\n"
        f"Defect: run {defect['run_id']}, case {case['id']} ({case['type']}), check {ctq.id} "
        f"{ctq.name} failed with failure category {defect['category']}.\n"
        f"Check {ctq.id}: {ctq.statement}\n\n"
        f"The question the run answered:\n{case['question']}\n\n"
        "Cause categories (choose exactly one):\n"
        f"{cause_lines(cycle)}\n\n"
        f"The run's recorded events, in order ({len(events)} events; content shortened):\n"
        f"{_json(list(events))}\n\n"
        "Find the root cause of this defect in these events. Return:\n"
        "- cause_category: one of the categories above;\n"
        f"- why_chain: 1 to {cycle.why_chain_max} statements, each answering 'why' for the one before, "
        "from the defect to its root cause;\n"
        "- origin_event_id: the id of the event where the cause first shows;\n"
        "- source_event_ids: the ids of the events that support the chain, including the origin event;\n"
        "- text: the lesson, one or two sentences a future run could act on, stated as a general "
        "process norm for this task.\n"
        "Cite only event ids from the list above."
    )
    return system, user


# ---------------------------------------------------------------- improve


# The /checks lines come from the contract's catalog of editable checks.
_CHECK_LINES = "\n".join(
    f"- {path} ({'surface of measurement causes' if i == 0 else 'measurement'}): {bounds}."
    for i, (path, bounds) in enumerate(CHECK_BOUNDS.items())
)

BOUNDS = f"""Editable paths (RFC 6902 JSON Patch operations on the configuration) and their bounds:
- /stages (surface of method causes): reorder, or insert the optional stages X1 to X3 at their
  allowed positions (see optional_stage_catalog: "before" and "after"). S1 to S7 may not be
  removed; S4 stays before S5; S6 then S7 stay last.
- /context_policy/... (surface of material causes): context items from the catalog only (task,
  output_schema, tool_docs, lessons, open_objections, prior:<stage id>); lessons.k 0 to 5;
  lessons.filters limited to status and scope.
{_CHECK_LINES}
Everything else is fixed: model, budgets, both stage catalogs, change_cap, /checks/schema,
tools, role charters, the evaluator, the cases, and the answer key."""


def proposal_schema(change_cap: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": change_cap,
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "object",
                            "properties": {
                                "op": {"type": "string", "enum": ["add", "remove", "replace", "move", "copy"]},
                                "path": {"type": "string"},
                                "value": {},
                                "from": {"type": "string"},
                            },
                            "required": ["op", "path"],
                        },
                        "lesson_id": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["op", "lesson_id", "why"],
                },
            },
            "rationale": {"type": "string"},
            "expected_effect": {"type": "string"},
            "risks": {"type": "string"},
        },
        "required": ["changes", "rationale", "expected_effect", "risks"],
        "additionalProperties": False,
    }


def proposal(
    cycle: CycleConfig,
    acceptance_statements: Sequence[str],
    config: Mapping[str, Any],
    charter: Mapping[str, Any],
    root_causes: Sequence[Mapping[str, Any]],
    change_cap: int,
    history: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    system = f"{ROLE_DESCRIPTION}\n\n{TASK_DESCRIPTION}"
    rules = "\n".join(f"- {s}" for s in acceptance_statements)
    earlier = ""
    if history:
        earlier = (
            "Earlier proposals for this task, oldest first: each version's changes (JSON Patch "
            "operation and the lesson_id it cited), its status, and its outcome on the "
            "development cases only; and each proposal refused by the validator in an earlier "
            "experiment, with its changes and the validator's reasons:\n"
            f"{_json(list(history))}\n\n"
            "A proposal whose set of changes (in any order) equals that of an earlier rejected "
            "or rolled_back version, or of a proposal refused by the validator, will be refused.\n\n"
        )
    user = (
        "Phase: improve.\n\n"
        "Current configuration:\n"
        f"{_json(config)}\n\n"
        f"{BOUNDS}\n\n"
        "Cause categories and the surface each may change:\n"
        f"{cause_lines(cycle)}\n\n"
        "Project charter (development cases only):\n"
        f"{_json(charter)}\n\n"
        "Verified controllable root causes, with excerpts of their source events:\n"
        f"{_json(list(root_causes))}\n\n"
        "The candidate is accepted only if all of these hold when it is piloted:\n"
        f"{rules}\n\n"
        f"{earlier}"
        f"Propose at most {change_cap} changes. Each change is one JSON Patch operation on an "
        "editable path, cites the lesson_id of the root cause it addresses, and that root "
        "cause's category must be the surface of the path. Return:\n"
        '- changes: [{"op": {"op": ..., "path": ..., "value": ...}, "lesson_id": ..., "why": ...}];\n'
        "- rationale: why these changes address the root causes;\n"
        "- expected_effect: which checks should improve;\n"
        "- risks: what could get worse, and which acceptance rule would catch it."
    )
    return system, user

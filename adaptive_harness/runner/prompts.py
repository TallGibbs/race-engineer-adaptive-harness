"""Stable prompt text: role charters, the reply protocol, and stage purposes.

Nothing here depends on the case or on time, so the system prompt stays byte-identical
across calls of the same role and stage and provider prompt caching can work. Charters
state general professional norms only.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..contracts.outputs import OUTPUT_MODELS
from ..contracts.protocol import StageNote

CHARTERS_DIR = Path(__file__).resolve().parents[2] / "examples" / "neutralization_brief" / "charters"

TEAM = (
    "You are one of three role agents (race engineer, statistician, data engineer) "
    "holding one bounded discussion to answer a single case. A coordinator runs the "
    "stages, executes tools, and records every message, tool call, and result as an "
    "event with an id. Refer to evidence by those event ids."
)

STAGE_PURPOSE: dict[str, str] = {
    "frame": "Frame the case: restate the question, the scope, what counts, and what a complete answer needs.",
    "assess": "Assess the framing independently from your own discipline: the plan, the definitions, "
    "the risks, and what evidence will be needed.",
    "challenge": "Respond once to the other two roles: agree, disagree, or add, with reasons. "
    "Raise objections that must be answered before the brief.",
    "gather": "Gather the evidence with the tools. Record in your note what you fetched, with event ids, "
    "and what you could not fetch.",
    "estimate": "Compute the counts and estimates from the gathered evidence, using the tools for arithmetic, "
    "and cite the event ids behind each number.",
    "brief": "Write the final answer for the case in the output schema. Cite recorded tool_result event ids "
    "as evidence and carry forward any objection that is still open.",
    "identity_check": "Confirm from the data which event the case targets before any other work starts.",
    "definitions": "Write down the definitions, units, and inclusion rules the counting will use.",
    "reconcile_sources": "Compare what the different sources say about each item and report agreements "
    "and disagreements, with event ids.",
}

FINISH_ONLY = (
    "Reply with exactly one JSON object and nothing else:\n"
    '{"action": "finish", "output": <your output, matching the output schema>}'
)

TOOL_PROTOCOL = (
    "Reply with exactly one JSON object and nothing else, either\n"
    '{"action": "call_tool", "tool": "<tool name>", "args": {...}, "why": "<one line>"}\n'
    "to call one tool (the coordinator runs it and returns the result with its event id), or\n"
    '{"action": "finish", "output": <your output, matching the output schema>}\n'
    "when you are done."
)

REPAIR = "Reply with one valid JSON object only."

CHECK_REPAIR = (
    "The harness's validation checks failed on your output, for these reasons:\n{reasons}\n"
    "Revise the output to address them and reply with the complete revised output as one "
    "finish action. This is the only revision round."
)


@lru_cache(maxsize=None)
def charter(role: str) -> str:
    return (CHARTERS_DIR / f"{role}.md").read_text(encoding="utf-8").strip()


def output_schema(stage_id: str, case_type: str) -> dict[str, Any]:
    """The schema of this stage's `output`: StageNote before S6, the section D output at S6."""
    if stage_id == "S6":
        return OUTPUT_MODELS[case_type].model_json_schema(by_alias=True)
    return StageNote.model_json_schema()


def tool_docs(tools: dict[str, Any], names: list[str]) -> str:
    parts = []
    for name in names:
        t = tools.get(name)
        if t is None:
            continue
        parts.append(f"- {t.name}: {t.description}\n  args schema: {dumps(t.input_schema)}")
    return "Tools you may call:\n" + "\n".join(parts)


def dumps(obj: Any) -> str:
    """Deterministic JSON text for prompts (sorted keys, so the prefix stays stable)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)

"""Export JSON Schemas for every contract model: python -m adaptive_harness.contracts.export"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from . import config, outputs, protocol, records, rules
from .paths import SCHEMAS_DIR

MODELS: dict[str, type[BaseModel]] = {
    # configuration and rules
    "harness_config": config.HarnessConfig,
    "resolved_model": config.ResolvedModel,
    "acceptance_rules": rules.AcceptanceRules,
    "cycle_config": rules.CycleConfig,
    # outputs
    "venue_brief": outputs.VenueBrief,
    "race_audit": outputs.RaceAudit,
    # run-time messages
    "task": protocol.Task,
    "tool_result": protocol.ToolResult,
    "chat_message": protocol.ChatMessage,
    "model_response": protocol.ModelResponse,
    "stage_note": protocol.StageNote,
    # MongoDB documents
    "run": records.Run,
    "event": records.Event,
    "harness_version": records.HarnessVersion,
    "lesson": records.Lesson,
    "evaluation": records.Evaluation,
    "experiment": records.Experiment,
}


def all_schemas() -> dict[str, dict]:
    out = {name: m.model_json_schema(by_alias=True) for name, m in MODELS.items()}
    out["stage_reply"] = TypeAdapter(protocol.StageReply).json_schema(by_alias=True)
    return out


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(target: Path = SCHEMAS_DIR) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in all_schemas().items():
        p = target / f"{name}.schema.json"
        p.write_text(render(schema), encoding="utf-8", newline="\n")
        written.append(p)
    return written


if __name__ == "__main__":
    for p in export():
        print(p.name)

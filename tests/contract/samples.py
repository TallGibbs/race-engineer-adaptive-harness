"""Synthetic instances of every contract model. Values are invented, never from the key."""

from __future__ import annotations

from datetime import date, datetime, timezone

from adaptive_harness.contracts import config, outputs, protocol, records, rules
from adaptive_harness.contracts.paths import load_acceptance, load_config, load_cycle

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
RATE = {"value": 0.5, "interval": [0.2, 0.8], "method": "wilson_90"}
ASSIGN = {"race_engineer": "m", "statistician": "m", "data_engineer": "m", "improvement_agent": "m"}

VENUE_BRIEF = {
    "case_id": "T1",
    "venue": {"location": "Testville", "from_target": {"year": 2030, "round": 1}, "evidence": ["r1:00003"]},
    "races": [
        {"year": 2029, "round": 1, "location": "Testville", "event_name": "Test Grand Prix",
         "sc": True, "vsc": False, "red_flag": False, "evidence": ["r1:00004"]},
        {"year": 2030, "round": 1, "location": "Testville", "event_name": "Test Grand Prix",
         "sc": False, "vsc": False, "red_flag": False, "evidence": ["r1:00005"]},
    ],
    "counts": {"n_races": 2, "n_sc": 1, "n_vsc": 0, "n_any": 1},
    "rates": {"sc": RATE, "vsc": RATE, "any": RATE},
    "disposition": "GO",
    "thin_sample": True,
    "hold_reason": None,
    "open_objections": [],
}
HOLD_BRIEF = {
    **VENUE_BRIEF,
    "races": [],
    "counts": {"n_races": 0, "n_sc": 0, "n_vsc": 0, "n_any": 0},
    "rates": {"sc": None, "vsc": None, "any": None},
    "disposition": "HOLD",
    "hold_reason": "no race qualifies",
}
RACE_AUDIT = {
    "case_id": "T2",
    "race": {"year": 2030, "round": 2, "event_name": "Sample Grand Prix", "location": "Sampletown"},
    "sc": False, "vsc": True, "red_flag": False,
    "evidence": ["r2:00004"],
    "open_objections": ["one source was sparse"],
}
RESOLVED = {"provider": "fake", "base_url": None, "assignment": ASSIGN}


def samples() -> list[tuple[type, dict]]:
    return [
        (config.HarnessConfig, load_config().to_dict()),
        (config.ResolvedModel, RESOLVED),
        (rules.AcceptanceRules, load_acceptance().model_dump(by_alias=True, mode="json")),
        (rules.CycleConfig, load_cycle().model_dump(by_alias=True, mode="json")),
        (outputs.VenueBrief, VENUE_BRIEF),
        (outputs.VenueBrief, HOLD_BRIEF),
        (outputs.RaceAudit, RACE_AUDIT),
        (protocol.Task, {"id": "T1", "set": "development", "type": "venue_brief",
                         "target": {"year": 2030, "round": 1}, "as_of": date(2031, 1, 1), "question": "q?"}),
        (protocol.ToolResult, protocol.ToolResult.build("ok", {"a": 1}).model_dump()),
        (protocol.ChatMessage, {"role": "user", "content": "hi"}),
        (protocol.ModelResponse, {"text": "{}", "parsed": {}, "usage": {"input_tokens": 3, "output_tokens": 1,
                                  "cache_read_tokens": 0}, "stop_reason": "end_turn", "latency_ms": 5, "model": "m"}),
        (protocol.CallTool, {"action": "call_tool", "tool": "list_events", "args": {"year": 2030}, "why": "need schedule"}),
        (protocol.Finish, {"action": "finish", "output": {"summary": "done"}}),
        (protocol.StageNote, {"summary": "framed", "objections": ["x"], "data": {"k": 1}}),
        (records.Run, {"_id": "r1", "harness_version": "v1", "config_hash": "h", "memory_snapshot": "M0",
                       "case_id": "T1", "arm": "baseline", "experiment_id": "x1",
                       "model": {"provider": "fake", "assignment": ASSIGN}, "fastf1_version": "3.8.3",
                       "started_at": NOW, "ended_at": NOW, "status": "completed",
                       "totals": {"model_calls": 1, "tool_calls": 2, "input_tokens": 3, "output_tokens": 4,
                                  "cache_read_tokens": 0, "wall_seconds": 1.5}, "output": VENUE_BRIEF}),
        (records.Event, {"_id": "r1:00001", "run_id": "r1", "seq": 1, "stage_id": "S1", "role": "race_engineer",
                         "type": "model_call", "content": {"text": "{}"}, "refs": [],
                         "context_manifest": [{"item": "task", "id": None, "sha256": "a", "approx_tokens": 10}],
                         "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0}, "model": "m",
                         "stop_reason": "end_turn", "content_sha256": "b", "created_at": NOW}),
        (records.HarnessVersion, {"_id": "v2", "parent_id": "v1", "config": load_config().to_dict(), "config_hash": "h",
                                  "changes": [{"op": {"op": "replace", "path": "/checks/venue_match/enabled", "value": True},
                                               "lesson_id": "L1", "why": "w"}],
                                  "rationale": "r", "expected_effect": "e", "risks": "R1 would catch x",
                                  "validation": {"valid": True, "reasons": []}, "status": "accepted", "pinned": True,
                                  "control_plan": {"thresholds": {"T1": 7}, "model": RESOLVED, "fastf1_version": "3.8.3",
                                                   "reaction_plan": "rerun once; roll back on repeat", "status": "armed",
                                                   "confirmation_run_ids": ["r9"],
                                                   "signals": [{"run_id": "r9", "case_id": "T1", "passed": 6,
                                                                "threshold": 7, "repeated": False}]},
                                  "decision_reasons": ["all rules hold"], "created_at": NOW}),
        (records.Lesson, {"_id": "L1", "defect": {"run_id": "r1", "case_id": "T1", "check_id": "E3"},
                          "failure_category": "coverage_mismatch", "cause_category": "method", "controllable": True,
                          "why_chain": ["a", "b"], "origin_event_id": "r1:00004", "source_event_ids": ["r1:00004"],
                          "text": "lesson", "status": "verified", "scope": "neutralization_brief", "snapshot": "M1",
                          "embedding": [0.1, 0.2], "embedding_model": "e", "created_at": NOW}),
        (records.Evaluation, {"_id": "e1", "run_id": "r1", "case_id": "T1", "arm": "baseline", "experiment_id": "x1",
                              "checks": [{"id": "E1", "result": "PASS", "category": None},
                                         {"id": "E2", "result": "FAIL", "category": "venue_mismatch"},
                                         {"id": "E3", "result": "NA", "category": None}],
                              "opportunities": 2, "defects": 1, "evaluator_sha256": "a", "key_sha256": "b",
                              "created_at": NOW}),
        (records.Experiment, {"_id": "x1", "cases": [{"id": "T1", "set": "development"}], "arms": ["baseline"],
                              "dmaic": {"define": {"status": "passed", "artifact": {"dpo": 0.1},
                                                   "tollgate": {"passed": True, "reasons": []}, "completed_at": NOW}},
                              "acceptance": {r: {"holds": True, "detail": "ok"} for r in ("R0", "R1", "R2", "R3", "R4")},
                              "decision": "accepted", "candidate_version": "v2", "report_path": "reports/x1.md",
                              "created_at": NOW}),
    ]

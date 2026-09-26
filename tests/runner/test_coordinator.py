from __future__ import annotations

from adaptive_harness.contracts import Event, FetchError, Run, canonical_sha256
from adaptive_harness.contracts.errors import DataUnavailable
from adaptive_harness.contracts.protocol import ModelResponse
from adaptive_harness.runner.context import approx_tokens
from adaptive_harness.testing import FakeStore, FakeTool

from .helpers import (
    AUDIT,
    BRIEF,
    call,
    finish,
    config,
    fake_tools,
    note,
    run_case,
    standard_scripts,
)


def stage_sequence(events):
    seen = []
    for e in events:
        if e["stage_id"] and e["stage_id"] not in seen:
            seen.append(e["stage_id"])
    return seen


def test_scripted_run_completes_and_records_everything():
    run, events, model, store = run_case()
    Run.model_validate(run)
    for e in events:
        Event.model_validate(e)
        assert e["content_sha256"] == canonical_sha256(e["content"])
    assert run["status"] == "completed"
    assert run["output"] == BRIEF
    assert run["fastf1_version"] == "0.0-test"
    assert run["model"]["assignment"]["race_engineer"] == "re-model"
    assert run["totals"]["model_calls"] == 11 and run["totals"]["tool_calls"] == 2
    assert run["totals"]["input_tokens"] > 0
    types = {e["type"] for e in events}
    assert {"model_call", "tool_call", "tool_result", "message", "check", "decision"} <= types
    # refs chain: tool_result -> tool_call -> model_call
    by_id = {e["_id"]: e for e in events}
    for tr in (e for e in events if e["type"] == "tool_result"):
        tc = by_id[tr["refs"][0]]
        assert tc["type"] == "tool_call" and by_id[tc["refs"][0]]["type"] == "model_call"
    decision = [e for e in events if e["type"] == "decision"][-1]
    assert decision["content"]["status"] == "completed"
    # the S6 author saw the tool-result ids of S4 in its context
    s6_call = next(e for e in events if e["type"] == "model_call" and e["stage_id"] == "S6")
    s4_result = next(e for e in events if e["type"] == "tool_result" and e["stage_id"] == "S4")
    assert s4_result["_id"] in s6_call["content"]["input"][0]["content"]


def test_stage_order_follows_config():
    run, events, *_ = run_case()
    seq = stage_sequence(events)
    assert seq[0] == "S1" and set(seq[1:3]) == {"S2a", "S2b"} and seq[3:] == ["S3", "S4", "S5", "S6", "S7"]

    cfg = config(stages=["X1", "S1", "S2a", "S2b", "X2", "S3", "S4", "X3", "S5", "S6", "S7"])
    scripts = standard_scripts()
    scripts["data_engineer"] = [note("identity ok")] + scripts["data_engineer"][:4] + [note("reconciled")]
    scripts["statistician"] = [scripts["statistician"][0], note("definitions")] + scripts["statistician"][1:]
    run, events, *_ = run_case(scripts, cfg=cfg)
    seq = stage_sequence(events)
    assert seq[0:2] == ["X1", "S1"] and set(seq[2:4]) == {"S2a", "S2b"}
    assert seq[4:] == ["X2", "S3", "S4", "X3", "S5", "S6", "S7"]
    assert run["status"] == "completed"


def test_optional_stages_do_not_run_unless_configured():
    _, events, *_ = run_case()
    assert not any(e["stage_id"] in ("X1", "X2", "X3") for e in events)


def test_s2_is_blind_and_s3_sees_both():
    _, events, model, _ = run_case()
    calls = {e["stage_id"] + ":" + e["role"]: e for e in events if e["type"] == "model_call"}
    s2a, s2b = calls["S2a:statistician"], calls["S2b:data_engineer"]
    assert "S2B-MARKER" not in str(s2a["content"]) and "S2A-MARKER" not in str(s2b["content"])
    assert not any(m["item"] == "prior:S2b" for m in s2a["context_manifest"])
    assert not any(m["item"] == "prior:S2a" for m in s2b["context_manifest"])
    for role in ("race_engineer", "statistician", "data_engineer"):
        s3 = calls[f"S3:{role}"]
        assert "S2A-MARKER" in str(s3["content"]) and "S2B-MARKER" in str(s3["content"])
    # each role responds exactly once at S3
    assert sum(1 for e in events if e["type"] == "message" and e["stage_id"] == "S3") == 3


def test_manifest_recorded_on_every_model_call():
    _, events, *_ = run_case()
    model_calls = [e for e in events if e["type"] == "model_call"]
    assert model_calls
    for e in model_calls:
        items = [m["item"] for m in e["context_manifest"]]
        assert items[0] == "charter" and "task" in items and "output_schema" in items
        assert all(len(m["sha256"]) == 64 and m["approx_tokens"] > 0 for m in e["context_manifest"])
        assert e["model"] and e["usage"] is not None and e["stop_reason"] == "end_turn"
    s4 = next(e for e in model_calls if e["stage_id"] == "S4")
    assert "tool_docs" in [m["item"] for m in s4["context_manifest"]]
    s1 = next(e for e in model_calls if e["stage_id"] == "S1")
    task_item = next(m for m in s1["context_manifest"] if m["item"] == "task")
    assert task_item["id"] == "T1" and task_item["sha256"] == __import__("hashlib").sha256(
        s1["content"]["input"][0]["content"].split("\n\n")[0].encode()).hexdigest()
    assert task_item["approx_tokens"] == approx_tokens(s1["content"]["input"][0]["content"].split("\n\n")[0])


def test_system_prompt_is_stable_and_volatile_content_last():
    _, events, model, _ = run_case()
    calls = model.calls("race_engineer")
    s1, s3 = calls[0], calls[1]
    assert "Race engineer" in s1["system"] and "Output schema" in s1["system"]
    assert "Case:" not in s1["system"] and s1["messages"][0]["content"].startswith("Case:")
    assert s1["system"].split("Stage S1")[0] == s3["system"].split("Stage S3")[0]


def test_tool_loop_respects_tool_budget():
    cfg = config(budgets__max_tool_calls=3)
    scripts = standard_scripts()
    scripts["data_engineer"] = scripts["data_engineer"][:2] + [call("list_events", year=2030)] * 10
    run, events, *_ = run_case(scripts, cfg=cfg)
    assert run["status"] == "budget_exceeded"
    assert run["totals"]["tool_calls"] == 3
    assert sum(1 for e in events if e["type"] == "tool_call") == 3
    assert events[-1]["type"] == "error" and "max_tool_calls" in events[-1]["content"]["message"]


def test_tool_loop_respects_model_call_budget():
    cfg = config(budgets__max_model_calls=8)
    scripts = standard_scripts()
    scripts["data_engineer"] = scripts["data_engineer"][:2] + [call("list_events", year=2030)] * 10
    run, events, *_ = run_case(scripts, cfg=cfg)
    assert run["status"] == "budget_exceeded"
    assert run["totals"]["model_calls"] == 8
    assert sum(1 for e in events if e["type"] == "model_call") == 8


def test_wall_budget(monkeypatch):
    cfg = config(budgets__max_wall_seconds=1)
    from adaptive_harness.runner import recorder

    monkeypatch.setattr(recorder.Recorder, "elapsed", lambda self: 5.0)
    run, events, *_ = run_case(cfg=cfg)
    assert run["status"] == "budget_exceeded" and run["totals"]["model_calls"] == 0
    assert "max_wall_seconds" in events[-1]["content"]["message"]


def test_fetch_error_gives_harness_error():
    tools = fake_tools(list_events=FakeTool("list_events", default=FetchError("network down")))
    run, events, *_ = run_case(tools=tools)
    assert run["status"] == "harness_error"
    errors = [e for e in events if e["type"] == "error"]
    assert any(e["content"]["error"] == "FetchError" for e in errors)
    assert not any(e["stage_id"] in ("S5", "S6", "S7") for e in events)


def test_model_unavailable_gives_harness_error():
    scripts = standard_scripts()
    scripts["race_engineer"] = scripts["race_engineer"][:2]  # the S6 call exhausts the script
    run, *_ = run_case(scripts)
    assert run["status"] == "harness_error"


def test_data_unavailable_is_reported_to_the_role():
    tools = fake_tools(list_events=FakeTool("list_events", default=DataUnavailable("no such season")))
    run, events, model, _ = run_case(tools=tools)
    assert run["status"] == "completed"
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["content"]["error"] == "DataUnavailable"
    assert "data unavailable" in model.calls("data_engineer")[-1]["messages"][-1]["content"]


def test_unknown_tool_is_refused_without_spending_tool_budget():
    scripts = standard_scripts()
    scripts["data_engineer"][2:2] = [call("interval", k=1, n=1)]  # interval is not an S4 tool
    run, events, model, _ = run_case(scripts)
    assert run["status"] == "completed" and run["totals"]["tool_calls"] == 2
    assert any(e["type"] == "error" and e["content"]["error"] == "tool_not_available" for e in events)


def test_invalid_json_gets_one_repair():
    scripts = standard_scripts()
    scripts["race_engineer"].insert(0, "this is not json")
    run, events, model, _ = run_case(scripts)
    assert run["status"] == "completed"
    re_calls = model.calls("race_engineer")
    assert "Reply with one valid JSON object only." in re_calls[1]["messages"][-1]["content"]
    s1_calls = [e for e in events if e["type"] == "model_call" and e["stage_id"] == "S1"]
    assert [c["content"]["attempt"] for c in s1_calls] == ["initial", "repair"]
    assert any(e["type"] == "error" and e["content"]["error"] == "invalid_reply" for e in events)


def test_second_invalid_reply_records_a_stage_error_and_the_run_continues():
    scripts = standard_scripts()
    scripts["race_engineer"][0:1] = ["nope", '{"action": "finish"}']
    run, events, *_ = run_case(scripts)
    s1_calls = [e for e in events if e["type"] == "model_call" and e["stage_id"] == "S1"]
    assert len(s1_calls) == 2
    s1_errors = [e for e in events if e["type"] == "error" and e["stage_id"] == "S1"]
    assert len(s1_errors) == 2
    assert not any(e["type"] == "message" and e["stage_id"] == "S1" for e in events)
    assert run["status"] == "completed"


def test_refusal_is_recorded_as_error_event():
    refusal = ModelResponse(text="", parsed=None, stop_reason="refusal", model="re-model")
    scripts = standard_scripts()
    scripts["race_engineer"][0] = refusal
    run, events, *_ = run_case(scripts)
    err = next(e for e in events if e["type"] == "error" and e["stage_id"] == "S1")
    assert err["content"]["stop_reason"] == "refusal"
    s1_calls = [e for e in events if e["type"] == "model_call" and e["stage_id"] == "S1"]
    assert len(s1_calls) == 1  # no repair for a refusal


def test_failed_validation_records_hold_with_reasons():
    bad = dict(BRIEF)
    bad.pop("counts")
    run, events, *_ = run_case(standard_scripts(bad))
    assert run["status"] == "hold" and run["output"] == bad
    check = next(e for e in events if e["type"] == "check")
    assert check["content"]["check"] == "schema" and not check["content"]["passed"]
    decision = next(e for e in events if e["type"] == "decision")
    assert decision["content"]["disposition"] == "HOLD" and any("counts" in r for r in decision["content"]["reasons"])


def test_hold_disposition_gives_hold_status():
    hold = dict(BRIEF, races=[], counts={"n_races": 0, "n_sc": 0, "n_vsc": 0, "n_any": 0},
                rates={"sc": None, "vsc": None, "any": None}, disposition="HOLD", hold_reason="none")
    run, *_ = run_case(standard_scripts(hold))
    assert run["status"] == "hold"


def test_enabled_checks_run_at_s7():
    cfg = config(checks__venue_match={"enabled": True},
                 checks__min_races_for_rate={"enabled": True, "value": 5})
    scripts = standard_scripts()
    scripts["race_engineer"].append(finish(BRIEF))  # the repair round returns the brief unchanged
    run, events, *_ = run_case(scripts, cfg=cfg)
    checks = {e["content"]["check"]: e["content"] for e in events if e["type"] == "check"}
    assert set(checks) == {"schema", "venue_match", "min_races_for_rate"}
    assert checks["venue_match"]["passed"]
    assert not checks["min_races_for_rate"]["passed"]
    assert run["status"] == "hold"


def test_race_audit_run():
    audit = {"case_id": "T2", "race": {"year": 2030, "round": 2, "event_name": "E", "location": "L"},
             "sc": False, "vsc": True, "red_flag": False, "evidence": ["e"], "open_objections": []}
    run, *_ = run_case(standard_scripts(audit), task=AUDIT)
    assert run["status"] == "completed"


def test_lessons_limited_to_snapshot():
    store = FakeStore()
    for snap in ("M0", "M1"):
        store.insert("lessons", {"_id": f"L-{snap}", "text": f"lesson text {snap}", "status": "verified",
                                 "scope": "neutralization_brief", "snapshot": snap if snap == "M1" else "M1x",
                                 "embedding": [1.0, 0.0], "embedding_model": "fake-embed"})
    seen = []

    def embedder(text, model_name):
        seen.append(model_name)
        return [1.0, 0.0]

    _, events, *_ = run_case(store=store, snapshot="M1", embedder=embedder)
    s1 = next(e for e in events if e["type"] == "model_call" and e["stage_id"] == "S1")
    lessons = next(m for m in s1["context_manifest"] if m["item"] == "lessons")
    assert lessons["id"] == "L-M1" and "lesson text M1" in s1["content"]["input"][0]["content"]
    assert seen == ["fake-embed"]  # embedded once per run

    _, events, *_ = run_case(store=store, snapshot="M0", embedder=embedder)
    s1 = next(e for e in events if e["type"] == "model_call" and e["stage_id"] == "S1")
    assert not any(m["item"] == "lessons" for m in s1["context_manifest"])
    assert seen == ["fake-embed"]  # nothing to retrieve on M0, so nothing embedded


def test_each_role_uses_its_assigned_model():
    _, events, *_ = run_case()
    for e in (e for e in events if e["type"] == "model_call"):
        assert e["model"] == {"race_engineer": "re-model", "statistician": "stat-model",
                              "data_engineer": "de-model"}[e["role"]]

from datetime import date

import pytest

from adaptive_harness.__main__ import SUBCOMMANDS, build_parser, main
from adaptive_harness.contracts import (
    DuplicateKey,
    ModelClient,
    ModelUnavailable,
    Store,
    Tool,
    ToolResult,
    event_id,
)
from adaptive_harness.testing import FakeModel, FakeStore, FakeTool, FakeTools


def test_fakes_satisfy_interfaces():
    assert isinstance(FakeStore(), Store)
    assert isinstance(FakeModel([]), ModelClient)
    assert isinstance(FakeTool("list_events", default={}), Tool)


def test_fake_store_crud_events_pin_and_search():
    s = FakeStore()
    s.init()
    s.insert("runs", {"_id": "r1", "case_id": "T1", "totals": {"model_calls": 1}})
    with pytest.raises(DuplicateKey):
        s.insert("runs", {"_id": "r1"})
    s.update("runs", "r1", {"status": "completed", "totals.tool_calls": 2})
    assert s.get("runs", "r1")["totals"] == {"model_calls": 1, "tool_calls": 2}
    assert [d["_id"] for d in s.find("runs", {"status": {"$in": ["completed"]}})] == ["r1"]

    assert s.next_seq("r1") == 0
    ev = {"_id": event_id("r1", 0), "run_id": "r1", "seq": 0, "type": "message"}
    assert s.append_event(ev) is True
    assert s.append_event(ev) is False  # idempotent retry
    assert s.next_seq("r1") == 1

    for v in ("v1", "v2"):
        s.insert("harness_versions", {"_id": v, "pinned": v == "v1"})
    s.pin_version("v2")
    assert s.pinned_version()["_id"] == "v2" and not s.get("harness_versions", "v1")["pinned"]

    s.insert("lessons", {"_id": "a", "embedding": [1.0, 0.0], "status": "verified", "scope": "x", "snapshot": "M1"})
    s.insert("lessons", {"_id": "b", "embedding": [0.0, 1.0], "status": "verified", "scope": "x", "snapshot": "M1"})
    s.insert("lessons", {"_id": "c", "embedding": [1.0, 0.1], "status": "provisional", "scope": "x", "snapshot": "M1"})
    hits = s.vector_search_lessons([1.0, 0.0], 5, {"status": "verified", "scope": "x"})
    assert [h["_id"] for h in hits] == ["a", "b"] and hits[0]["score"] > hits[1]["score"]
    assert s.vector_search_lessons([1.0, 0.0], 0, {}) == []


def test_fake_model_scripted_in_order():
    m = FakeModel([{"action": "finish", "output": {}}, "not json", RuntimeError("boom")],
                  assignment={"statistician": "stat-model"})
    r = m.complete("statistician", "sys", [{"role": "user", "content": "go"}])
    assert r.parsed == {"action": "finish", "output": {}} and r.model == "stat-model"
    r = m.complete("race_engineer", "sys", [])
    assert r.parsed is None and r.text == "not json" and r.model == "fake-model"
    with pytest.raises(RuntimeError):
        m.complete("race_engineer", "sys", [])
    with pytest.raises(ModelUnavailable):
        m.complete("race_engineer", "sys", [])
    assert len(m.calls) == 4


def test_fake_tools_canned():
    tools = FakeTools(list_events={FakeTool.key({"year": 2030}): {"events": []}})
    tools.add(FakeTool("interval", default=lambda args, as_of: ToolResult.build("ok", {"n": args["n"]})))
    r = tools.call("list_events", {"year": 2030}, date(2031, 1, 1))
    assert r.payload == {"events": []} and len(r.sha256) == 64
    assert tools.call("interval", {"n": 3}, date(2031, 1, 1)).payload == {"n": 3}
    with pytest.raises(KeyError):
        tools.call("list_events", {"year": 1999}, date(2031, 1, 1))
    assert len(tools.calls) == 3


def test_cli_lists_every_subcommand(capsys):
    help_text = build_parser().format_help()
    for name in ("init-db", "run", "define", "measure", "analyze", "improve", "control", "cycle",
                 "report", "maps", "show"):
        assert name in SUBCOMMANDS and name in help_text


def test_cli_unimplemented_names_lane(capsys, monkeypatch):
    import adaptive_harness.__main__ as cli

    monkeypatch.setitem(cli.SUBCOMMANDS, "zzz-test", ("nolane", "test"))
    assert main(["zzz-test"]) == 2
    assert "lane nolane" in capsys.readouterr().err

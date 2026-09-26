"""`show` views: experiment, version, lesson, lessons (vector search), cost (FakeStore, no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adaptive_harness.report import cli, show
from adaptive_harness.store import cli as store_cli
from adaptive_harness.testing import FakeStore

T0 = datetime(2031, 1, 1, tzinfo=timezone.utc)
LONG = ("This reason is deliberately long so that it has to be wrapped across several lines of output "
        "instead of running off the right edge of a narrow terminal window. ") * 3
VEC = [0.123456789] * 4


def _run(store, rid, arm, version, status="completed", wall=10.0):
    store.insert("runs", {"_id": rid, "harness_version": version, "config_hash": "h", "memory_snapshot": "M1",
                          "case_id": rid.split("-")[0], "arm": arm, "experiment_id": "x1",
                          "model": {"provider": "fake", "assignment": {}}, "fastf1_version": "0",
                          "started_at": T0, "status": status,
                          "totals": {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0,
                                     "cache_read_tokens": 0, "wall_seconds": wall}})


def _call(store, rid, seq, role, model, inp, out, cache=0, type_="model_call"):
    store.insert("events", {"_id": f"{rid}:{seq:05d}", "run_id": rid, "seq": seq, "stage_id": "S1", "role": role,
                            "type": type_, "content": {}, "model": model, "created_at": T0, "content_sha256": "0" * 64,
                            "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_tokens": cache}})


@pytest.fixture()
def store() -> FakeStore:
    s = FakeStore()
    s.close = lambda: None
    phase = {"status": "passed", "tollgate": {"passed": True, "reasons": [LONG]}, "artifact": None}
    s.insert("experiments", {
        "_id": "x1", "cases": [{"id": "D1", "set": "development"}, {"id": "P1", "set": "control"}],
        "arms": ["baseline", "memory_only", "candidate"],
        "dmaic": {"define": phase, "measure": phase, "analyze": phase,
                  "improve": {"status": "stopped", "tollgate": {"passed": False, "reasons": ["R2 fails"]}},
                  "control": {"status": "not_reached", "tollgate": None}},
        "acceptance": {r: {"holds": r != "R2", "detail": f"{r} detail {LONG}"} for r in show.RULES},
        "decision": "rejected", "candidate_version": "v2", "report_path": "reports/x1.md", "created_at": T0,
    })
    s.insert("harness_versions", {"_id": "v1", "parent_id": None, "config": {}, "config_hash": "a" * 64,
                                  "status": "baseline", "pinned": True, "created_at": T0})
    s.insert("harness_versions", {
        "_id": "v2", "parent_id": "v1", "config": {}, "config_hash": "b" * 64, "status": "rejected", "pinned": False,
        "changes": [{"op": {"op": "replace", "path": "/checks/venue_match/enabled", "value": True},
                     "lesson_id": "lesson:x1:D1-r:E7", "why": LONG}],
        "rationale": LONG, "validation": {"valid": True, "reasons": []}, "decision_reasons": ["R2 fails: " + LONG],
        "created_at": T0})
    for i, (snap, text) in enumerate([("M1", "Cite a tool result for every race row.\nSecond line."),
                                      ("M0", "An older lesson.")]):
        s.insert("lessons", {
            "_id": f"lesson:{i}", "defect": {"run_id": "D1-r", "case_id": "D1", "check_id": "E7"},
            "failure_category": "evidence_missing", "cause_category": "measurement", "controllable": True,
            "why_chain": ["first why " + LONG, "second why"], "origin_event_id": "D1-r:00002",
            "source_event_ids": ["D1-r:00001", "D1-r:00002"], "text": text, "status": "verified",
            "scope": "neutralization_brief", "snapshot": snap, "embedding": [1.0, 0.0, 0.0, float(i)],
            "embedding_model": "test-model", "created_at": T0})
    _run(s, "D1-b", "baseline", "v1")
    _run(s, "P1-b", "baseline", "v1", status="harness_error", wall=5.0)
    _run(s, "D1-c", "candidate", "v2", wall=20.0)
    _call(s, "D1-b", 0, "race_engineer", "m-big", 100, 10, 5)
    _call(s, "D1-b", 1, "race_engineer", "m-big", 200, 20, 0)
    _call(s, "P1-b", 0, "data_engineer", "m-small", 50, 5, 1000)
    _call(s, "D1-c", 0, "race_engineer", "m-big", 300, 30, 0)
    _call(s, "D1-c", 1, "race_engineer", "m-big", 0, 0, 0, type_="message")  # not a model call
    s.insert("runs", {"_id": "other", "harness_version": "v1", "config_hash": "h", "memory_snapshot": "M0",
                      "case_id": "D1", "arm": "baseline", "experiment_id": "x9", "model": {"provider": "f",
                      "assignment": {}}, "fastf1_version": "0", "started_at": T0})
    _call(s, "other", 0, "race_engineer", "m-big", 9999, 9999)  # another experiment
    return s


def _fits(lines, width):
    assert lines and all(len(ln) <= width for ln in lines), [ln for ln in lines if len(ln) > width]


# ---------------------------------------------------------------- experiment


def test_show_experiment(store):
    lines = show.show_experiment(store, "x1", width=70)
    _fits(lines, 70)
    text = "\n".join(lines)
    assert lines[0] == "experiment x1  decision rejected"
    assert "candidate version: v2" in text and "pinned version now: v1" in text
    assert "improve  stopped      tollgate not passed" in text and "control  not_reached  tollgate -" in text
    assert "R2 fails  R2 detail" in text and "R0 holds" in text
    rows = {ln.split()[0]: ln.split() for ln in lines if ln.startswith("  baseline") or ln.startswith("  candidate")}
    assert rows["baseline"] == ["baseline", "v1", "2", "1", "15"]
    assert rows["candidate"] == ["candidate", "v2", "1", "0", "20"]
    with pytest.raises(KeyError):
        show.show_experiment(store, "nope")


def test_runs_per_arm_pipeline_shape():
    p = show.runs_per_arm_pipeline("x1")
    assert p[0] == {"$match": {"experiment_id": "x1"}}
    assert p[1]["$group"]["_id"] == "$arm" and p[1]["$group"]["runs"] == {"$sum": 1}
    assert list(p[2]) == ["$sort"]


def test_runs_per_arm_uses_aggregate_when_the_store_has_it(store):
    seen = []

    def aggregate(collection, pipeline):
        seen.append((collection, pipeline))
        return [{"_id": "candidate", "runs": 4, "harness": 0, "wall_seconds": 1.0, "versions": ["v2"]},
                {"_id": "baseline", "runs": 3, "harness": 0, "wall_seconds": 1.0, "versions": ["v1"]}]

    store.aggregate = aggregate
    rows = show.runs_per_arm(store, "x1")
    assert seen == [("runs", show.runs_per_arm_pipeline("x1"))]
    assert [r["_id"] for r in rows] == ["baseline", "candidate"]  # arm order, not alphabetical


# ---------------------------------------------------------------- version and lesson


def test_show_version(store):
    lines = show.show_version(store, "v2", width=72)
    _fits(lines, 72)
    text = "\n".join(lines)
    assert lines[0] == "version v2  status rejected  pinned False  parent v1"
    assert "config_hash: " + "b" * 50 in text.replace("\n", "")  or "b" * 40 in text
    assert "1. op replace /checks/venue_match/enabled = true" in text
    assert "lesson: lesson:x1:D1-r:E7" in text and "why: This reason" in text
    assert "decision reasons" in text
    with pytest.raises(KeyError):
        show.show_version(store, "v9")


def test_show_lesson_never_prints_the_vector(store):
    store.update("lessons", "lesson:0", {"embedding": VEC})
    lines = show.show_lesson(store, "lesson:0", width=60)
    _fits(lines, 60)
    text = "\n".join(lines)
    assert "cause category: measurement" in text and "failure category: evidence_missing" in text
    assert "1. first why" in text and "2. second why" in text
    assert "snapshot M1" in text and "test-model, 4 dimensions (vector not shown)" in text
    assert "Cite a tool result for every race row." in text
    assert "0.123" not in text
    with pytest.raises(KeyError):
        show.show_lesson(store, "lesson:9")


# ---------------------------------------------------------------- lessons (vector search)


def test_lesson_search_pipeline_shape():
    p = show.lesson_search_pipeline([1, 0], 5, {"snapshot": "M1"})
    vs = p[0]["$vectorSearch"]
    assert vs["index"] == "lessons_vector" and vs["limit"] == 5 and vs["filter"] == {"snapshot": {"$eq": "M1"}}
    assert p[1] == {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}
    assert p[-1] == {"$project": {"embedding": 0}}


def test_show_lessons_with_snapshot_prefilter(store):
    queries = []

    def embed(text):
        queries.append(text)
        return [1.0, 0.0, 0.0, 0.0]

    lines = show.show_lessons(store, "race row citations", embed, k=5, snapshot="M1", width=60)
    _fits(lines, 60)
    text = "\n".join(lines)
    assert queries == ["race row citations"]
    assert "pre-filter: snapshot = M1" in text
    assert "lesson:0" in text and "lesson:1" not in text  # M0 lesson filtered out
    assert "score: 1.0000  cause measurement" in text
    assert "Cite a tool result for every race row." in text and "Second line" not in text

    all_hits = show.show_lessons(store, "q", embed, k=5, width=60)
    assert "lesson:1" in "\n".join(all_hits)
    none = show.show_lessons(store, "q", embed, k=5, snapshot="M7", width=60)
    assert none[-1] == "  no lessons found"


def test_show_lessons_uses_the_aggregate_pipeline_when_available(store):
    seen = []
    store.aggregate = lambda c, p: seen.append((c, p)) or [
        {"_id": "L", "score": 0.5, "cause_category": "method", "text": "x " * 200}]
    lines = show.show_lessons(store, "q", lambda t: [0.0, 1.0], k=2, snapshot="M1", width=50)
    assert seen[0][0] == "lessons" and seen[0][1] == show.lesson_search_pipeline([0.0, 1.0], 2, {"snapshot": "M1"})
    _fits(lines, 50)
    assert lines[-1].endswith("...") and len([ln for ln in lines if ln.startswith("   x")]) == show.SEARCH_TEXT_LINES


# ---------------------------------------------------------------- cost


def test_cost_pipeline_shape():
    p = show.cost_pipeline(["r1", "r2"])
    assert [list(s)[0] for s in p] == ["$match", "$lookup", "$unwind", "$group", "$sort"]
    assert p[0]["$match"] == {"run_id": {"$in": ["r1", "r2"]}, "type": "model_call"}
    assert p[1]["$lookup"] == {"from": "runs", "localField": "run_id", "foreignField": "_id", "as": "run"}
    g = p[3]["$group"]
    assert g["_id"] == {"arm": "$run.arm", "role": "$role", "model": "$model"}
    assert g["calls"] == {"$sum": 1}
    for f in ("input_tokens", "output_tokens", "cache_read_tokens"):
        assert g[f] == {"$sum": {"$ifNull": [f"$usage.{f}", 0]}}


def test_cost_rows_python_fallback(store):
    rows = show.cost_rows(store, "x1")
    assert rows == [
        {"arm": "baseline", "role": "data_engineer", "model": "m-small", "calls": 1,
         "input_tokens": 50, "output_tokens": 5, "cache_read_tokens": 1000},
        {"arm": "baseline", "role": "race_engineer", "model": "m-big", "calls": 2,
         "input_tokens": 300, "output_tokens": 30, "cache_read_tokens": 5},
        {"arm": "candidate", "role": "race_engineer", "model": "m-big", "calls": 1,
         "input_tokens": 300, "output_tokens": 30, "cache_read_tokens": 0},
    ]


def test_cost_rows_via_aggregate(store):
    seen = []
    store.aggregate = lambda c, p: seen.append((c, p)) or [
        {"_id": {"arm": "candidate", "role": "r", "model": "m"}, "calls": 1, "input_tokens": 1,
         "output_tokens": 2, "cache_read_tokens": 3}]
    rows = show.cost_rows(store, "x1")
    assert seen[0][0] == "events" and set(seen[0][1][0]["$match"]["run_id"]["$in"]) == {"D1-b", "P1-b", "D1-c"}
    assert rows == [{"arm": "candidate", "role": "r", "model": "m", "calls": 1, "input_tokens": 1,
                     "output_tokens": 2, "cache_read_tokens": 3}]


def test_show_cost_table(store):
    lines = show.show_cost(store, "x1", width=80)
    _fits(lines, 80)
    total = [ln for ln in lines if ln.startswith("total")][0].split()
    assert total == ["total", "4", "650", "65", "1,005"]
    assert not any("usd" in ln.lower() or "price" in ln.lower() for ln in lines)
    with pytest.raises(KeyError):
        show.show_cost(store, "nope")


# ---------------------------------------------------------------- CLI


@pytest.fixture()
def patched(monkeypatch, store):
    monkeypatch.setattr(cli, "open_store", lambda: store)
    monkeypatch.setattr(store_cli.MongoStore, "from_env", classmethod(lambda cls, **kw: store))
    return store


@pytest.mark.parametrize("argv", [
    ["experiment", "x1"], ["version", "v2"], ["lesson", "lesson:0"], ["cost", "--experiment", "x1"],
    ["--width", "50", "experiment", "x1"],
])
def test_cli_show_kinds(patched, capsys, argv):
    assert cli.show(argv) == 0
    out = capsys.readouterr().out.splitlines()
    width = 50 if "--width" in argv else 100
    _fits(out, width)


def test_cli_show_width_option(patched, capsys):
    assert cli.show(["version", "v2", "--width", "60"]) == 0
    _fits(capsys.readouterr().out.splitlines(), 60)


def test_cli_show_lessons(patched, capsys, monkeypatch):
    from adaptive_harness.store import embeddings

    monkeypatch.setattr(embeddings, "embed_query", lambda text: [1.0, 0.0, 0.0, 0.0])
    assert cli.show(["lessons", "--query", "citations", "--snapshot", "M1", "--k", "2"]) == 0
    out = capsys.readouterr().out
    assert "lesson:0" in out and "lesson:1" not in out


def test_cli_show_not_found(patched, capsys):
    assert cli.show(["experiment", "nope"]) == 1
    assert "no experiment 'nope'" in capsys.readouterr().err


def test_cli_show_run_delegates_to_store_lane(patched, capsys):
    assert cli.show(["run", "D1-b"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("run D1-b") and "model_call" in out
    assert cli.show(["--width", "60", "run", "D1-b"]) == 0
    _fits(capsys.readouterr().out.splitlines()[2:], 60)
    assert cli.show(["run", "missing"]) == 1

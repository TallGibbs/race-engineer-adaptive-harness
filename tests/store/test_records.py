"""Store helpers against the FakeStore contract (no network)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_harness.contracts import DuplicateKey, canonical_sha256
from adaptive_harness.contracts.records import Event, Run
from adaptive_harness.store import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    append_event,
    events_for_run,
    evaluations_for_run,
    finish_run,
    get_experiment,
    get_lesson,
    get_run,
    get_version,
    pin_version,
    pinned_version,
    save_evaluation,
    save_experiment,
    save_lesson,
    save_phase,
    save_version,
    search_lessons,
    search_lessons_by_metadata,
    start_run,
    update_experiment,
    update_version,
    write_event,
)
from adaptive_harness.testing import FakeStore

from .samples import evaluation_doc, experiment_doc, lesson_doc, run_doc, version_doc


@pytest.fixture
def store() -> FakeStore:
    s = FakeStore()
    s.init()
    return s


def fake_embed(texts):
    return [[1.0] + [0.0] * (EMBEDDING_DIMS - 1) for _ in texts]


# ---------------------------------------------------------------- runs and events


def test_start_get_finish_run(store):
    assert start_run(store, run_doc()) == "r1"
    with pytest.raises(DuplicateKey):
        start_run(store, run_doc())
    run = get_run(store, "r1")
    assert isinstance(run, Run) and run.status is None and run.ended_at is None

    done = finish_run(store, "r1", "completed", output={"case_id": "T1"},
                      totals={"model_calls": 3, "tool_calls": 2, "wall_seconds": 1.5})
    assert done.status == "completed"
    stored = get_run(store, "r1")
    assert stored.status == "completed" and stored.ended_at is not None
    assert stored.totals.model_calls == 3 and stored.output == {"case_id": "T1"}
    assert get_run(store, "missing") is None


def test_finish_run_rejects_bad_status_and_missing_run(store):
    start_run(store, run_doc())
    with pytest.raises(ValidationError):
        finish_run(store, "r1", "exploded")
    assert get_run(store, "r1").status is None  # nothing written
    with pytest.raises(KeyError):
        finish_run(store, "nope", "completed")


def test_start_run_validates(store):
    with pytest.raises(ValidationError):
        start_run(store, run_doc(arm="not-an-arm"))


def test_append_event_ids_hash_and_order(store):
    start_run(store, run_doc())
    a = append_event(store, "r1", "message", {"text": "hello"}, stage_id="S1", role="race_engineer")
    b = append_event(store, "r1", "tool_call", {"tool": "list_events", "args": {"year": 2030}},
                     stage_id="S4", role="data_engineer", refs=[a])
    c = append_event(store, "r1", "tool_result", {"text": "ok"}, stage_id="S4", role="data_engineer", refs=[b])
    assert (a, b, c) == ("r1:00000", "r1:00001", "r1:00002")
    events = events_for_run(store, "r1")
    assert [e.seq for e in events] == [0, 1, 2] and all(isinstance(e, Event) for e in events)
    assert events[0].content_sha256 == canonical_sha256({"text": "hello"})
    assert events[2].refs == ["r1:00001"]


def test_append_event_idempotent_with_explicit_seq(store):
    first = append_event(store, "r1", "message", {"text": "x"}, seq=7)
    again = append_event(store, "r1", "message", {"text": "x"}, seq=7)  # a retry
    assert first == again == "r1:00007"
    assert len(events_for_run(store, "r1")) == 1
    assert append_event(store, "r1", "message", "next") == "r1:00008"


def test_events_sorted_by_seq_not_insertion(store):
    for seq in (3, 1, 2):
        append_event(store, "r1", "message", seq, seq=seq)
    assert [e.seq for e in events_for_run(store, "r1")] == [1, 2, 3]


def test_write_event_prebuilt_and_validation(store):
    ev = {"_id": "r1:00000", "run_id": "r1", "seq": 0, "type": "decision", "content": "GO",
          "content_sha256": canonical_sha256("GO"), "created_at": run_doc()["started_at"]}
    assert write_event(store, ev) == "r1:00000"
    assert write_event(store, ev) == "r1:00000"
    with pytest.raises(ValidationError):
        write_event(store, {**ev, "_id": "r1:1"})  # _id must match run_id and seq
    with pytest.raises(ValidationError):
        append_event(store, "r1", "shouting", "x")


def test_model_call_event_fields(store):
    eid = append_event(store, "r1", "model_call", {"text": "{}"}, stage_id="S1", role="race_engineer",
                       context_manifest=[{"item": "task", "id": None, "sha256": "a", "approx_tokens": 10}],
                       usage={"input_tokens": 5, "output_tokens": 2}, model="m", stop_reason="end_turn")
    ev = events_for_run(store, "r1")[0]
    assert ev.id == eid and ev.model == "m" and ev.usage.input_tokens == 5
    assert ev.context_manifest[0].item == "task"


# ---------------------------------------------------------------- versions


def test_versions_exactly_one_pinned(store):
    save_version(store, version_doc("v1", pinned=True))
    save_version(store, version_doc("v2"))
    assert pinned_version(store).id == "v1"
    save_version(store, version_doc("v3", pinned=True))
    assert pinned_version(store).id == "v3"
    assert [v for v in ("v1", "v2", "v3") if get_version(store, v).pinned] == ["v3"]
    pin_version(store, "v1")
    assert [v for v in ("v1", "v2", "v3") if get_version(store, v).pinned] == ["v1"]
    with pytest.raises(KeyError):
        pin_version(store, "v9")
    assert get_version(store, "v9") is None


def test_update_version(store):
    save_version(store, version_doc("v2"))
    v = update_version(store, "v2", {"status": "rejected", "decision_reasons": ["R2 failed"]})
    assert v.status == "rejected" and get_version(store, "v2").decision_reasons == ["R2 failed"]
    with pytest.raises(ValueError):
        update_version(store, "v2", {"pinned": True})
    with pytest.raises(ValidationError):
        update_version(store, "v2", {"status": "maybe"})


def test_pinned_version_none(store):
    assert pinned_version(store) is None


# ---------------------------------------------------------------- evaluations and experiments


def test_evaluations(store):
    save_evaluation(store, evaluation_doc("e1"))
    assert [e.id for e in evaluations_for_run(store, "r1")] == ["e1"]
    bad = evaluation_doc("e2")
    bad["defects"] = 0
    with pytest.raises(ValidationError):
        save_evaluation(store, bad)


def test_save_phase(store):
    save_experiment(store, experiment_doc())
    phase = save_phase(store, "x1", "define", {"dpo": 0.25}, {"passed": True, "reasons": []})
    assert phase.status == "passed"
    save_phase(store, "x1", "measure", {"valid": False}, {"passed": False, "reasons": ["hash mismatch"]})
    exp = get_experiment(store, "x1")
    assert exp.dmaic.define.status == "passed" and exp.dmaic.define.artifact == {"dpo": 0.25}
    assert exp.dmaic.measure.status == "stopped" and exp.dmaic.measure.tollgate.reasons == ["hash mismatch"]
    assert exp.dmaic.analyze.status == "not_reached"
    with pytest.raises(ValueError):
        save_phase(store, "x1", "dream", {}, {"passed": True})
    with pytest.raises(KeyError):
        save_phase(store, "x9", "define", {}, {"passed": True})


def test_update_experiment(store):
    save_experiment(store, experiment_doc())
    exp = update_experiment(store, "x1", {"decision": "stopped", "report_path": "reports/x1.md"})
    assert exp.decision == "stopped" and get_experiment(store, "x1").report_path == "reports/x1.md"
    with pytest.raises(ValidationError):
        update_experiment(store, "x1", {"report_path": "/abs/path.md"})
    assert get_experiment(store, "missing") is None


# ---------------------------------------------------------------- lessons


def test_save_lesson_embeds_with_model_name(store):
    save_lesson(store, lesson_doc("L1"), embed_fn=fake_embed)
    lesson = get_lesson(store, "L1")
    assert lesson.embedding_model == EMBEDDING_MODEL and len(lesson.embedding) == EMBEDDING_DIMS


def test_save_lesson_keeps_given_embedding_and_checks_dims(store):
    save_lesson(store, lesson_doc("L1", embedding=[0.5, 0.5]))
    assert get_lesson(store, "L1").embedding == [0.5, 0.5]
    with pytest.raises(ValueError):
        save_lesson(store, lesson_doc("L2", embedding=[0.1, 0.2], embedding_model=EMBEDDING_MODEL))
    with pytest.raises(ValueError):
        save_lesson(store, lesson_doc("L3", embedding=[0.1], embedding_model=None))


def test_search_lessons_filters_and_snapshots(store):
    save_lesson(store, lesson_doc("a", embedding=[1.0, 0.0]))
    save_lesson(store, lesson_doc("b", embedding=[0.6, 0.4]))
    save_lesson(store, lesson_doc("p", embedding=[1.0, 0.0], status="provisional"))
    save_lesson(store, lesson_doc("m0", embedding=[1.0, 0.0], snapshot="M0"))
    filters = {"status": "verified", "scope": "neutralization_brief"}

    hits = search_lessons(store, [1.0, 0.0], 5, filters, ["M1"])
    assert [h["_id"] for h in hits] == ["a", "b"]
    assert hits[0]["score"] > hits[1]["score"] and "embedding" not in hits[0]

    assert [h["_id"] for h in search_lessons(store, [1.0, 0.0], 5, filters, ["M0"])] == ["m0"]
    assert {h["_id"] for h in search_lessons(store, [1.0, 0.0], 5, filters, ["M0", "M1"])} == {"a", "b", "m0"}
    assert search_lessons(store, [1.0, 0.0], 1, filters, ["M1"])[0]["_id"] == "a"
    assert search_lessons(store, [1.0, 0.0], 0, filters, ["M1"]) == []
    assert search_lessons(store, [1.0, 0.0], 3, filters, []) == []
    with pytest.raises(ValueError):
        search_lessons(store, [1.0, 0.0], 3, {"text": "x"}, ["M1"])


def test_search_lessons_by_metadata_recency(store):
    for i, lid in enumerate(["old", "mid", "new"]):
        save_lesson(store, lesson_doc(lid, embedding=[1.0], minutes=i))
    save_lesson(store, lesson_doc("prov", embedding=[1.0], minutes=9, status="provisional"))
    hits = search_lessons_by_metadata(store, {"status": "verified"}, 2)
    assert [h["_id"] for h in hits] == ["new", "mid"] and "embedding" not in hits[0]
    assert search_lessons_by_metadata(store, {"status": "verified"}, 5, allowed_snapshots=["M0"]) == []
    assert len(search_lessons_by_metadata(store, None, 10)) == 4
    assert search_lessons_by_metadata(store, None, 0) == []

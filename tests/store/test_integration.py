"""Integration test against MongoDB Atlas. Skipped unless MONGODB_URI is set in the
environment (this module does not read .env), for example:

    python -m dotenv run -- pytest tests/store/test_integration.py

It writes into the database "<MONGODB_DB>_it", scopes every document to a fresh id, and
deletes what it wrote. The embedding model is downloaded on first use.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("MONGODB_URI"), reason="MONGODB_URI not set")


@pytest.fixture(scope="module")
def atlas():
    from adaptive_harness.store import MongoStore

    db_name = (os.environ.get("MONGODB_DB") or "adaptive_harness") + "_it"
    store = MongoStore.from_env(db_name=db_name)
    store.init(wait_seconds=300)
    yield store
    store.close()


def test_run_events_lesson_and_vector_query(atlas):
    from adaptive_harness.contracts import DuplicateKey
    from adaptive_harness.store import (
        EMBEDDING_DIMS,
        EMBEDDING_MODEL,
        append_event,
        embed_query,
        events_for_run,
        finish_run,
        get_lesson,
        get_run,
        save_lesson,
        search_lessons,
        search_lessons_by_metadata,
        start_run,
    )
    from adaptive_harness.store.cli import show_run

    from .samples import lesson_doc, run_doc

    tag = uuid.uuid4().hex[:10]
    run_id, lesson_id, scope = f"it-{tag}", f"it-L-{tag}", f"it-scope-{tag}"
    try:
        start_run(atlas, run_doc(run_id))
        with pytest.raises(DuplicateKey):
            start_run(atlas, run_doc(run_id))
        e0 = append_event(atlas, run_id, "message", {"text": "frame the question"}, seq=0,
                          stage_id="S1", role="race_engineer")
        e1 = append_event(atlas, run_id, "tool_call", {"tool": "list_events", "args": {"year": 2030}}, seq=1,
                          stage_id="S4", role="data_engineer", refs=[e0])
        assert append_event(atlas, run_id, "tool_call", {"tool": "list_events", "args": {"year": 2030}}, seq=1,
                            stage_id="S4", role="data_engineer", refs=[e0]) == e1  # idempotent retry
        e2 = append_event(atlas, run_id, "tool_result", {"text": "2 events"}, stage_id="S4",
                          role="data_engineer", refs=[e1])
        assert e2 == f"{run_id}:00002"
        finish_run(atlas, run_id, "completed", totals={"model_calls": 0, "tool_calls": 1, "wall_seconds": 0.5})

        run = get_run(atlas, run_id)
        assert run.status == "completed" and run.started_at.tzinfo is not None
        events = events_for_run(atlas, run_id)
        assert [e.id for e in events] == [e0, e1, e2] and events[2].refs == [e1]
        assert len(show_run(atlas, run_id)) == 2 + 3

        text = "Confirm the venue of the target race before collecting prior races."
        save_lesson(atlas, lesson_doc(lesson_id, text=text, scope=scope))
        stored = get_lesson(atlas, lesson_id)
        assert stored.embedding_model == EMBEDDING_MODEL and len(stored.embedding) == EMBEDDING_DIMS

        # Atlas Search indexes new documents asynchronously; poll briefly.
        import time

        query = embed_query("check which venue the race was held at")
        hits = []
        for _ in range(30):
            hits = search_lessons(atlas, query, 3, {"scope": scope, "status": "verified"}, ["M1"])
            if hits:
                break
            time.sleep(2)
        assert [h["_id"] for h in hits] == [lesson_id]
        assert isinstance(hits[0]["score"], float) and hits[0]["score"] > 0.5 and "embedding" not in hits[0]
        assert search_lessons(atlas, query, 3, {"scope": scope, "status": "verified"}, ["M0"]) == []
        assert [h["_id"] for h in search_lessons_by_metadata(atlas, {"scope": scope}, 3)] == [lesson_id]
    finally:
        atlas.db["events"].delete_many({"run_id": run_id})
        atlas.db["runs"].delete_one({"_id": run_id})
        atlas.db["lessons"].delete_one({"_id": lesson_id})


def test_pin_version_exactly_one(atlas):
    from adaptive_harness.store import get_version, pin_version, pinned_version, save_version

    from .samples import version_doc

    tag = uuid.uuid4().hex[:10]
    a, b = f"it-va-{tag}", f"it-vb-{tag}"
    previous = pinned_version(atlas)
    try:
        save_version(atlas, version_doc(a, pinned=True))
        save_version(atlas, version_doc(b))
        pin_version(atlas, b)
        assert pinned_version(atlas).id == b and not get_version(atlas, a).pinned
        assert atlas.db["harness_versions"].count_documents({"pinned": True}) == 1
    finally:
        atlas.db["harness_versions"].delete_many({"_id": {"$in": [a, b]}})
        if previous is not None:
            pin_version(atlas, previous.id)

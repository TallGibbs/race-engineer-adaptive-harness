"""Pure pieces of the Mongo store, the embedder wrapper, and the CLI (no network)."""

from __future__ import annotations

import pytest

from adaptive_harness.contracts import Store, load_config
from adaptive_harness.store import cli, embeddings
from adaptive_harness.store.mongo import (
    INDEXES,
    VECTOR_INDEX,
    MongoStore,
    vector_filter,
    vector_index_definition,
    vector_search_pipeline,
)
from adaptive_harness.store import append_event, get_version, pinned_version, save_version, start_run
from adaptive_harness.contracts.records import COLLECTIONS
from adaptive_harness.testing import FakeStore

from .samples import run_doc, version_doc


def test_mongo_store_satisfies_interface():
    assert isinstance(MongoStore.__new__(MongoStore), Store)


def test_indexes_cover_every_collection_and_event_uniqueness():
    assert set(INDEXES) == set(COLLECTIONS)
    run_seq = [i.document for i in INDEXES["events"] if i.document["name"] == "run_seq"][0]
    assert run_seq["unique"] is True and list(run_seq["key"].items()) == [("run_id", 1), ("seq", 1)]
    pinned = [i.document for i in INDEXES["harness_versions"] if i.document["name"] == "one_pinned"][0]
    assert pinned["unique"] and pinned["partialFilterExpression"] == {"pinned": True}


def test_vector_index_definition():
    d = vector_index_definition()
    vec = [f for f in d["fields"] if f["type"] == "vector"][0]
    assert vec == {"type": "vector", "path": "embedding", "numDimensions": 384, "similarity": "cosine"}
    assert sorted(f["path"] for f in d["fields"] if f["type"] == "filter") == ["scope", "snapshot", "status"]


def test_vector_filter_translation():
    assert vector_filter({}) is None
    assert vector_filter({"status": "verified"}) == {"status": {"$eq": "verified"}}
    assert vector_filter({"status": "verified", "snapshot": {"$in": ["M0"]}}) == {
        "$and": [{"status": {"$eq": "verified"}}, {"snapshot": {"$in": ["M0"]}}]
    }
    with pytest.raises(ValueError):
        vector_filter({"text": "x"})


def test_vector_search_pipeline():
    p = vector_search_pipeline([1, 0], 3, {"scope": "s"})
    vs = p[0]["$vectorSearch"]
    assert vs["index"] == VECTOR_INDEX and vs["path"] == "embedding" and vs["limit"] == 3
    assert vs["queryVector"] == [1.0, 0.0] and vs["numCandidates"] >= 3 and vs["filter"] == {"scope": {"$eq": "s"}}
    assert p[1] == {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}
    assert "filter" not in vector_search_pipeline([1.0], 1, {})[0]["$vectorSearch"]


def test_embed_wrapper(monkeypatch):
    class Model:
        def embed(self, texts):
            return [[0.5] * embeddings.EMBEDDING_DIMS for _ in texts]

        def query_embed(self, text):
            return iter([[0.25] * embeddings.EMBEDDING_DIMS])

    monkeypatch.setattr(embeddings, "_model", lambda: Model())
    vecs = embeddings.embed(["a", "b"])
    assert len(vecs) == 2 and len(vecs[0]) == 384 and isinstance(vecs[0][0], float)
    assert embeddings.embed([]) == []
    assert embeddings.embed_query("q")[0] == 0.25
    with pytest.raises(TypeError):
        embeddings.embed("single string")


def test_embed_wrapper_rejects_wrong_dims(monkeypatch):
    class Model:
        def embed(self, texts):
            return [[0.5] * 10 for _ in texts]

    monkeypatch.setattr(embeddings, "_model", lambda: Model())
    with pytest.raises(ValueError):
        embeddings.embed(["a"])


def test_register_baseline_pins_v1_once():
    s = FakeStore()
    lines: list[str] = []
    cli.register_baseline(s, log=lines.append)
    v1 = get_version(s, "v1")
    cfg = load_config()
    assert v1.status == "baseline" and v1.pinned and v1.config_hash == cfg.config_hash()
    assert v1.config == cfg.to_dict()
    cli.register_baseline(s, log=lines.append)  # idempotent
    assert pinned_version(s).id == "v1"


def test_register_baseline_keeps_existing_pin():
    s = FakeStore()
    save_version(s, version_doc("v2", pinned=True))
    cli.register_baseline(s, log=lambda m: None)
    assert pinned_version(s).id == "v2" and not get_version(s, "v1").pinned


def test_show_run_compact_and_ordered():
    s = FakeStore()
    start_run(s, run_doc())
    append_event(s, "r1", "tool_call", {"tool": "list_events", "args": {"year": 2030}}, seq=1,
                 stage_id="S4", role="data_engineer")
    append_event(s, "r1", "message", {"text": "framing " + "x" * 500}, seq=0, stage_id="S1", role="race_engineer")
    append_event(s, "r1", "model_call", {"text": "{}"}, seq=2, stage_id="S5", role="statistician",
                 model="m", refs=["r1:00001"])
    lines = cli.show_run(s, "r1", width=100)
    assert lines[0].startswith("run r1") and "status running" in lines[0]
    body = lines[2:]
    assert [ln.split()[0] for ln in body] == ["0", "1", "2"]
    assert "list_events(" in body[1] and body[0].endswith("...") and all(len(ln) <= 100 for ln in body)
    assert "[m]" in body[2] and "refs 00001" in body[2]
    with pytest.raises(KeyError):
        cli.show_run(s, "nope")


def test_init_db_reports_connection_problem(monkeypatch, capsys):
    monkeypatch.setattr(cli.MongoStore, "from_env",
                        classmethod(lambda cls, **kw: (_ for _ in ()).throw(cli.StoreConnectionError("no IP"))))
    assert cli.init_db([]) == 1
    assert "no IP" in capsys.readouterr().err

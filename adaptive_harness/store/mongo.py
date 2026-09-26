"""MongoDB Atlas implementation of contracts.interfaces.Store (CONTRACT.md section C)."""

from __future__ import annotations

import os
import time
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure
from pymongo.operations import SearchIndexModel

from ..contracts.errors import DuplicateKey
from ..contracts.records import COLLECTIONS, event_id
from .embeddings import EMBEDDING_DIMS

VECTOR_INDEX = "lessons_vector"
VECTOR_FILTER_FIELDS: tuple[str, ...] = ("scope", "status", "snapshot")

# Secondary indexes per collection. The events (run_id, seq) index is the idempotency
# guard; the partial unique index on pinned allows at most one pinned harness version.
INDEXES: dict[str, list[IndexModel]] = {
    "runs": [
        IndexModel([("experiment_id", ASCENDING), ("arm", ASCENDING), ("case_id", ASCENDING)], name="experiment_arm_case"),
        IndexModel([("harness_version", ASCENDING), ("started_at", DESCENDING)], name="version_started"),
        IndexModel([("case_id", ASCENDING), ("started_at", DESCENDING)], name="case_started"),
    ],
    "events": [
        IndexModel([("run_id", ASCENDING), ("seq", ASCENDING)], name="run_seq", unique=True),
        IndexModel([("run_id", ASCENDING), ("type", ASCENDING)], name="run_type"),
    ],
    "harness_versions": [
        IndexModel([("pinned", ASCENDING)], name="one_pinned", unique=True,
                   partialFilterExpression={"pinned": True}),
        IndexModel([("status", ASCENDING)], name="status"),
    ],
    "lessons": [
        IndexModel([("scope", ASCENDING), ("status", ASCENDING), ("snapshot", ASCENDING), ("created_at", DESCENDING)],
                   name="scope_status_snapshot_recent"),
        IndexModel([("defect.run_id", ASCENDING)], name="defect_run"),
    ],
    "evaluations": [
        IndexModel([("run_id", ASCENDING)], name="run"),
        IndexModel([("experiment_id", ASCENDING), ("arm", ASCENDING), ("case_id", ASCENDING)], name="experiment_arm_case"),
    ],
    "experiments": [
        IndexModel([("created_at", DESCENDING)], name="created"),
    ],
}


def vector_index_definition(dims: int = EMBEDDING_DIMS) -> dict[str, Any]:
    return {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"},
            *({"type": "filter", "path": f} for f in VECTOR_FILTER_FIELDS),
        ]
    }


def vector_filter(filters: Mapping[str, Any]) -> dict[str, Any] | None:
    """Translate store filters into a $vectorSearch pre-filter.

    Only the indexed filter fields are allowed. A plain value means equality; an operator
    dict ($in, $nin, $ne, ...) is passed through.
    """
    clauses = []
    for field, cond in filters.items():
        if field not in VECTOR_FILTER_FIELDS:
            raise ValueError(f"lessons can only be filtered on {VECTOR_FILTER_FIELDS}, not {field!r}")
        if isinstance(cond, Mapping) and cond and all(str(k).startswith("$") for k in cond):
            clauses.append({field: dict(cond)})
        else:
            clauses.append({field: {"$eq": cond}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def vector_search_pipeline(vector: Sequence[float], k: int, filters: Mapping[str, Any]) -> list[dict[str, Any]]:
    stage: dict[str, Any] = {
        "index": VECTOR_INDEX,
        "path": "embedding",
        "queryVector": [float(x) for x in vector],
        "numCandidates": max(100, 20 * k),
        "limit": k,
    }
    pre = vector_filter(filters)
    if pre is not None:
        stage["filter"] = pre
    return [{"$vectorSearch": stage}, {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}]


class StoreConnectionError(RuntimeError):
    """The cluster could not be reached (often: this machine's IP is not on the Atlas access list)."""


def load_env() -> None:
    """Load .env from the working directory upward, then the repository root. Never overrides."""
    from dotenv import find_dotenv, load_dotenv

    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)
    from ..contracts.paths import REPO_ROOT

    root_env = Path(REPO_ROOT) / ".env"
    if root_env.exists():
        load_dotenv(root_env, override=False)


class MongoStore:
    """contracts.interfaces.Store on MongoDB Atlas."""

    def __init__(self, client: MongoClient, db_name: str) -> None:
        self.client = client
        self.db = client[db_name]

    @classmethod
    def from_env(cls, *, timeout_ms: int = 10_000, db_name: str | None = None, check: bool = True) -> "MongoStore":
        """Connect with MONGODB_URI and MONGODB_DB (loaded from .env when present)."""
        load_env()
        uri = os.environ.get("MONGODB_URI", "").strip()
        if not uri:
            raise StoreConnectionError("MONGODB_URI is not set (see .env.example)")
        name = db_name or os.environ.get("MONGODB_DB", "").strip() or "adaptive_harness"
        client: MongoClient = MongoClient(
            uri,
            tz_aware=True,
            tzinfo=timezone.utc,
            serverSelectionTimeoutMS=timeout_ms,
            appname="adaptive_harness",
        )
        store = cls(client, name)
        if check:
            store.ping()
        return store

    def ping(self) -> None:
        from pymongo.errors import ServerSelectionTimeoutError

        try:
            self.client.admin.command("ping")
        except ServerSelectionTimeoutError as e:
            raise StoreConnectionError(
                "Timed out connecting to MongoDB Atlas. Check that this machine's current public IP "
                "is on the Atlas IP Access List before debugging anything else."
            ) from e

    def close(self) -> None:
        self.client.close()

    def _coll(self, name: str):
        if name not in COLLECTIONS:
            raise KeyError(f"unknown collection {name!r}")
        return self.db[name]

    # ------------------------------------------------------------ setup

    def init(self, *, wait_seconds: float = 300.0, poll_seconds: float = 3.0, log=None) -> None:
        """Create the six collections, their indexes, and the vector index; wait until the
        vector index is queryable. Idempotent."""
        say = log or (lambda msg: None)
        existing = set(self.db.list_collection_names())
        for name in COLLECTIONS:
            if name not in existing:
                self.db.create_collection(name)
                say(f"created collection {name}")
            created = self.db[name].create_indexes(INDEXES[name]) if INDEXES.get(name) else []
            say(f"indexes on {name}: {', '.join(created) or 'none'}")
        self.ensure_vector_index(log=say)
        self.wait_vector_index(wait_seconds=wait_seconds, poll_seconds=poll_seconds, log=say)

    def _vector_index_info(self) -> dict[str, Any] | None:
        found = list(self.db["lessons"].list_search_indexes(VECTOR_INDEX))
        return found[0] if found else None

    def ensure_vector_index(self, log=None) -> None:
        say = log or (lambda msg: None)
        wanted = vector_index_definition()
        info = self._vector_index_info()
        if info is None:
            self.db["lessons"].create_search_index(
                SearchIndexModel(definition=wanted, name=VECTOR_INDEX, type="vectorSearch")
            )
            say(f"created vector search index {VECTOR_INDEX}")
            return
        current = info.get("latestDefinition") or {}
        if _normalize_fields(current.get("fields")) != _normalize_fields(wanted["fields"]):
            self.db["lessons"].update_search_index(VECTOR_INDEX, wanted)
            say(f"updated vector search index {VECTOR_INDEX}")
        else:
            say(f"vector search index {VECTOR_INDEX} already defined")

    def vector_index_ready(self) -> bool:
        info = self._vector_index_info()
        return bool(info and info.get("queryable") and info.get("status") in (None, "READY"))

    def wait_vector_index(self, *, wait_seconds: float = 300.0, poll_seconds: float = 3.0, log=None) -> None:
        say = log or (lambda msg: None)
        deadline = time.monotonic() + wait_seconds
        while True:
            info = self._vector_index_info() or {}
            if info.get("queryable") and info.get("status") in (None, "READY"):
                say(f"vector search index {VECTOR_INDEX} is queryable")
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"vector search index {VECTOR_INDEX} not queryable after {wait_seconds:.0f}s "
                    f"(status {info.get('status')!r})"
                )
            say(f"waiting for {VECTOR_INDEX} (status {info.get('status')!r})")
            time.sleep(poll_seconds)

    # ------------------------------------------------------------ Store protocol

    def insert(self, collection: str, doc: Mapping[str, Any]) -> str:
        if "_id" not in doc:
            raise ValueError("documents must carry an _id")
        try:
            self._coll(collection).insert_one(dict(doc))
        except DuplicateKeyError as e:
            raise DuplicateKey(f"{collection}: {doc['_id']}") from e
        return doc["_id"]

    def get(self, collection: str, _id: str) -> dict[str, Any] | None:
        return self._coll(collection).find_one({"_id": _id})

    def find(
        self,
        collection: str,
        filter: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        cursor = self._coll(collection).find(dict(filter or {}))
        if sort:
            cursor = cursor.sort([(p, ASCENDING if d >= 0 else DESCENDING) for p, d in sort])
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    def update(self, collection: str, _id: str, set_fields: Mapping[str, Any]) -> None:
        if not set_fields:
            if self.get(collection, _id) is None:
                raise KeyError(f"{collection}: {_id}")
            return
        result = self._coll(collection).update_one({"_id": _id}, {"$set": dict(set_fields)})
        if result.matched_count == 0:
            raise KeyError(f"{collection}: {_id}")

    def append_event(self, event: Mapping[str, Any]) -> bool:
        doc = dict(event)
        doc.setdefault("_id", event_id(doc["run_id"], doc["seq"]))
        try:
            self.insert("events", doc)
        except DuplicateKey:
            return False
        return True

    def next_seq(self, run_id: str) -> int:
        last = self.db["events"].find_one({"run_id": run_id}, sort=[("seq", DESCENDING)], projection={"seq": 1})
        return int(last["seq"]) + 1 if last else 0

    def pin_version(self, version_id: str) -> None:
        coll = self.db["harness_versions"]

        def _pin(session) -> None:
            if coll.count_documents({"_id": version_id}, limit=1, session=session) == 0:
                raise KeyError(f"harness_versions: {version_id}")
            # Unpin first so the partial unique index never sees two pinned versions.
            coll.update_many({"_id": {"$ne": version_id}, "pinned": True}, {"$set": {"pinned": False}}, session=session)
            coll.update_one({"_id": version_id}, {"$set": {"pinned": True}}, session=session)

        with self.client.start_session() as session:
            session.with_transaction(_pin)

    def pinned_version(self) -> dict[str, Any] | None:
        return self.db["harness_versions"].find_one({"pinned": True})

    def vector_search_lessons(
        self, vector: Sequence[float], k: int, filters: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        if k <= 0:
            return []
        try:
            return list(self.db["lessons"].aggregate(vector_search_pipeline(vector, k, filters)))
        except OperationFailure as e:
            raise RuntimeError(f"vector search on {VECTOR_INDEX} failed: {e}") from e


def _normalize_fields(fields: Any) -> list[tuple]:
    out = []
    for f in fields or []:
        out.append(tuple(sorted((k, v) for k, v in f.items())))
    return sorted(out)

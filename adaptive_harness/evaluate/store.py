"""Opening the record store from the evaluator process.

Uses the store lane's MongoDB store when it exposes a factory; otherwise a minimal
pymongo adapter with the few Store methods the evaluator needs. Reads MONGODB_URI and
MONGODB_DB from the environment (.env loaded by the CLI).
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Mapping, Sequence

from ..contracts.errors import DuplicateKey
from ..contracts.records import event_id

_FACTORIES = ("get_store", "open_store", "connect", "MongoStore")


def open_store() -> Any:
    try:
        lane = importlib.import_module("adaptive_harness.store")
    except ImportError:
        lane = None
    from_env = getattr(getattr(lane, "MongoStore", None), "from_env", None)
    if callable(from_env):
        return from_env()
    for name in _FACTORIES:
        factory = getattr(lane, name, None)
        if callable(factory):
            return factory()
    return MinimalMongoStore(os.environ["MONGODB_URI"], os.environ.get("MONGODB_DB", "adaptive_harness"))


class MinimalMongoStore:
    """The subset of contracts.interfaces.Store the evaluator uses, on pymongo."""

    def __init__(self, uri: str, db: str) -> None:
        from pymongo import MongoClient

        self._db = MongoClient(uri, tz_aware=True)[db]

    def insert(self, collection: str, doc: Mapping[str, Any]) -> str:
        from pymongo.errors import DuplicateKeyError

        try:
            self._db[collection].insert_one(dict(doc))
        except DuplicateKeyError as e:
            raise DuplicateKey(f"{collection}: {doc.get('_id')}") from e
        return doc["_id"]

    def get(self, collection: str, _id: str) -> dict[str, Any] | None:
        return self._db[collection].find_one({"_id": _id})

    def find(
        self,
        collection: str,
        filter: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        cursor = self._db[collection].find(dict(filter or {}))
        if sort:
            cursor = cursor.sort(list(sort))
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    def update(self, collection: str, _id: str, set_fields: Mapping[str, Any]) -> None:
        result = self._db[collection].update_one({"_id": _id}, {"$set": dict(set_fields)})
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
        last = self.find("events", {"run_id": run_id}, sort=[("seq", -1)], limit=1)
        return last[0]["seq"] + 1 if last else 0

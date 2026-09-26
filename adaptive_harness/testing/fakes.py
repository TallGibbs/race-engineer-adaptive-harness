"""In-memory fakes so every lane can test without network or credentials."""

from __future__ import annotations

import copy
import json
import math
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from ..contracts.common import canonical_json
from ..contracts.errors import DuplicateKey, ModelUnavailable
from ..contracts.protocol import ChatMessage, ModelResponse, ModelUsage, ToolResult
from ..contracts.records import COLLECTIONS, event_id

# ---------------------------------------------------------------- FakeStore

_MISSING = object()


def _get_path(doc: Mapping[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _match_value(value: Any, cond: Any) -> bool:
    if isinstance(cond, Mapping) and cond and all(str(k).startswith("$") for k in cond):
        for op, arg in cond.items():
            present = value is not _MISSING
            if op == "$exists":
                if present != bool(arg):
                    return False
            elif op == "$in":
                if not present or not _any_equal(value, arg):
                    return False
            elif op == "$nin":
                if present and _any_equal(value, arg):
                    return False
            elif op == "$ne":
                if present and _eq(value, arg):
                    return False
            elif op in ("$gt", "$gte", "$lt", "$lte"):
                if not present or value is None:
                    return False
                if op == "$gt" and not value > arg:
                    return False
                if op == "$gte" and not value >= arg:
                    return False
                if op == "$lt" and not value < arg:
                    return False
                if op == "$lte" and not value <= arg:
                    return False
            else:
                raise NotImplementedError(f"FakeStore does not support {op}")
        return True
    return value is not _MISSING and _eq(value, cond)


def _eq(value: Any, cond: Any) -> bool:
    # Like MongoDB, an array field matches when any element equals the condition.
    if isinstance(value, list) and not isinstance(cond, list):
        return cond in value
    return value == cond


def _any_equal(value: Any, options: Iterable[Any]) -> bool:
    return any(_eq(value, o) for o in options)


def matches(doc: Mapping[str, Any], filter: Mapping[str, Any] | None) -> bool:
    for path, cond in (filter or {}).items():
        if not _match_value(_get_path(doc, path), cond):
            return False
    return True


class FakeStore:
    """In-memory implementation of contracts.interfaces.Store."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {c: {} for c in COLLECTIONS}
        self.initialized = False

    def _coll(self, name: str) -> dict[str, dict[str, Any]]:
        if name not in self.collections:
            raise KeyError(f"unknown collection {name!r}")
        return self.collections[name]

    def init(self) -> None:
        self.initialized = True

    def insert(self, collection: str, doc: Mapping[str, Any]) -> str:
        coll = self._coll(collection)
        if "_id" not in doc:
            raise ValueError("documents must carry an _id")
        _id = doc["_id"]
        if _id in coll:
            raise DuplicateKey(f"{collection}: {_id}")
        if collection == "events":
            for e in coll.values():
                if e.get("run_id") == doc.get("run_id") and e.get("seq") == doc.get("seq"):
                    raise DuplicateKey(f"events: ({doc.get('run_id')}, {doc.get('seq')})")
        coll[_id] = copy.deepcopy(dict(doc))
        return _id

    def get(self, collection: str, _id: str) -> dict[str, Any] | None:
        doc = self._coll(collection).get(_id)
        return copy.deepcopy(doc) if doc is not None else None

    def find(
        self,
        collection: str,
        filter: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        docs = [copy.deepcopy(d) for d in self._coll(collection).values() if matches(d, filter)]
        for path, direction in reversed(list(sort or [])):
            docs.sort(key=lambda d: _sort_key(_get_path(d, path)), reverse=direction < 0)
        return docs[:limit] if limit else docs

    def update(self, collection: str, _id: str, set_fields: Mapping[str, Any]) -> None:
        coll = self._coll(collection)
        if _id not in coll:
            raise KeyError(f"{collection}: {_id}")
        for path, value in set_fields.items():
            _set_path(coll[_id], path, copy.deepcopy(value))

    def append_event(self, event: Mapping[str, Any]) -> bool:
        doc = dict(event)
        doc.setdefault("_id", event_id(doc["run_id"], doc["seq"]))
        try:
            self.insert("events", doc)
        except DuplicateKey:
            return False
        return True

    def next_seq(self, run_id: str) -> int:
        seqs = [e["seq"] for e in self.collections["events"].values() if e.get("run_id") == run_id]
        return max(seqs) + 1 if seqs else 0

    def pin_version(self, version_id: str) -> None:
        coll = self._coll("harness_versions")
        if version_id not in coll:
            raise KeyError(f"harness_versions: {version_id}")
        for _id, doc in coll.items():
            doc["pinned"] = _id == version_id

    def pinned_version(self) -> dict[str, Any] | None:
        pinned = self.find("harness_versions", {"pinned": True})
        return pinned[0] if pinned else None

    def vector_search_lessons(
        self, vector: Sequence[float], k: int, filters: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        if k <= 0:
            return []
        scored = []
        for doc in self.find("lessons", filters):
            emb = doc.get("embedding") or []
            if len(emb) != len(vector):
                continue
            doc["score"] = _cosine(vector, emb)
            scored.append(doc)
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored[:k]


def _sort_key(v: Any) -> tuple[int, Any]:
    if v is _MISSING or v is None:
        return (0, 0)
    return (1, v)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- FakeModel


class FakeModel:
    """Returns scripted responses in order (contracts.interfaces.ModelClient).

    Each script item is a dict or list (sent as JSON), a str (sent as text, parsed when it
    is JSON), a ModelResponse (returned as is), or an Exception (raised). When the script
    runs out, ModelUnavailable is raised. Every call is recorded in `calls`.
    """

    def __init__(
        self,
        script: Iterable[Any],
        assignment: Mapping[str, str] | None = None,
        default_model: str = "fake-model",
    ) -> None:
        self.script = list(script)
        self.assignment = dict(assignment or {})
        self.default_model = default_model
        self.calls: list[dict[str, Any]] = []

    def model_for(self, role: str) -> str:
        return self.assignment.get(role) or self.default_model

    def complete(
        self,
        role: str,
        system: str,
        messages: Sequence[ChatMessage | Mapping[str, str]],
        json_schema: Mapping[str, Any] | None = None,
    ) -> ModelResponse:
        msgs = [m.model_dump() if isinstance(m, ChatMessage) else dict(m) for m in messages]
        self.calls.append({"role": role, "system": system, "messages": msgs, "json_schema": json_schema})
        if not self.script:
            raise ModelUnavailable("FakeModel script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, ModelResponse):
            return item
        if isinstance(item, str):
            text = item
            try:
                parsed = json.loads(item)
            except ValueError:
                parsed = None
        else:
            text = json.dumps(item)
            parsed = item
        prompt_chars = len(system) + sum(len(m.get("content", "")) for m in msgs)
        return ModelResponse(
            text=text,
            parsed=parsed,
            usage=ModelUsage(input_tokens=prompt_chars // 4, output_tokens=len(text) // 4),
            stop_reason="end_turn",
            latency_ms=1,
            model=self.model_for(role),
        )


# ---------------------------------------------------------------- FakeTools


Canned = Any  # ToolResult | dict payload | Exception | callable(args, as_of) -> one of those


class FakeTool:
    """One canned tool (contracts.interfaces.Tool).

    `responses` maps a canonical-args key (see FakeTool.key) to a canned result; `default`
    answers anything else. A canned value may be a ToolResult, a payload (wrapped with a
    JSON text), an Exception (raised), or a callable(args, as_of) returning one of those.
    """

    def __init__(
        self,
        name: str,
        responses: Mapping[str, Canned] | None = None,
        default: Canned = None,
        description: str = "",
        input_schema: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description or f"Fake {name}"
        self.input_schema = dict(input_schema or {"type": "object"})
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[tuple[dict[str, Any], date]] = []

    @staticmethod
    def key(args: Mapping[str, Any]) -> str:
        return canonical_json(dict(args)).decode("utf-8")

    def call(self, args: Mapping[str, Any], as_of: date) -> ToolResult:
        self.calls.append((dict(args), as_of))
        canned = self.responses.get(self.key(args), self.default)
        if callable(canned) and not isinstance(canned, (ToolResult, BaseException)):
            canned = canned(dict(args), as_of)
        if isinstance(canned, BaseException):
            raise canned
        if isinstance(canned, ToolResult):
            return canned
        if canned is None:
            raise KeyError(f"FakeTool {self.name}: no canned result for {self.key(args)}")
        return ToolResult.build(text=json.dumps(canned, sort_keys=True, default=str), payload=canned)


class FakeTools(dict):
    """A name -> FakeTool registry. FakeTools(list_events={...}, track_status=FakeTool(...))."""

    def __init__(self, **tools: FakeTool | Mapping[str, Canned]) -> None:
        super().__init__()
        for name, spec in tools.items():
            self[name] = spec if isinstance(spec, FakeTool) else FakeTool(name, responses=spec)

    def add(self, tool: FakeTool) -> FakeTool:
        self[tool.name] = tool
        return tool

    def call(self, name: str, args: Mapping[str, Any], as_of: date) -> ToolResult:
        return self[name].call(args, as_of)

    @property
    def calls(self) -> list[tuple[str, dict[str, Any], date]]:
        return [(n, a, d) for n, t in self.items() for a, d in t.calls]


__all__ = ["FakeStore", "FakeModel", "FakeTool", "FakeTools", "matches"]

"""Interfaces every lane codes against. Real implementations and the fakes in
adaptive_harness.testing both satisfy them."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .protocol import ChatMessage, ModelResponse, ToolResult


@runtime_checkable
class Tool(Protocol):
    """A task tool (section E). Raises AsOfViolation, DataUnavailable, or FetchError."""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema of args

    def call(self, args: Mapping[str, Any], as_of: date) -> ToolResult: ...


@runtime_checkable
class ModelClient(Protocol):
    """Section F. Resolves role -> model through the config's model block (role override,
    else the default), records the model that answered in ModelResponse.model, retries
    twice on rate limits, server errors, and network errors, and raises ModelUnavailable
    on final failure. No sampling parameters and no token limits are passed."""

    def complete(
        self,
        role: str,
        system: str,
        messages: Sequence[ChatMessage | Mapping[str, str]],
        json_schema: Mapping[str, Any] | None = None,
    ) -> ModelResponse: ...


@runtime_checkable
class Store(Protocol):
    """The record store (section C). Documents are plain dicts keyed by `_id`
    (use Model.to_doc() to write and Model.model_validate(doc) to read).

    Filters support equality on dotted paths and the operators $in, $nin, $ne, $exists,
    $gt, $gte, $lt, $lte. `sort` is a list of (dotted path, 1 | -1).
    """

    def init(self) -> None:
        """Create collections and indexes (unique (run_id, seq) on events; Atlas Vector
        Search index "lessons_vector" on lessons.embedding with filter fields scope,
        status, snapshot). Idempotent."""

    def insert(self, collection: str, doc: Mapping[str, Any]) -> str:
        """Insert and return the _id. Raises DuplicateKey when the _id exists."""

    def get(self, collection: str, _id: str) -> dict[str, Any] | None: ...

    def find(
        self,
        collection: str,
        filter: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]: ...

    def update(self, collection: str, _id: str, set_fields: Mapping[str, Any]) -> None:
        """$set the given (possibly dotted) fields. Raises KeyError when _id is missing."""

    def append_event(self, event: Mapping[str, Any]) -> bool:
        """Insert an event. Idempotent: returns True when written, False when an event with
        the same (run_id, seq) already exists (a retry counts as already written)."""

    def next_seq(self, run_id: str) -> int:
        """1 + the highest seq recorded for the run, or 0 when it has none."""

    def pin_version(self, version_id: str) -> None:
        """Set pinned true on this harness version and false on every other."""

    def pinned_version(self) -> dict[str, Any] | None: ...

    def vector_search_lessons(
        self, vector: Sequence[float], k: int, filters: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Top-k lessons by similarity, filtered by equality on scope, status, snapshot.
        Each returned doc carries a float "score"."""

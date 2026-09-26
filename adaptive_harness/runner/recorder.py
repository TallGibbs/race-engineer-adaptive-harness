"""Event recording and budget accounting for one case run (thread safe)."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..contracts.common import canonical_sha256
from ..contracts.config import Budgets
from ..contracts.errors import BudgetExceeded
from ..contracts.interfaces import Store
from ..contracts.records import Event, Totals, event_id


def now() -> datetime:
    return datetime.now(timezone.utc)


def jsonable(obj: Any) -> Any:
    """Plain JSON types only, so content hashes the same before and after the store."""
    return json.loads(json.dumps(obj, default=str))


class Recorder:
    def __init__(self, store: Store, run_id: str, budgets: Budgets) -> None:
        self.store = store
        self.run_id = run_id
        self.budgets = budgets
        self.totals = Totals()
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._seq = store.next_seq(run_id)
        self.events: list[dict[str, Any]] = []  # local copy of everything written

    # ------------------------------------------------------------ events

    def emit(
        self,
        type: str,
        content: Any = None,
        *,
        stage_id: str | None = None,
        role: str | None = None,
        refs: list[str] | None = None,
        **extra: Any,
    ) -> str:
        content = jsonable(content)
        with self._lock:
            seq = self._seq
            self._seq += 1
            doc = Event(
                _id=event_id(self.run_id, seq),
                run_id=self.run_id,
                seq=seq,
                stage_id=stage_id,
                role=role,
                type=type,
                content=content,
                refs=list(refs or []),
                content_sha256=canonical_sha256(content),
                created_at=now(),
                **extra,
            ).to_doc()
            self.store.append_event(doc)
            self.events.append(doc)
            return doc["_id"]

    def find(self, **match: Any) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.events if all(e.get(k) == v for k, v in match.items())]

    # ------------------------------------------------------------ budgets

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def check_wall(self) -> None:
        if self.elapsed() >= self.budgets.max_wall_seconds:
            raise BudgetExceeded(f"max_wall_seconds {self.budgets.max_wall_seconds} reached")

    def reserve_model_call(self) -> None:
        with self._lock:
            self.check_wall()
            if self.totals.model_calls >= self.budgets.max_model_calls:
                raise BudgetExceeded(f"max_model_calls {self.budgets.max_model_calls} reached")
            self.totals.model_calls += 1

    def reserve_tool_call(self) -> None:
        with self._lock:
            self.check_wall()
            if self.totals.tool_calls >= self.budgets.max_tool_calls:
                raise BudgetExceeded(f"max_tool_calls {self.budgets.max_tool_calls} reached")
            self.totals.tool_calls += 1

    def add_usage(self, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> None:
        with self._lock:
            self.totals.input_tokens += input_tokens
            self.totals.output_tokens += output_tokens
            self.totals.cache_read_tokens += cache_read_tokens

    def final_totals(self) -> Totals:
        with self._lock:
            self.totals.wall_seconds = round(self.elapsed(), 3)
            return self.totals.model_copy()

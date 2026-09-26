"""Task tools for neutralization_brief (CONTRACT.md section E).

Four tools: list_events, race_control_messages, track_status, interval. Each returns a
ToolResult whose text is compact lines and whose payload is the structured rows; the
ToolResult carries the canonical SHA-256 of the payload.

Data comes from FastF1 through a small backend so tests can substitute fake sessions.
The FastF1 cache is FASTF1_CACHE_DIR; FASTF1_OFFLINE=1 switches FastF1 to offline mode.
"""

from __future__ import annotations

import functools
import math
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping

import pandas as pd
import requests

from adaptive_harness.contracts.errors import AsOfViolation, DataUnavailable, FetchError
from adaptive_harness.contracts.protocol import ToolResult

WILSON_Z_90 = 1.6448536269514722
HTTP_TIMEOUT = (30, 120)  # (connect, read) seconds
RETRY_PAUSE_SECONDS = 5.0


# ---------------------------------------------------------------- network safety


def _install_default_timeout() -> None:
    """Give every requests.Session request a timeout unless the caller set one."""
    original = requests.Session.request
    if getattr(original, "_harness_default_timeout", False):
        return

    @functools.wraps(original)
    def request(self: requests.Session, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = HTTP_TIMEOUT
        return original(self, method, url, *args, **kwargs)

    request._harness_default_timeout = True  # type: ignore[attr-defined]
    requests.Session.request = request  # type: ignore[method-assign]


_install_default_timeout()


# ---------------------------------------------------------------- FastF1 backend


class FastF1Backend:
    """Loads schedules and race sessions from FastF1, configured from the environment."""

    def __init__(self) -> None:
        self._configured = False

    def _configure(self) -> None:
        if self._configured:
            return
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover
            pass
        import fastf1

        cache_dir = os.environ.get("FASTF1_CACHE_DIR", "").strip()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            fastf1.Cache.enable_cache(cache_dir)
        if os.environ.get("FASTF1_OFFLINE", "0").strip() == "1":
            fastf1.Cache.offline_mode(True)
        self._configured = True

    def schedule(self, year: int) -> Any:
        self._configure()
        import fastf1

        return fastf1.get_event_schedule(year, include_testing=False)

    def race_session(self, year: int, round: int) -> Any:
        self._configure()
        import fastf1

        return fastf1.get_session(year, round, "R")


# ---------------------------------------------------------------- value helpers


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value: Any) -> str | None:
    return None if _is_missing(value) else str(value)


def _int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    return int(value)


def _datetime(value: Any) -> str | None:
    if _is_missing(value):
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _date(value: Any) -> date | None:
    if _is_missing(value):
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _session_time(value: Any) -> str | None:
    """Session time (a timedelta) as H:MM:SS.mmm."""
    if _is_missing(value):
        return None
    if hasattr(value, "to_pytimedelta"):
        value = value.to_pytimedelta()
    if not isinstance(value, timedelta):
        return str(value)
    total_ms = round(value.total_seconds() * 1000)
    sign = "-" if total_ms < 0 else ""
    total_ms = abs(total_ms)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{sign}{hours}:{minutes:02d}:{seconds:02d}.{ms:03d}"


def _cell(value: Any) -> str:
    return "-" if value is None else str(value)


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    return [dict(r) for r in frame]


def _arg_int(args: Mapping[str, Any], name: str) -> int:
    if name not in args:
        raise DataUnavailable(f"missing argument {name!r}")
    value = args[name]
    if isinstance(value, bool):
        raise DataUnavailable(f"argument {name!r} must be an integer")
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        raise DataUnavailable(f"argument {name!r} must be an integer") from None
    if isinstance(value, float) and value != as_int:
        raise DataUnavailable(f"argument {name!r} must be an integer")
    return as_int


def _fetch_failed(what: str, exc: BaseException | None = None) -> FetchError:
    # Only the exception type: FastF1 messages can carry local cache paths.
    detail = f" ({type(exc).__name__})" if exc is not None else ""
    return FetchError(f"could not load {what}{detail}")


# ---------------------------------------------------------------- tools


class _BaseTool:
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}

    def __init__(self, backend: Any, retry_pause: float = RETRY_PAUSE_SECONDS) -> None:
        self.backend = backend
        self.retry_pause = retry_pause

    def _schedule_rows(self, year: int) -> list[dict[str, Any]]:
        return self._load_with_retry(
            f"the {year} event schedule", lambda: _records(self.backend.schedule(year))
        )

    def _guard(self, year: int, round: int, as_of: date) -> dict[str, Any]:
        """Return the schedule row of the race, or raise AsOfViolation / DataUnavailable."""
        for row in self._schedule_rows(year):
            if _int(row.get("RoundNumber")) == round:
                event_date = _date(row.get("EventDate"))
                if event_date is None:
                    raise DataUnavailable(f"{year} round {round} has no scheduled date")
                if event_date >= as_of:
                    raise AsOfViolation(
                        f"{year} round {round} is dated {event_date.isoformat()}, "
                        f"on or after the as-of date {as_of.isoformat()}"
                    )
                return row
        raise DataUnavailable(f"no {year} event with round {round}")

    def _load_with_retry(self, what: str, attempt: Callable[[], Any]) -> Any:
        """Run attempt(); on failure pause and try once more, then raise FetchError."""
        last: BaseException | None = None
        for i in range(2):
            if i:
                time.sleep(self.retry_pause)
            try:
                return attempt()
            except (AsOfViolation, DataUnavailable):
                raise
            except FetchError as exc:
                last = exc
            except Exception as exc:
                last = _fetch_failed(what, exc)
        assert last is not None
        raise last


class ListEvents(_BaseTool):
    name = "list_events"
    description = (
        "The Formula 1 event schedule of one season, testing excluded. Returns one row per "
        "event: round, event_name, official_event_name, location, country, event_date "
        "(ISO date), event_format. Text lines: "
        "round | event_name | official_event_name | location | country | event_date | event_format."
    )
    input_schema = {
        "type": "object",
        "properties": {"year": {"type": "integer", "description": "Season year."}},
        "required": ["year"],
        "additionalProperties": False,
    }

    def call(self, args: Mapping[str, Any], as_of: date) -> ToolResult:
        year = _arg_int(args, "year")
        rows = self._schedule_rows(year)
        events = []
        for row in rows:
            event_date = _date(row.get("EventDate"))
            events.append(
                {
                    "round": _int(row.get("RoundNumber")),
                    "event_name": _text(row.get("EventName")),
                    "official_event_name": _text(row.get("OfficialEventName")),
                    "location": _text(row.get("Location")),
                    "country": _text(row.get("Country")),
                    "event_date": event_date.isoformat() if event_date else None,
                    "event_format": _text(row.get("EventFormat")),
                }
            )
        if not events:
            raise DataUnavailable(f"no events in the {year} schedule")
        payload = {"year": year, "events": events}
        lines = [f"{year} event schedule ({len(events)} events)"]
        lines += [
            " | ".join(
                _cell(e[k])
                for k in ("round", "event_name", "official_event_name", "location", "country",
                          "event_date", "event_format")
            )
            for e in events
        ]
        return ToolResult.build("\n".join(lines), payload)


class RaceControlMessages(_BaseTool):
    name = "race_control_messages"
    description = (
        "Every race control message of one Grand Prix race session, unfiltered, in recorded "
        "order. Returns rows with time, lap, category, message, flag, scope. Text lines: "
        "lap | category | message."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "year": {"type": "integer", "description": "Season year."},
            "round": {"type": "integer", "description": "Round number in that season's schedule."},
        },
        "required": ["year", "round"],
        "additionalProperties": False,
    }

    def call(self, args: Mapping[str, Any], as_of: date) -> ToolResult:
        year, round = _arg_int(args, "year"), _arg_int(args, "round")
        event = self._guard(year, round, as_of)
        what = f"race control messages for {year} round {round}"

        def attempt() -> list[dict[str, Any]]:
            session = self.backend.race_session(year, round)
            session.load(laps=False, telemetry=False, weather=False, messages=True)
            rows = _records(session.race_control_messages)
            if not rows:
                raise FetchError(f"{what}: the session returned no messages")
            return rows

        rows = self._load_with_retry(what, attempt)
        messages = [
            {
                "time": _datetime(r.get("Time")),
                "lap": _int(r.get("Lap")),
                "category": _text(r.get("Category")),
                "message": _text(r.get("Message")),
                "flag": _text(r.get("Flag")),
                "scope": _text(r.get("Scope")),
            }
            for r in rows
        ]
        payload = {
            "year": year,
            "round": round,
            "event_name": _text(event.get("EventName")),
            "messages": messages,
        }
        lines = [f"{year} round {round} {_cell(payload['event_name'])} race: "
                 f"{len(messages)} race control messages (lap | category | message)"]
        lines += [f"{_cell(m['lap'])} | {_cell(m['category'])} | {_cell(m['message'])}" for m in messages]
        return ToolResult.build("\n".join(lines), payload)


class TrackStatus(_BaseTool):
    name = "track_status"
    description = (
        "Every track status row of one Grand Prix race session, in recorded order. Returns "
        "rows with time (session time H:MM:SS.mmm), status (code as recorded), message. "
        "Text lines: time | status | message."
    )
    input_schema = RaceControlMessages.input_schema

    def call(self, args: Mapping[str, Any], as_of: date) -> ToolResult:
        year, round = _arg_int(args, "year"), _arg_int(args, "round")
        event = self._guard(year, round, as_of)
        what = f"track status for {year} round {round}"

        def attempt() -> list[dict[str, Any]]:
            session = self.backend.race_session(year, round)
            session.load(laps=True, telemetry=False, weather=False, messages=False)
            rows = _records(session.track_status)
            if not rows:
                raise FetchError(f"{what}: the session returned no track status")
            return rows

        rows = self._load_with_retry(what, attempt)
        statuses = [
            {
                "time": _session_time(r.get("Time")),
                "status": _text(r.get("Status")),
                "message": _text(r.get("Message")),
            }
            for r in rows
        ]
        payload = {
            "year": year,
            "round": round,
            "event_name": _text(event.get("EventName")),
            "track_status": statuses,
        }
        lines = [f"{year} round {round} {_cell(payload['event_name'])} race: "
                 f"{len(statuses)} track status rows (time | status | message)"]
        lines += [f"{_cell(s['time'])} | {_cell(s['status'])} | {_cell(s['message'])}" for s in statuses]
        return ToolResult.build("\n".join(lines), payload)


def wilson_interval(k: int, n: int, z: float = WILSON_Z_90) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials, unrounded."""
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


class Interval(_BaseTool):
    name = "interval"
    description = (
        "The proportion k / n and its Wilson score interval at 90 percent confidence "
        "(z = 1.6448536269514722), each rounded to 3 decimals. Returns k, n, value, "
        "interval [low, high], method wilson_90."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "k": {"type": "integer", "minimum": 0, "description": "Count of successes."},
            "n": {"type": "integer", "minimum": 0, "description": "Count of trials."},
        },
        "required": ["k", "n"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        pass

    def call(self, args: Mapping[str, Any], as_of: date) -> ToolResult:
        k, n = _arg_int(args, "k"), _arg_int(args, "n")
        if n == 0:
            raise DataUnavailable("n is 0: no interval for an empty sample")
        if n < 0 or k < 0 or k > n:
            raise DataUnavailable(f"need 0 <= k <= n, got k={k}, n={n}")
        low, high = wilson_interval(k, n)
        payload = {
            "k": k,
            "n": n,
            "value": round(k / n, 3),
            "interval": [round(low, 3), round(high, 3)],
            "method": "wilson_90",
        }
        text = (f"k={k} n={n} value={payload['value']:.3f} "
                f"interval=[{payload['interval'][0]:.3f}, {payload['interval'][1]:.3f}] method=wilson_90")
        return ToolResult.build(text, payload)


def build_tools(backend: Any | None = None, retry_pause: float = RETRY_PAUSE_SECONDS) -> dict[str, Any]:
    """The four tools by name. `backend` defaults to FastF1; tests pass a fake."""
    backend = backend or FastF1Backend()
    tools = [
        ListEvents(backend, retry_pause),
        RaceControlMessages(backend, retry_pause),
        TrackStatus(backend, retry_pause),
        Interval(),
    ]
    return {t.name: t for t in tools}


__all__ = [
    "FastF1Backend",
    "ListEvents",
    "RaceControlMessages",
    "TrackStatus",
    "Interval",
    "build_tools",
    "wilson_interval",
    "WILSON_Z_90",
    "HTTP_TIMEOUT",
]

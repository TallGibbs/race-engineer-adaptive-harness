"""S7 in-harness checks: small deterministic functions over the brief and the run's
recorded tool results. They are the harness's own gate, distinct from the evaluator,
and never see the answer key.

Each check returns (passed, reasons). `tool_results` is a list of the run's
tool_result event contents: {"tool", "args", "text", "payload", "sha256"}.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from pydantic import ValidationError

from ..contracts.outputs import OUTPUT_MODELS

Result = tuple[bool, list[str]]

_YEAR_KEYS = ("year", "Year", "season", "Season")
_ROUND_KEYS = ("round", "Round", "RoundNumber", "round_number")
_LOCATION_KEYS = ("location", "Location")
FLAGS = ("sc", "vsc", "red_flag")


def schema_check(brief: Any, case_type: str) -> Result:
    if not isinstance(brief, Mapping):
        return False, ["no output was produced at S6"]
    try:
        OUTPUT_MODELS[case_type].model_validate(brief)
    except ValidationError as e:
        return False, [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()]
    return True, []


# ---------------------------------------------------------------- helpers


def _first(d: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _walk(obj: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(obj, Mapping):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def event_locations(tool_results: list[Mapping[str, Any]]) -> dict[tuple[int, int], set[str]]:
    """(year, round) -> locations reported by any recorded tool result."""
    out: dict[tuple[int, int], set[str]] = {}
    for tr in tool_results:
        args = tr.get("args") or {}
        for d in _walk(tr.get("payload")):
            loc = _first(d, _LOCATION_KEYS)
            rnd = _as_int(_first(d, _ROUND_KEYS))
            year = _as_int(_first(d, _YEAR_KEYS)) or _as_int(_first(args, _YEAR_KEYS))
            if isinstance(loc, str) and rnd is not None and year is not None:
                out.setdefault((year, rnd), set()).add(loc)
    return out


def _results_for(tool_results: list[Mapping[str, Any]], tool: str, year: int, rnd: int) -> list[Mapping[str, Any]]:
    out = []
    for tr in tool_results:
        args = tr.get("args") or {}
        if tr.get("tool") == tool and _as_int(_first(args, _YEAR_KEYS)) == year and _as_int(_first(args, _ROUND_KEYS)) == rnd:
            out.append(tr)
    return out


Flags = dict[str, tuple[bool, int | None]]  # flag -> (value, first lap, or None when the source has no laps)


def _upper(v: Any) -> str:
    return str(v).strip().upper() if v is not None else ""


def flags_from_race_control(payload: Any) -> Flags | None:
    """sc, vsc, red_flag from race control message rows (meanings as in data/definitions.md):
    sc when the safety car is deployed or the race starts behind it; vsc when a virtual
    safety car is deployed; red_flag when a red flag is shown. First laps from the rows."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("messages"), list):
        return None
    laps: dict[str, list[int]] = {f: [] for f in FLAGS}
    hit = {f: False for f in FLAGS}
    for row in payload["messages"]:
        if not isinstance(row, Mapping):
            continue
        msg, flag = _upper(row.get("message")), _upper(row.get("flag"))
        found = []
        if "VIRTUAL SAFETY CAR DEPLOYED" in msg:
            found.append("vsc")
        elif "SAFETY CAR DEPLOYED" in msg or ("START" in msg and "BEHIND" in msg and "SAFETY CAR" in msg):
            found.append("sc")
        if flag == "RED" or msg.startswith("RED FLAG"):
            found.append("red_flag")
        for f in found:
            hit[f] = True
            lap = _as_int(row.get("lap"))
            if lap is not None:
                laps[f].append(lap)
    return {f: (hit[f], min(laps[f]) if laps[f] else None) for f in FLAGS}


# Track status codes as recorded: 4 safety car deployed, 5 red flag, 6 virtual safety car deployed.
_TRACK_CODES = {"4": "sc", "5": "red_flag", "6": "vsc"}
_TRACK_WORDS = {"SCDEPLOYED": "sc", "RED": "red_flag", "VSCDEPLOYED": "vsc"}


def flags_from_track_status(payload: Any) -> Flags | None:
    """sc, vsc, red_flag from track status rows (status code or its message). These rows
    carry session times, not laps, so no first lap is given."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("track_status"), list):
        return None
    hit = {f: False for f in FLAGS}
    for row in payload["track_status"]:
        if not isinstance(row, Mapping):
            continue
        f = _TRACK_CODES.get(str(row.get("status") or "").strip()) or _TRACK_WORDS.get(
            _upper(row.get("message")).replace(" ", ""))
        if f:
            hit[f] = True
    return {f: (hit[f], None) for f in FLAGS}


SOURCES = {"race_control_messages": flags_from_race_control, "track_status": flags_from_track_status}


def _races(brief: Mapping[str, Any], case_type: str) -> list[Mapping[str, Any]]:
    if case_type == "race_audit":
        race = dict(brief.get("race") or {})
        race.update({f: brief.get(f) for f in FLAGS})
        return [race]
    return list(brief.get("races") or [])


# ---------------------------------------------------------------- optional checks


def venue_match(brief: Mapping[str, Any], case_type: str, tool_results: list[Mapping[str, Any]]) -> Result:
    """Every counted race sits at the brief's venue, and the recorded tool results give the
    target event that same location."""
    if case_type != "venue_brief":
        return True, []
    reasons = []
    venue = brief.get("venue") or {}
    loc = venue.get("location")
    for r in brief.get("races") or []:
        if r.get("location") != loc:
            reasons.append(f"race {r.get('year')} round {r.get('round')} has location {r.get('location')!r}, venue is {loc!r}")
    target = venue.get("from_target") or {}
    key = (_as_int(target.get("year")), _as_int(target.get("round")))
    reported = event_locations(tool_results).get(key)  # type: ignore[arg-type]
    if not reported:
        reasons.append(f"no recorded tool result gives the location of the target {key[0]} round {key[1]}")
    elif loc not in reported:
        reasons.append(f"recorded tool results give the target location {sorted(reported)}, the brief says {loc!r}")
    return not reasons, reasons


def source_agreement(
    brief: Mapping[str, Any], case_type: str, tool_results: list[Mapping[str, Any]], tolerance_laps: int
) -> Result:
    """For each race, the flags derived from race_control_messages and from track_status
    agree (first laps within tolerance when both sources give laps), and the brief matches
    what they agree on."""
    reasons = []
    for r in _races(brief, case_type):
        year, rnd = _as_int(r.get("year")), _as_int(r.get("round"))
        label = f"{year} round {rnd}"
        per_source = {}
        for tool in ("race_control_messages", "track_status"):
            found = [SOURCES[tool](tr.get("payload")) for tr in _results_for(tool_results, tool, year, rnd)]  # type: ignore[arg-type]
            found = [f for f in found if f is not None]
            if not found:
                reasons.append(f"{label}: no comparable {tool} result recorded")
            else:
                per_source[tool] = found[-1]
        if len(per_source) < 2:
            continue
        a, b = per_source["race_control_messages"], per_source["track_status"]
        for f in FLAGS:
            (va, la), (vb, lb) = a[f], b[f]
            if va != vb:
                reasons.append(f"{label}: sources disagree on {f} ({va} vs {vb})")
            elif la is not None and lb is not None and abs(la - lb) > tolerance_laps:
                reasons.append(f"{label}: {f} first laps differ by more than {tolerance_laps} ({la} vs {lb})")
            elif r.get(f) != va:
                reasons.append(f"{label}: brief says {f}={r.get(f)}, both sources say {va}")
    return not reasons, reasons


def min_races_for_rate(brief: Mapping[str, Any], case_type: str, value: int) -> Result:
    """No rate is reported on fewer than `value` races."""
    if case_type != "venue_brief":
        return True, []
    n = _as_int((brief.get("counts") or {}).get("n_races")) or 0
    rates = brief.get("rates") or {}
    if n < value and any(rates.get(k) is not None for k in ("sc", "vsc", "any")):
        return False, [f"rates reported on {n} races, fewer than the minimum {value}"]
    return True, []

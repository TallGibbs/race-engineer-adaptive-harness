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


def _source_flags(payload: Any) -> dict[str, tuple[bool, int | None]] | None:
    """Flags a source reports for one race: flag -> (value, first lap or None)."""
    if not isinstance(payload, Mapping) or not all(isinstance(payload.get(f), bool) for f in FLAGS):
        return None
    out = {}
    for f in FLAGS:
        laps = payload.get(f"{f}_laps")
        first = min((_as_int(x) for x in laps if _as_int(x) is not None), default=None) if isinstance(laps, list) else None
        out[f] = (payload[f], first)
    return out


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
    """For each race, race_control_messages and track_status agree on each flag (first laps
    within tolerance when both give laps), and the brief matches what they agree on."""
    reasons = []
    for r in _races(brief, case_type):
        year, rnd = _as_int(r.get("year")), _as_int(r.get("round"))
        label = f"{year} round {rnd}"
        per_source = {}
        for tool in ("race_control_messages", "track_status"):
            found = [_source_flags(tr.get("payload")) for tr in _results_for(tool_results, tool, year, rnd)]  # type: ignore[arg-type]
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

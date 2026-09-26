"""SYNTHETIC tools for exercising the runner end to end before the tools lane merges
(`run --tools synthetic`). Every value is invented from the arguments by a fixed rule;
none of it describes real races, and none of it comes from the answer key. Built on the
contract's FakeTool so it behaves like any other tool.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import Any

from ..contracts.errors import AsOfViolation, DataUnavailable
from ..contracts.protocol import ToolResult
from ..testing import FakeTool, FakeTools

ROUNDS_PER_YEAR = 20
Z90 = 1.6448536269514722


def _bit(*parts: Any) -> int:
    return hashlib.sha256("|".join(map(str, parts)).encode()).digest()[0]


def _event_date(year: int, rnd: int) -> date:
    return date(year, 3, 1) + timedelta(days=10 * (rnd - 1))


def _events(args: dict, as_of: date) -> ToolResult:
    year = int(args["year"])
    if date(year, 1, 1) >= as_of:
        raise AsOfViolation(f"season {year} starts on or after the as-of date")
    rows = [
        {"round": r, "event_name": f"Synthetic Grand Prix {r}", "location": f"Synthville {r}",
         "date": _event_date(year, r).isoformat()}
        for r in range(1, ROUNDS_PER_YEAR + 1) if _event_date(year, r) < as_of
    ]
    text = "\n".join(f"{year} R{e['round']}: {e['event_name']}, {e['location']}, {e['date']}" for e in rows)
    return ToolResult.build(f"SYNTHETIC schedule {year}\n{text}", {"synthetic": True, "year": year, "events": rows})


def _race_check(args: dict, as_of: date) -> tuple[int, int]:
    year, rnd = int(args["year"]), int(args["round"])
    if not 1 <= rnd <= ROUNDS_PER_YEAR:
        raise DataUnavailable(f"no round {rnd} in {year}")
    if _event_date(year, rnd) >= as_of:
        raise AsOfViolation(f"{year} round {rnd} is on or after the as-of date")
    return year, rnd


def _flags(year: int, rnd: int) -> dict[str, Any]:
    b = _bit(year, rnd)
    sc, vsc, red = bool(b & 1), bool(b & 2), (b % 11 == 0)
    return {"sc": sc, "vsc": vsc, "red_flag": red,
            "sc_laps": [5 + b % 30] if sc else [], "vsc_laps": [9 + b % 25] if vsc else [],
            "red_flag_laps": [20] if red else []}


def _messages(args: dict, as_of: date) -> ToolResult:
    year, rnd = _race_check(args, as_of)
    f = _flags(year, rnd)
    msgs = [{"lap": 1, "category": "Flag", "message": "GREEN LIGHT - PIT EXIT OPEN", "flag": "GREEN", "scope": "Track"}]
    for lap in f["sc_laps"]:
        msgs.append({"lap": lap, "category": "SafetyCar", "message": "SAFETY CAR DEPLOYED", "flag": None, "scope": None})
    for lap in f["vsc_laps"]:
        msgs.append({"lap": lap, "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED", "flag": None,
                     "scope": None})
    for lap in f["red_flag_laps"]:
        msgs.append({"lap": lap, "category": "Flag", "message": "RED FLAG", "flag": "RED", "scope": "Track"})
    msgs.sort(key=lambda m: m["lap"])
    text = "\n".join(f"{m['lap']} | {m['category']} | {m['message']}" for m in msgs)
    return ToolResult.build(f"SYNTHETIC race control {year} R{rnd} (lap | category | message)\n{text}",
                            {"year": year, "round": rnd, "event_name": f"Synthetic Grand Prix {rnd}", "messages": msgs})


def _status(args: dict, as_of: date) -> ToolResult:
    year, rnd = _race_check(args, as_of)
    f = _flags(year, rnd)
    rows = [{"time": "0:00:00.000", "status": "1", "message": "AllClear"}]
    if f["sc"]:
        rows.append({"time": "0:30:00.000", "status": "4", "message": "SCDeployed"})
    if f["vsc"]:
        rows.append({"time": "0:40:00.000", "status": "6", "message": "VSCDeployed"})
    if f["red_flag"]:
        rows.append({"time": "0:50:00.000", "status": "5", "message": "Red"})
    text = "\n".join(f"{r['time']} | {r['status']} | {r['message']}" for r in rows)
    return ToolResult.build(f"SYNTHETIC track status {year} R{rnd} (time | status | message)\n{text}",
                            {"year": year, "round": rnd, "event_name": f"Synthetic Grand Prix {rnd}", "track_status": rows})


def _interval(args: dict, as_of: date) -> ToolResult:
    k, n = int(args["k"]), int(args["n"])
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("need 0 <= k <= n and n > 0")
    p = k / n
    denom = 1 + Z90 ** 2 / n
    centre = (p + Z90 ** 2 / (2 * n)) / denom
    half = Z90 * math.sqrt(p * (1 - p) / n + Z90 ** 2 / (4 * n * n)) / denom
    payload = {"k": k, "n": n, "value": round(p, 3), "interval": [round(centre - half, 3), round(centre + half, 3)],
               "method": "wilson_90"}
    return ToolResult.build(f"{k}/{n} = {payload['value']}, 90% Wilson {payload['interval']}", payload)


_YR = {"type": "object", "properties": {"year": {"type": "integer"}}, "required": ["year"]}
_RACE = {"type": "object", "properties": {"year": {"type": "integer"}, "round": {"type": "integer"}},
         "required": ["year", "round"]}
_KN = {"type": "object", "properties": {"k": {"type": "integer"}, "n": {"type": "integer"}}, "required": ["k", "n"]}


def synthetic_tools() -> FakeTools:
    tools = FakeTools()
    tools.add(FakeTool("list_events", default=_events, input_schema=_YR,
                       description="SYNTHETIC. The season schedule for a year: round, event name, location, date."))
    tools.add(FakeTool("race_control_messages", default=_messages, input_schema=_RACE,
                       description="SYNTHETIC. Race control messages for one race."))
    tools.add(FakeTool("track_status", default=_status, input_schema=_RACE,
                       description="SYNTHETIC. Track status summary for one race."))
    tools.add(FakeTool("interval", default=_interval, input_schema=_KN,
                       description="Rate k/n with a 90 percent Wilson score interval, rounded to 3 decimals."))
    return tools

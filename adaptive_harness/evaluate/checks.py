"""Checks E1 to E8 (CONTRACT.md section H, SPECIFICATION.md CTQs).

Pure functions: a run document, its events, one case of the key, and the budgets in, a
list of verdicts out. Nothing here touches the store.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from ..contracts.common import CHECK_IDS
from ..contracts.outputs import OUTPUT_MODELS

Z90 = 1.6448536269514722
TOLERANCE = 0.001
_EPS = 1e-9  # float slack on top of the 0.001 tolerance

CATEGORY: dict[str, str] = {
    "E1": "schema_invalid",
    "E2": "venue_mismatch",
    "E3": "coverage_mismatch",
    "E4": "indicator_mismatch",
    "E5": "arithmetic_mismatch",
    "E6": "disposition_wrong",
    "E7": "evidence_missing",
    "E8": "budget_exceeded",
}
APPLIES: dict[str, set[str]] = {
    "venue_brief": set(CHECK_IDS),
    "race_audit": {"E1", "E4", "E7", "E8"},
}
HARNESS_STATUS = "harness_error"


def wilson_90(k: int, n: int) -> tuple[float, float, float]:
    """(value, low, high) for k of n: k/n and the Wilson score interval at 90 percent,
    each rounded to 3 decimals."""
    p = k / n
    z2 = Z90 * Z90
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = Z90 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return round(p, 3), round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def _close(a: Any, b: float) -> bool:
    return isinstance(a, (int, float)) and not isinstance(a, bool) and abs(float(a) - b) <= TOLERANCE + _EPS


def _norm(s: Any) -> str:
    return " ".join(str(s).split()).casefold()


def _race_key(row: Mapping[str, Any]) -> tuple[int, int]:
    return int(row["year"]), int(row["round"])


# ---------------------------------------------------------------- individual checks
# Each returns True (PASS) or False (FAIL). An exception while checking is a FAIL.


def check_schema(output: Any, case_type: str) -> bool:
    if not isinstance(output, dict):
        return False
    try:
        OUTPUT_MODELS[case_type].model_validate(output)
    except ValidationError:
        return False
    return True


def check_venue(output: Mapping[str, Any], key_case: Mapping[str, Any]) -> bool:
    venue = _norm(key_case["venue_location"])
    if _norm(output["venue"]["location"]) != venue:
        return False
    return all(_norm(row["location"]) == venue for row in output["races"])


def check_coverage(output: Mapping[str, Any], key_case: Mapping[str, Any]) -> bool:
    got = [_race_key(r) for r in output["races"]]
    want = {_race_key(r) for r in key_case["races"]}
    return len(got) == len(set(got)) and set(got) == want


def check_indicators_brief(output: Mapping[str, Any], key_case: Mapping[str, Any]) -> bool:
    truth = {_race_key(r): r for r in key_case["races"]}
    for row in output["races"]:
        k = _race_key(row)
        if k not in truth:
            continue  # a race outside the key's set is a coverage defect (E3), not an indicator one
        for flag in ("sc", "vsc", "red_flag"):
            if row[flag] is not truth[k][flag]:
                return False
    return True


def check_indicators_audit(output: Mapping[str, Any], key_case: Mapping[str, Any]) -> bool:
    target = key_case["target"]
    if _race_key(output["race"]) != (int(target["year"]), int(target["round"])):
        return False
    return all(output[flag] is key_case[flag] for flag in ("sc", "vsc", "red_flag"))


def check_arithmetic(output: Mapping[str, Any]) -> bool:
    """Recompute counts, rates, and Wilson intervals from the brief's own race rows."""
    races = output["races"]
    n = len(races)
    n_sc = sum(1 for r in races if r["sc"] is True)
    n_vsc = sum(1 for r in races if r["vsc"] is True)
    n_any = sum(1 for r in races if r["sc"] is True or r["vsc"] is True)
    counts = output["counts"]
    if [counts["n_races"], counts["n_sc"], counts["n_vsc"], counts["n_any"]] != [n, n_sc, n_vsc, n_any]:
        return False
    rates = output["rates"]
    for name, k in (("sc", n_sc), ("vsc", n_vsc), ("any", n_any)):
        rate = rates[name]
        if n == 0:
            if rate is not None:
                return False
            continue
        if rate is None:
            return False
        value, low, high = wilson_90(k, n)
        interval = rate["interval"]
        if len(interval) != 2:
            return False
        if not (_close(rate["value"], value) and _close(interval[0], low) and _close(interval[1], high)):
            return False
    return True


def check_disposition(output: Mapping[str, Any], key_case: Mapping[str, Any]) -> bool:
    if output["thin_sample"] is not key_case["thin_sample"]:
        return False
    if key_case["counts"]["n_races"] == 0:
        return output["disposition"] == "HOLD" and all(output["rates"][r] is None for r in ("sc", "vsc", "any"))
    return output["disposition"] == "GO"


def tool_args(event: Mapping[str, Any], events: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The arguments of the tool call that produced a tool_result event: from the result's
    own content when it carries them, else from the tool_call event it refers to, else from
    the tool_call event recorded just before it."""
    content = event.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("args"), Mapping):
        return content["args"]
    candidates = [events.get(r) for r in event.get("refs") or []]
    seq = event.get("seq")
    if isinstance(seq, int):
        candidates.append(next((e for e in events.values() if e.get("seq") == seq - 1), None))
    for e in candidates:
        if e and e.get("type") == "tool_call" and isinstance(e.get("content"), Mapping):
            args = e["content"].get("args")
            if isinstance(args, Mapping):
                return args
    return None


def _cites_race(evidence: Any, year: int, rnd: int, run_id: str, events: Mapping[str, Mapping[str, Any]]) -> bool:
    if not isinstance(evidence, list) or not evidence:
        return False
    for eid in evidence:
        e = events.get(eid)
        if e is None or e.get("run_id") != run_id or e.get("type") != "tool_result":
            return False
        args = tool_args(e, events)
        if args is None:
            return False
        try:
            if (int(args["year"]), int(args["round"])) != (year, rnd):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


def check_evidence_brief(output: Mapping[str, Any], run_id: str, events: Mapping[str, Mapping[str, Any]]) -> bool:
    return all(
        _cites_race(row["evidence"], int(row["year"]), int(row["round"]), run_id, events) for row in output["races"]
    )


def check_evidence_audit(output: Mapping[str, Any], run_id: str, events: Mapping[str, Mapping[str, Any]]) -> bool:
    race = output["race"]
    return _cites_race(output["evidence"], int(race["year"]), int(race["round"]), run_id, events)


def check_budget(run: Mapping[str, Any], budgets: Mapping[str, Any]) -> bool:
    if run.get("status") == "budget_exceeded":
        return False
    totals = run.get("totals") or {}
    return (
        totals.get("model_calls", 0) <= budgets["max_model_calls"]
        and totals.get("tool_calls", 0) <= budgets["max_tool_calls"]
        and totals.get("wall_seconds", 0.0) <= budgets["max_wall_seconds"]
    )


# ---------------------------------------------------------------- scoring a run


def _safe(fn: Callable[[], bool]) -> bool:
    try:
        return bool(fn())
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return False


def score(
    run: Mapping[str, Any],
    events: Mapping[str, Mapping[str, Any]],
    key_case: Mapping[str, Any],
    budgets: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Verdicts E1..E8 for one run: [{id, result, category}]."""
    case_type = key_case["type"]
    applies = APPLIES[case_type]
    harness = run.get("status") == HARNESS_STATUS
    output = run.get("output")
    run_id = run["_id"]
    audit = case_type == "race_audit"
    tests: dict[str, Callable[[], bool]] = {
        "E1": lambda: check_schema(output, case_type),
        "E2": lambda: check_venue(output, key_case),
        "E3": lambda: check_coverage(output, key_case),
        "E4": lambda: (check_indicators_audit if audit else check_indicators_brief)(output, key_case),
        "E5": lambda: check_arithmetic(output),
        "E6": lambda: check_disposition(output, key_case),
        "E7": lambda: (check_evidence_audit if audit else check_evidence_brief)(output, run_id, events),
        "E8": lambda: check_budget(run, budgets),
    }
    verdicts = []
    for cid in CHECK_IDS:
        if cid not in applies:
            verdicts.append({"id": cid, "result": "NA", "category": None})
        elif harness:
            verdicts.append({"id": cid, "result": "HARNESS", "category": None})
        elif _safe(tests[cid]):
            verdicts.append({"id": cid, "result": "PASS", "category": None})
        else:
            verdicts.append({"id": cid, "result": "FAIL", "category": CATEGORY[cid]})
    return verdicts

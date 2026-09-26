"""Measure: confirm the measurement system and record the baseline.

Every baseline run is re-scored by the evaluator in a fresh process; the verdicts, the
evaluator hash, and the key hash must all match what was stored. A case whose run ends
HARNESS is rerun once. Records DPO by check and by case set, a Pareto table of defects by
check, and cost per run. With `repeat`, the development cases run once more under the
baseline version on M0 (arm repeat) and the checks that changed are recorded. Tollgate M:
the measurement is valid.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from ..contracts.common import CHECK_IDS
from .common import (
    case_sets,
    cases_in,
    dpo_table,
    evaluation_of,
    is_harness,
    load_experiment,
    record_phase,
    require_passed,
    run_arm,
    runs_of,
    score_missing,
    valid_scored,
)
from .ports import Deps

COST_FIELDS = ("model_calls", "tool_calls", "input_tokens", "output_tokens", "cache_read_tokens", "wall_seconds")


def _by_case(runs: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        out[r["case_id"]].append(r)
    return out


def _latest_is_harness(deps: Deps, group: Sequence[dict[str, Any]]) -> bool:
    last = group[-1]
    return is_harness(last, evaluation_of(deps, last["_id"]))


def _harness_streak(deps: Deps, group: Sequence[dict[str, Any]]) -> int:
    n = 0
    for r in reversed(group):
        if not is_harness(r, evaluation_of(deps, r["_id"])):
            break
        n += 1
    return n


def rerun_harness(deps: Deps, experiment_id: str) -> list[str]:
    """Rerun once each baseline case whose latest run ended HARNESS and was not rerun yet."""
    runs = runs_of(deps, experiment_id, "baseline")
    reruns = []
    for case_id, group in _by_case(runs).items():
        # A streak of n HARNESS runs has had n - 1 reruns.
        if 0 < _harness_streak(deps, group) <= deps.cycle.harness_reruns:
            last = group[-1]
            reruns += deps.runner.run_cases(
                experiment_id=experiment_id,
                arm="baseline",
                version_id=last["harness_version"],
                snapshot=last["memory_snapshot"],
                case_ids=[case_id],
            )
    return reruns


def pareto(by_check: dict[str, dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    rows = sorted(
        ((cid, v["defects"]) for cid, v in by_check.items() if v["defects"]),
        key=lambda t: (-t[1], CHECK_IDS.index(t[0])),
    )
    total = sum(d for _, d in rows)
    out, cum = [], 0
    for cid, d in rows:
        cum += d
        out.append({"check_id": cid, "name": names[cid], "defects": d,
                    "share": round(d / total, 4), "cumulative_share": round(cum / total, 4)})
    return out


def cost(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    per_run = []
    for r in runs:
        t = r.get("totals") or {}
        per_run.append({"run_id": r["_id"], "case_id": r["case_id"], **{f: t.get(f, 0) for f in COST_FIELDS}})
    n = len(per_run)
    mean = {f: round(sum(p[f] for p in per_run) / n, 3) if n else None for f in COST_FIELDS}
    return {"per_run": per_run, "mean": mean}


def repeat_comparison(deps: Deps, experiment_id: str, dev_cases: Sequence[str]) -> dict[str, Any]:
    """Run the development cases once more under the baseline version on M0 (arm repeat)
    and list the checks whose verdict changed between the two identical runs."""
    base = valid_scored(deps, runs_of(deps, experiment_id, "baseline"))
    version = base[0][0]["harness_version"] if base else "v1"
    run_arm(deps, experiment_id=experiment_id, arm="repeat", version_id=version, snapshot="M0", case_ids=dev_cases)
    repeat_runs = runs_of(deps, experiment_id, "repeat")
    score_missing(deps, repeat_runs)
    rep = valid_scored(deps, repeat_runs)
    last_base = {r["case_id"]: ev for r, ev in base}
    last_rep = {r["case_id"]: ev for r, ev in rep}
    changed = []
    compared = 0
    for case_id in dev_cases:
        a, b = last_base.get(case_id), last_rep.get(case_id)
        if a is None or b is None:
            continue
        compared += 1
        va = {c["id"]: c["result"] for c in a["checks"]}
        vb = {c["id"]: c["result"] for c in b["checks"]}
        for cid in CHECK_IDS:
            if va.get(cid) != vb.get(cid):
                changed.append({"case_id": case_id, "check_id": cid, "baseline": va.get(cid), "repeat": vb.get(cid)})
    return {
        "version": version,
        "snapshot": "M0",
        "cases_compared": compared,
        "changed_checks": changed,
        "checks_changed_by_id": {cid: sum(1 for c in changed if c["check_id"] == cid) for cid in CHECK_IDS},
    }


def measure(deps: Deps, experiment_id: str, repeat: bool = False) -> dict[str, Any]:
    exp = load_experiment(deps, experiment_id)
    require_passed(exp, "define")
    sets = case_sets(exp)
    problems: list[str] = []

    runs = runs_of(deps, experiment_id, "baseline")
    score_missing(deps, runs)
    reruns = rerun_harness(deps, experiment_id)
    runs = runs_of(deps, experiment_id, "baseline")
    score_missing(deps, runs)

    for case_id, group in sorted(_by_case(runs).items()):
        if _latest_is_harness(deps, group):
            problems.append(f"case {case_id} ends HARNESS after {deps.cycle.harness_reruns} rerun(s)")

    # hashes: every baseline evaluation from the same evaluator and key
    evaluations = [e for e in (evaluation_of(deps, r["_id"]) for r in runs) if e is not None]
    if len(evaluations) < sum(1 for r in runs if r.get("status") is not None):
        problems.append("a finished baseline run has no evaluation")
    ev_hashes = sorted({e["evaluator_sha256"] for e in evaluations})
    key_hashes = sorted({e["key_sha256"] for e in evaluations})
    if len(ev_hashes) > 1:
        problems.append(f"baseline evaluations come from {len(ev_hashes)} evaluator versions")
    if len(key_hashes) > 1:
        problems.append(f"baseline evaluations use {len(key_hashes)} key hashes")

    # re-score every baseline run in a fresh evaluator process
    rescores = []
    for e in evaluations:
        res = deps.evaluator.rescore(e["run_id"])
        rescores.append({k: res.get(k) for k in ("run_id", "exit_code", "identical", "differing_checks",
                                                  "evaluator_sha256_match", "key_sha256_match")})
        rid = e["run_id"]
        if res.get("exit_code") == 3:
            problems.append(f"run {rid}: the evaluator refused to score (key hash mismatch)")
            continue
        if not res.get("identical"):
            problems.append(f"run {rid}: re-score differs on {', '.join(res.get('differing_checks') or []) or 'verdicts'}")
        if res.get("evaluator_sha256_match") is False:
            problems.append(f"run {rid}: evaluator hash differs from the stored evaluation")
        if res.get("key_sha256_match") is False:
            problems.append(f"run {rid}: key hash differs from the stored evaluation")

    scored = valid_scored(deps, runs)
    overall = dpo_table([ev for _, ev in scored])
    by_set = {}
    for s in ("development", "held_out", "control"):
        t = dpo_table([ev for r, ev in scored if sets.get(r["case_id"]) == s])
        by_set[s] = {k: t[k] for k in ("runs", "defects", "opportunities", "dpo")}
    names = {c.id: c.name for c in deps.cycle.ctqs}
    artifact: dict[str, Any] = {
        "hashes": {"evaluator_sha256": ev_hashes, "key_sha256": key_hashes},
        "rescore": rescores,
        "harness_reruns": reruns,
        "dpo": {k: overall[k] for k in ("runs", "defects", "opportunities", "dpo")},
        "dpo_by_check": overall["by_check"],
        "dpo_by_case_set": by_set,
        "pareto_by_check": pareto(overall["by_check"], names),
        "cost_per_run": cost([r for r, _ in scored]),
    }
    if repeat:
        artifact["repeat"] = repeat_comparison(deps, experiment_id, cases_in(exp, "development"))

    passed = not problems
    reasons = problems or [
        f"measurement valid: {len(rescores)} baseline run(s) re-scored identically, hashes confirmed, no HARNESS result remains"
    ]
    record_phase(deps, experiment_id, "measure", artifact, passed, reasons, decision=None if passed else "stopped")
    return {"phase": "measure", "passed": passed, "reasons": reasons, "artifact": artifact}

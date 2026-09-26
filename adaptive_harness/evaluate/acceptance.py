"""Acceptance rules R0 to R4 (configs/acceptance.json): the improve phase's tollgate.

The current arm (memory_only: v1 on M1) is compared with the candidate arm (candidate:
v2 on M1) over the same cases. Interpretation with repeated runs of a case:

- R0: in each (arm, case) group, ordered by start time, every HARNESS run is followed
  directly by a rerun that is not HARNESS, and the group has at least one valid run.
  The comparison must also be valid: same cases in both arms, the configured memory
  snapshot, one version per arm.
- Only valid (non-HARNESS) runs are scored.
- R1: per control case, every check that passes in every current run also passes in
  every candidate run.
- R2: worst candidate total (sum over development cases of the fewest checks passed)
  strictly beats best current total (sum of the most). With one run per case this is
  simply "strictly more".
- R3: worst candidate total on held-out cases is at least the current's worst total.
- R4: candidate wall-clock seconds, summed over its valid runs, are at most
  max_time_ratio times the current's.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..contracts.paths import load_acceptance
from ..contracts.records import Acceptance
from ..contracts.rules import AcceptanceRules
from .core import EvaluationError, Evaluator

HARNESS = "harness_error"


class AcceptanceError(EvaluationError):
    pass


def _case_sets(ev: Evaluator, experiment: dict[str, Any], case_ids: set[str]) -> dict[str, str]:
    sets = {c["id"]: c["set"] for c in experiment.get("cases") or []}
    missing = case_ids - set(sets)
    if missing:
        from examples.neutralization_brief.task import load_task

        for cid in missing:
            sets[cid] = load_task(cid).set
    return sets


def _passed(evaluation: dict[str, Any]) -> set[str]:
    return {c["id"] for c in evaluation["checks"] if c["result"] == "PASS"}


def decide(ev: Evaluator, experiment_id: str, rules: AcceptanceRules | None = None) -> dict[str, Any]:
    """Apply R0..R4, write the verdicts to the experiment, and set the candidate version's
    status to accepted or rejected with reasons."""
    rules = rules or load_acceptance()
    by_id = {r.id: r for r in rules.rules}
    experiment = ev.store.get("experiments", experiment_id)
    if experiment is None:
        raise AcceptanceError(f"no experiment {experiment_id!r}")
    arm_of = {"current": rules.compare["current"], "candidate": rules.compare["candidate"]}

    runs: dict[str, list[dict[str, Any]]] = {}
    for side, ref in arm_of.items():
        runs[side] = [r for r in ev.experiment_runs(experiment_id) if r.get("arm") == ref.arm]
    all_cases = {r["case_id"] for side in runs for r in runs[side]}
    case_set = _case_sets(ev, experiment, all_cases)

    # ---- R0: validity
    r0_problems: list[str] = []
    for side, ref in arm_of.items():
        if not runs[side]:
            r0_problems.append(f"no {ref.arm} runs")
        versions = {r.get("harness_version") for r in runs[side]}
        if len(versions) > 1:
            r0_problems.append(f"{ref.arm} runs span versions {sorted(map(str, versions))}")
        wrong_snap = sorted(r["_id"] for r in runs[side] if r.get("memory_snapshot") != ref.snapshot)
        if wrong_snap:
            r0_problems.append(f"{ref.arm} runs not on {ref.snapshot}: {wrong_snap}")
        unfinished = sorted(r["_id"] for r in runs[side] if r.get("status") is None)
        if unfinished:
            r0_problems.append(f"{ref.arm} runs not finished: {unfinished}")
    cur_cases = {r["case_id"] for r in runs["current"]}
    cand_cases = {r["case_id"] for r in runs["candidate"]}
    if cur_cases != cand_cases:
        r0_problems.append(f"arms cover different cases: current {sorted(cur_cases)}, candidate {sorted(cand_cases)}")
    max_reruns = by_id["R0"].max_reruns if by_id["R0"].max_reruns is not None else 1
    for side, ref in arm_of.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in runs[side]:
            groups[r["case_id"]].append(r)
        for cid, group in sorted(groups.items()):
            group.sort(key=lambda r: r["started_at"])
            streak = 0
            for r in group:
                streak = streak + 1 if r.get("status") == HARNESS else 0
                if streak > max_reruns:
                    break
            if streak > max_reruns or streak and group[-1].get("status") == HARNESS:
                r0_problems.append(f"{ref.arm} case {cid} ends HARNESS after {max_reruns} rerun(s)")
            elif not any(r.get("status") not in (HARNESS, None) for r in group):
                r0_problems.append(f"{ref.arm} case {cid} has no valid run")

    # ---- scoring valid runs
    passed: dict[str, dict[str, list[set[str]]]] = {"current": defaultdict(list), "candidate": defaultdict(list)}
    wall: dict[str, float] = {"current": 0.0, "candidate": 0.0}
    for side in runs:
        for r in runs[side]:
            if r.get("status") in (HARNESS, None):
                continue
            passed[side][r["case_id"]].append(_passed(ev.evaluation_for(r["_id"])))
            wall[side] += float((r.get("totals") or {}).get("wall_seconds", 0.0))

    def cases_in(s: str) -> list[str]:
        return sorted(c for c in cur_cases & cand_cases if case_set.get(c) == s)

    def total(side: str, cases: list[str], pick) -> int | None:
        if any(not passed[side][c] for c in cases):
            return None
        return sum(pick(len(p) for p in passed[side][c]) for c in cases)

    # ---- R1: no regression on control cases
    r1_problems = []
    control_cases = cases_in(by_id["R1"].case_set or "control")
    for c in control_cases:
        cur, cand = passed["current"][c], passed["candidate"][c]
        if not cur or not cand:
            r1_problems.append(f"case {c}: no valid run to compare")
            continue
        kept = set.intersection(*cur)
        lost = sorted(kept - set.intersection(*cand))
        if lost:
            r1_problems.append(f"case {c}: {', '.join(lost)} passed under the current version but not the candidate")
    r1 = (not r1_problems, "; ".join(r1_problems) or f"no regression on {len(control_cases)} control case(s)")

    # ---- R2: improvement on development cases
    dev = cases_in(by_id["R2"].case_set or "development")
    cand_worst, cur_best = total("candidate", dev, min), total("current", dev, max)
    if not dev:
        r2 = (False, "no development cases in both arms")
    elif cand_worst is None or cur_best is None:
        r2 = (False, "a development case has no valid run in one arm")
    else:
        r2 = (
            cand_worst > cur_best,
            f"development checks passed: candidate worst {cand_worst}, current best {cur_best}",
        )

    # ---- R3: held-out
    held = cases_in(by_id["R3"].case_set or "held_out")
    cand_h, cur_h = total("candidate", held, min), total("current", held, min)
    if not held:
        r3 = (True, "no held-out cases")
    elif cand_h is None or cur_h is None:
        r3 = (False, "a held-out case has no valid run in one arm")
    else:
        r3 = (cand_h >= cur_h, f"held-out checks passed: candidate {cand_h}, current {cur_h}")

    # ---- R4: time
    ratio = by_id["R4"].max_time_ratio or 1.5
    r4 = (
        wall["candidate"] <= ratio * wall["current"],
        f"wall-clock seconds: candidate {wall['candidate']:.1f}, current {wall['current']:.1f}, limit {ratio}x",
    )

    r0 = (not r0_problems, "; ".join(r0_problems) or "every run valid after at most one rerun")
    outcome = {rid: {"holds": bool(v[0]), "detail": v[1]} for rid, v in (("R0", r0), ("R1", r1), ("R2", r2), ("R3", r3), ("R4", r4))}
    acceptance = Acceptance.model_validate(outcome).model_dump()
    accepted = all(v["holds"] for v in outcome.values())
    decision = "accepted" if accepted else "rejected"
    reasons = (
        ["all acceptance rules hold"]
        if accepted
        else [f"{rid} {by_id[rid].name}: {v['detail']}" for rid, v in outcome.items() if not v["holds"]]
    )

    candidate_version = experiment.get("candidate_version")
    if not candidate_version:
        versions = {r.get("harness_version") for r in runs["candidate"]}
        candidate_version = versions.pop() if len(versions) == 1 else None
    updates: dict[str, Any] = {"acceptance": acceptance, "decision": decision}
    if candidate_version and not experiment.get("candidate_version"):
        updates["candidate_version"] = candidate_version
    ev.store.update("experiments", experiment_id, updates)
    if candidate_version and ev.store.get("harness_versions", candidate_version) is not None:
        ev.store.update(
            "harness_versions", candidate_version, {"status": decision, "decision_reasons": reasons}
        )
    return {
        "experiment_id": experiment_id,
        "candidate_version": candidate_version,
        "decision": decision,
        "acceptance": acceptance,
        "reasons": reasons,
    }

"""Gather one experiment's records from the store into plain dicts for the report and maps.

Reads only the store (runs, events, evaluations, harness_versions, lessons, experiments)
and the fixed configs. Never reads data/key.json.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..contracts.common import CHECK_IDS
from ..contracts.interfaces import Store

# Arms in the order the cycle runs them (run chart order).
ARM_ORDER = ("baseline", "repeat", "memory_only", "candidate", "confirmation")
SET_ORDER = ("development", "held_out", "control")
HARNESS_STATUS = "harness_error"


@dataclass
class ArmData:
    arm: str
    snapshot: str | None
    versions: list[str]
    runs: dict[str, dict[str, Any]]  # case_id -> the run scored for that case (latest)
    all_runs: list[dict[str, Any]]  # every run of the arm, including HARNESS reruns
    evaluations: dict[str, dict[str, Any]]  # case_id -> evaluation of runs[case_id]
    usage_by_model: dict[str, dict[str, int]]  # model -> {model_calls, input, output, cache_read}
    first_started: Any = None

    @property
    def totals(self) -> dict[str, float]:
        t: dict[str, float] = defaultdict(float)
        for r in self.runs.values():
            for k, v in (r.get("totals") or {}).items():
                t[k] += v
        return dict(t)


@dataclass
class ExperimentData:
    experiment: dict[str, Any]
    case_sets: dict[str, str]  # case_id -> set, in experiment order
    arms: dict[str, ArmData]  # in ARM_ORDER, only arms with runs
    versions: dict[str, dict[str, Any]]
    lessons: list[dict[str, Any]]
    events: dict[str, dict[str, Any]] = field(default_factory=dict)  # cited events by id
    # run_id -> error events of every run that ended HARNESS (the measurement-system cause)
    harness_errors: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def cases(self) -> list[str]:
        order = {s: i for i, s in enumerate(SET_ORDER)}
        return sorted(self.case_sets, key=lambda c: (order.get(self.case_sets[c], 9), c))


def verdicts(evaluation: Mapping[str, Any] | None) -> dict[str, str]:
    """check_id -> PASS/FAIL/HARNESS/NA (missing checks as '-')."""
    got = {c["id"]: c["result"] for c in (evaluation or {}).get("checks", [])}
    return {cid: got.get(cid, "-") for cid in CHECK_IDS}


def dpo_counts(evaluations: list[Mapping[str, Any]]) -> tuple[int, int]:
    """(defects, opportunities) summed over evaluations."""
    return (sum(e.get("defects", 0) for e in evaluations), sum(e.get("opportunities", 0) for e in evaluations))


def _latest(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return max(runs, key=lambda r: (r.get("started_at") is not None, r.get("started_at") or 0, r["_id"]))


def load(store: Store, experiment_id: str) -> ExperimentData:
    exp = store.get("experiments", experiment_id)
    if exp is None:
        raise KeyError(f"no experiment {experiment_id!r}")
    case_sets = {c["id"]: c["set"] for c in exp.get("cases", [])}

    runs = store.find("runs", {"experiment_id": experiment_id})
    evals = store.find("evaluations", {"experiment_id": experiment_id})
    evals_by_run: dict[str, dict[str, Any]] = {}
    for e in sorted(evals, key=lambda e: str(e.get("created_at"))):
        evals_by_run[e["run_id"]] = e  # latest evaluation of a run wins

    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_arm[r["arm"]].append(r)

    arms: dict[str, ArmData] = {}
    for arm in ARM_ORDER:
        arm_runs = by_arm.get(arm)
        if not arm_runs:
            continue
        per_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in arm_runs:
            per_case[r["case_id"]].append(r)
        scored = {c: _latest(rs) for c, rs in per_case.items()}
        usage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in scored.values():
            for ev in store.find("events", {"run_id": r["_id"], "type": "model_call"}):
                u = ev.get("usage") or {}
                m = usage[ev.get("model") or "unknown"]
                m["model_calls"] += 1
                m["input_tokens"] += u.get("input_tokens", 0)
                m["output_tokens"] += u.get("output_tokens", 0)
                m["cache_read_tokens"] += u.get("cache_read_tokens", 0)
        arms[arm] = ArmData(
            arm=arm,
            snapshot=_single({r.get("memory_snapshot") for r in scored.values()}),
            versions=sorted({r["harness_version"] for r in scored.values()}),
            runs=scored,
            all_runs=sorted(arm_runs, key=lambda r: str(r.get("started_at"))),
            evaluations={c: evals_by_run[r["_id"]] for c, r in scored.items() if r["_id"] in evals_by_run},
            usage_by_model={m: dict(v) for m, v in sorted(usage.items())},
            first_started=min(str(r.get("started_at")) for r in arm_runs),
        )
        for c in scored:
            case_sets.setdefault(c, "unknown")

    version_ids = {v for a in arms.values() for v in a.versions}
    if exp.get("candidate_version"):
        version_ids.add(exp["candidate_version"])
    improve = ((exp.get("dmaic") or {}).get("improve") or {}).get("artifact") or {}
    version_ids.update(h for h in improve.get("history") or [] if isinstance(h, str))
    versions: dict[str, dict[str, Any]] = {}
    for vid in list(version_ids):
        v = store.get("harness_versions", vid)
        if v is not None:
            versions[vid] = v
            if v.get("parent_id"):
                parent = store.get("harness_versions", v["parent_id"])
                if parent is not None:
                    versions[parent["_id"]] = parent

    analyze = ((exp.get("dmaic") or {}).get("analyze") or {}).get("artifact") or {}
    lesson_ids = [rc["lesson_id"] for rc in analyze.get("root_causes", []) if rc.get("lesson_id")]
    lesson_ids += [p["lesson_id"] for p in analyze.get("provisional", []) if isinstance(p, dict) and p.get("lesson_id")]
    lessons = [l for l in (store.get("lessons", lid) for lid in dict.fromkeys(lesson_ids)) if l is not None]

    events: dict[str, dict[str, Any]] = {}
    for l in lessons:
        for eid in [l.get("origin_event_id"), *l.get("source_event_ids", [])]:
            if eid and eid not in events:
                ev = store.get("events", eid)
                if ev is not None:
                    events[eid] = ev

    harness_errors = {
        r["_id"]: store.find("events", {"run_id": r["_id"], "type": "error"}, sort=[("seq", 1)])
        for a in arms.values() for r in a.all_runs if r.get("status") == HARNESS_STATUS
    }

    return ExperimentData(experiment=exp, case_sets=case_sets, arms=arms, versions=versions,
                          lessons=lessons, events=events, harness_errors=harness_errors)


def _single(values: set[Any]) -> Any:
    values.discard(None)
    if not values:
        return None
    return next(iter(values)) if len(values) == 1 else "/".join(sorted(map(str, values)))


def executed_stages(store: Store, run_ids: list[str]) -> set[str]:
    """Stage ids that recorded at least one event in any of the runs."""
    seen: set[str] = set()
    for rid in run_ids:
        for ev in store.find("events", {"run_id": rid}):
            if ev.get("stage_id"):
                seen.add(ev["stage_id"])
    return seen

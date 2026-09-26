"""The whole cycle: define, measure, analyze, improve (proposal), the pilot arms, improve
--verify, and control, stopping at the first tollgate that does not pass. A stop is a
recorded outcome, not an error."""

from __future__ import annotations

from typing import Any

from ..store.records import pinned_version
from .analyze import analyze
from .common import PhaseError, sync_arms
from .control import control
from .define import define
from .improve import pilot, propose, verify
from .measure import measure
from .ports import Deps


def cycle(
    deps: Deps,
    experiment_id: str,
    *,
    repeat: bool = False,
    confirm: bool = False,
    from_version: str | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def done(stopped_at: str | None) -> dict[str, Any]:
        sync_arms(deps, experiment_id)
        return {"experiment_id": experiment_id, "stopped_at": stopped_at, "steps": steps}

    steps.append(_brief(define(deps, experiment_id)))
    if not steps[-1]["passed"]:
        return done("define")
    steps.append(_brief(measure(deps, experiment_id, repeat=repeat)))
    if not steps[-1]["passed"]:
        return done("measure")
    snapshot = deps.cycle.lesson_snapshot_after_analyze
    steps.append(_brief(analyze(deps, experiment_id, snapshot)))
    if not steps[-1]["passed"]:
        return done("analyze")

    if from_version is None:
        pinned = pinned_version(deps.store)
        if pinned is None:
            raise PhaseError("no pinned harness version (run init-db)")
        from_version = pinned.id
    proposal = propose(deps, experiment_id, from_version, deps.acceptance.compare["candidate"].snapshot)
    steps.append(_brief(proposal))
    if not proposal["valid"]:
        return done("improve")
    runs = pilot(deps, experiment_id)
    steps.append({"phase": "pilot", "passed": True, "reasons": [f"{arm}: {len(ids)} run(s)" for arm, ids in runs.items()]})
    steps.append(_brief(verify(deps, experiment_id)))
    if not steps[-1]["passed"]:
        return done("improve")
    steps.append(_brief(control(deps, experiment_id, confirm=confirm)))
    if not steps[-1]["passed"]:
        return done("control")
    return done(None)


def _brief(result: dict[str, Any]) -> dict[str, Any]:
    out = {"phase": result["phase"], "passed": result.get("passed"), "reasons": result.get("reasons", [])}
    if "stage" in result:
        out["stage"] = result["stage"]
    return out

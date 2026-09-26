"""Scoring runs against the key, storing evaluations, re-scoring, and exporting defects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..contracts.interfaces import Store
from ..contracts.paths import load_config
from ..contracts.records import Evaluation, HarnessVersion
from . import checks
from .keyfile import Key, evaluator_sha256


class EvaluationError(Exception):
    """A run cannot be scored (missing, still running, or its case is not in the key)."""


def evaluation_id(run_id: str) -> str:
    return f"eval:{run_id}"


def now() -> datetime:
    return datetime.now(timezone.utc)


class Evaluator:
    def __init__(self, store: Store, key: Key, source_sha256: str | None = None) -> None:
        self.store = store
        self.key = key
        self.evaluator_sha256 = source_sha256 or evaluator_sha256()

    # ------------------------------------------------------------ reading

    def run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get("runs", run_id)
        if run is None:
            raise EvaluationError(f"no run {run_id!r}")
        if run.get("status") is None:
            raise EvaluationError(f"run {run_id!r} has not finished")
        return run

    def events(self, run_id: str) -> dict[str, dict[str, Any]]:
        return {e["_id"]: e for e in self.store.find("events", {"run_id": run_id})}

    def budgets(self, run: Mapping[str, Any]) -> dict[str, Any]:
        version = self.store.get("harness_versions", run.get("harness_version", ""))
        if version and isinstance(version.get("config"), Mapping) and "budgets" in version["config"]:
            return dict(version["config"]["budgets"])
        return load_config().budgets.model_dump()

    def stored(self, run_id: str) -> dict[str, Any] | None:
        found = self.store.find("evaluations", {"run_id": run_id}, sort=[("created_at", -1)], limit=1)
        return found[0] if found else None

    # ------------------------------------------------------------ scoring

    def score(self, run_id: str) -> Evaluation:
        """Score a run in this process without storing anything."""
        run = self.run(run_id)
        try:
            key_case = self.key.case(run["case_id"])
        except KeyError as e:
            raise EvaluationError(str(e)) from None
        verdicts = checks.score(run, self.events(run_id), key_case, self.budgets(run))
        return Evaluation(
            _id=evaluation_id(run_id),
            run_id=run_id,
            case_id=run["case_id"],
            arm=run["arm"],
            experiment_id=run.get("experiment_id"),
            checks=verdicts,
            opportunities=sum(1 for v in verdicts if v["result"] in ("PASS", "FAIL")),
            defects=sum(1 for v in verdicts if v["result"] == "FAIL"),
            evaluator_sha256=self.evaluator_sha256,
            key_sha256=self.key.sha256,
            created_at=now(),
        )

    def evaluate(self, run_id: str, force: bool = False, control: bool = True) -> tuple[dict[str, Any], bool]:
        """Score and store a run. Returns (evaluation doc, written). An existing evaluation is
        kept unless force is set. A run of a pinned version is then checked against its
        control plan."""
        existing = self.store.get("evaluations", evaluation_id(run_id))
        if existing is not None and not force:
            return existing, False
        doc = self.score(run_id).to_doc()
        if existing is None:
            self.store.insert("evaluations", doc)
        else:
            self.store.update("evaluations", doc["_id"], {k: v for k, v in doc.items() if k != "_id"})
        if control:
            from .control import apply_if_pinned

            apply_if_pinned(self, run_id)
        return doc, True

    def rescore(self, run_id: str) -> dict[str, Any]:
        """Score again in this process and compare every verdict with the stored evaluation."""
        stored = self.stored(run_id)
        if stored is None:
            raise EvaluationError(f"run {run_id!r} has no stored evaluation to compare with")
        fresh = self.score(run_id).to_doc()
        old = {c["id"]: (c["result"], c.get("category")) for c in stored["checks"]}
        new = {c["id"]: (c["result"], c.get("category")) for c in fresh["checks"]}
        differ = sorted(cid for cid in set(old) | set(new) if old.get(cid) != new.get(cid))
        return {
            "run_id": run_id,
            "identical": not differ,
            "differing_checks": differ,
            "stored": {cid: v[0] for cid, v in sorted(old.items())},
            "fresh": {cid: v[0] for cid, v in sorted(new.items())},
            "evaluator_sha256_match": stored.get("evaluator_sha256") == self.evaluator_sha256,
            "key_sha256_match": stored.get("key_sha256") == self.key.sha256,
        }

    def experiment_runs(self, experiment_id: str) -> list[dict[str, Any]]:
        return self.store.find("runs", {"experiment_id": experiment_id}, sort=[("started_at", 1)])

    def evaluation_for(self, run_id: str, control: bool = True) -> dict[str, Any]:
        """The stored evaluation of a run, scoring and storing it first when there is none."""
        return self.stored(run_id) or self.evaluate(run_id, control=control)[0]

    def version(self, version_id: str) -> HarnessVersion | None:
        doc = self.store.get("harness_versions", version_id)
        return HarnessVersion.model_validate(doc) if doc else None


# ---------------------------------------------------------------- export for the dmaic lane


def defect_summary(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Failure categories and defect counts for one run (CONTRACT.md section H).

    The only evaluator output a proposal may see: categories and counts, never key values."""
    failed = [c for c in evaluation["checks"] if c["result"] == "FAIL"]
    return {
        "run_id": evaluation["run_id"],
        "case_id": evaluation["case_id"],
        "arm": evaluation["arm"],
        "experiment_id": evaluation.get("experiment_id"),
        "opportunities": evaluation["opportunities"],
        "defects": evaluation["defects"],
        "harness": any(c["result"] == "HARNESS" for c in evaluation["checks"]),
        "failures": [{"check_id": c["id"], "category": c["category"]} for c in failed],
    }


def export(store: Store, experiment_id: str | None = None, run_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Defect summaries of stored evaluations, for an experiment or for given runs."""
    flt: dict[str, Any] = {}
    if experiment_id is not None:
        flt["experiment_id"] = experiment_id
    if run_ids is not None:
        flt["run_id"] = {"$in": list(run_ids)}
    return [defect_summary(e) for e in store.find("evaluations", flt, sort=[("run_id", 1)])]

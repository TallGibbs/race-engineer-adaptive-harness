"""Fakes and synthetic fixtures for the dmaic tests.

Every case id, run, event, and verdict here is invented. Nothing reads data/key.json.

- SynthEvaluator: the evaluate lane's Evaluator with scoring replaced by a verdict table,
  so its real re-score, acceptance (R0..R4), and control-check logic run in-process.
- FakeEvaluatorPort: the dmaic EvaluatorPort over a SynthEvaluator.
- FakeRunner: the dmaic RunnerPort; writes synthetic runs and events to the FakeStore and
  registers each run's verdicts with the evaluator from a scripted outcome function.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from adaptive_harness.contracts import CHECK_IDS, canonical_sha256, event_id, load_config
from adaptive_harness.contracts.records import Evaluation, Experiment, HarnessVersion
from adaptive_harness.dmaic.ports import Deps
from adaptive_harness.evaluate.acceptance import decide
from adaptive_harness.evaluate.core import Evaluator, evaluation_id
from adaptive_harness.store.records import save_experiment, save_version
from adaptive_harness.testing import FakeModel, FakeStore

T0 = datetime(2031, 1, 1, tzinfo=timezone.utc)
ASSIGN = {"race_engineer": "m-a", "statistician": "m-a", "data_engineer": "m-a", "improvement_agent": "m-b"}
CATEGORY = {
    "E1": "schema_invalid", "E2": "venue_mismatch", "E3": "coverage_mismatch", "E4": "indicator_mismatch",
    "E5": "arithmetic_mismatch", "E6": "disposition_wrong", "E7": "evidence_missing", "E8": "budget_exceeded",
}
CASES = [("dev1", "development"), ("dev2", "development"), ("held1", "held_out"), ("ctl1", "control")]
DEV, HELD, CTL = "dev1", "held1", "ctl1"


def verdicts(fail: Sequence[str] = (), harness: bool = False) -> dict[str, str]:
    if harness:
        return {cid: "HARNESS" for cid in CHECK_IDS}
    return {cid: ("FAIL" if cid in fail else "PASS") for cid in CHECK_IDS}


class SynthEvaluator(Evaluator):
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.key = SimpleNamespace(sha256="keysha")
        self.evaluator_sha256 = "evsha"
        self.verdicts: dict[str, dict[str, str]] = {}
        self.override: dict[str, dict[str, str]] = {}

    def score(self, run_id: str) -> Evaluation:
        run = self.run(run_id)
        table = {**self.verdicts[run_id], **self.override.get(run_id, {})}
        checks = [{"id": cid, "result": table[cid], "category": CATEGORY[cid] if table[cid] == "FAIL" else None}
                  for cid in CHECK_IDS]
        return Evaluation(
            _id=evaluation_id(run_id), run_id=run_id, case_id=run["case_id"], arm=run["arm"],
            experiment_id=run.get("experiment_id"), checks=checks,
            opportunities=sum(1 for c in checks if c["result"] in ("PASS", "FAIL")),
            defects=sum(1 for c in checks if c["result"] == "FAIL"),
            evaluator_sha256=self.evaluator_sha256, key_sha256=self.key.sha256, created_at=T0,
        )


class FakeEvaluatorPort:
    def __init__(self, ev: SynthEvaluator) -> None:
        self.ev = ev
        self.rescore_flip: dict[str, dict[str, str]] = {}  # run_id -> verdicts a fresh process would give
        self.calls: list[tuple[str, Any]] = []

    def score(self, run_ids):
        self.calls.append(("score", list(run_ids)))
        return [self.ev.evaluate(r)[0] for r in run_ids]

    def rescore(self, run_id):
        self.calls.append(("rescore", run_id))
        self.ev.override = dict(self.rescore_flip)
        try:
            res = self.ev.rescore(run_id)
        finally:
            self.ev.override = {}
        return {**res, "exit_code": 0 if res["identical"] else 4}

    def accept(self, experiment_id):
        self.calls.append(("accept", experiment_id))
        return decide(self.ev, experiment_id)


Outcome = Callable[[str, str, int], tuple[str, Sequence[str]]]  # (arm, case_id, attempt) -> (status, failed checks)


def all_pass(arm: str, case_id: str, attempt: int) -> tuple[str, Sequence[str]]:
    return "completed", ()


class FakeRunner:
    def __init__(self, store: FakeStore, ev: SynthEvaluator, outcome: Outcome = all_pass) -> None:
        self.store = store
        self.ev = ev
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.clock = 0

    def run_cases(self, *, experiment_id, arm, version_id, snapshot, case_ids):
        self.calls.append(dict(experiment_id=experiment_id, arm=arm, version_id=version_id, snapshot=snapshot,
                               case_ids=list(case_ids)))
        out = []
        for case_id in case_ids:
            attempt = len(self.store.find("runs", {"experiment_id": experiment_id, "arm": arm, "case_id": case_id}))
            run_id = f"{experiment_id}-{arm}-{case_id}-{attempt}"
            status, fails = self.outcome(arm, case_id, attempt)
            harness = status == "harness_error"
            self.clock += 1
            self.store.insert("runs", {
                "_id": run_id, "harness_version": version_id, "config_hash": "h", "memory_snapshot": snapshot,
                "case_id": case_id, "arm": arm, "experiment_id": experiment_id,
                "model": {"provider": "fake", "assignment": ASSIGN}, "fastf1_version": "9.9.9",
                "started_at": T0 + timedelta(minutes=self.clock), "ended_at": T0 + timedelta(minutes=self.clock, seconds=30),
                "status": status,
                "totals": {"model_calls": 8, "tool_calls": 4, "input_tokens": 1000, "output_tokens": 200,
                           "cache_read_tokens": 0, "wall_seconds": 30.0},
                "output": None,
            })
            for seq, (type_, content) in enumerate([
                ("model_call", {"text": f"frame {case_id}"}),
                ("tool_call", {"tool": "list_events", "args": {"year": 2030}}),
                ("tool_result", {"text": "synthetic result"}),
                ("message", {"summary": "brief written"}),
            ]):
                self.store.append_event({
                    "_id": event_id(run_id, seq), "run_id": run_id, "seq": seq, "stage_id": "S4",
                    "role": "data_engineer", "type": type_, "content": content, "refs": [],
                    "context_manifest": None, "usage": None, "model": None, "stop_reason": None,
                    "content_sha256": canonical_sha256(content), "created_at": T0,
                })
            self.ev.verdicts[run_id] = verdicts(fails, harness=harness)
            out.append(run_id)
        return out


def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    return [_vec(t) for t in texts]


def fake_embed_query(text: str) -> list[float]:
    return _vec(text)


def _vec(text: str) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return [1.0 + b / 255.0 for b in h[:8]]


class World:
    """A FakeStore with v1 pinned, experiment exp1, and the fakes wired into Deps."""

    def __init__(self, outcome: Outcome = all_pass, script: Sequence[Any] = (), cases=CASES) -> None:
        self.store = FakeStore()
        self.ev = SynthEvaluator(self.store)
        self.evaluator = FakeEvaluatorPort(self.ev)
        self.runner = FakeRunner(self.store, self.ev, outcome)
        self.model = FakeModel(list(script), assignment=ASSIGN)
        self.logs: list[str] = []
        cfg = load_config()
        save_version(self.store, HarnessVersion(
            _id="v1", parent_id=None, config=cfg.to_dict(), config_hash=cfg.config_hash(),
            status="baseline", pinned=True, created_at=T0,
        ))
        save_experiment(self.store, Experiment(
            _id="exp1", cases=[{"id": c, "set": s} for c, s in cases], arms=["baseline"], created_at=T0,
        ))
        self.deps = Deps(
            store=self.store, evaluator=self.evaluator, runner=self.runner, model_factory=lambda: self.model,
            embed=fake_embed, embed_query=fake_embed_query, embedding_model_name="fake-embed",
            now=lambda: T0, log=self.logs.append,
        )
        self.case_ids = [c for c, _ in cases]

    def baseline(self) -> list[str]:
        return self.runner.run_cases(experiment_id="exp1", arm="baseline", version_id="v1", snapshot="M0",
                                     case_ids=self.case_ids)

    def experiment(self) -> dict[str, Any]:
        return self.store.get("experiments", "exp1")

    def version(self, vid: str) -> dict[str, Any]:
        return self.store.get("harness_versions", vid)

    def prompts(self) -> str:
        return "\n".join(c["system"] + "\n" + m["content"] for c in self.model.calls for m in c["messages"])


def rid(arm: str, case_id: str, attempt: int = 0) -> str:
    return f"exp1-{arm}-{case_id}-{attempt}"


def eid(run_id: str, seq: int) -> str:
    return event_id(run_id, seq)


def root_cause(run_id: str, category: str = "method", origin: int = 1, sources: Sequence[int] = (1, 2),
               source_run: str | None = None) -> dict[str, Any]:
    src = source_run or run_id
    return {
        "cause_category": category,
        "why_chain": ["the output missed a check", "the stage ran without the needed input"],
        "origin_event_id": eid(src, origin),
        "source_event_ids": [eid(src, s) for s in sources],
        "text": "Confirm the case's inputs before gathering data.",
    }


PROBLEM = {"problem_statement": "One. Two. Three. Four."}

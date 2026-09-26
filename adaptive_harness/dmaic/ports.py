"""The small interfaces the DMAIC phases call, and their real implementations.

- `EvaluatorPort`: the fixed evaluator. The real one runs `python -m
  adaptive_harness.evaluate` as a separate process, so every verdict, re-score, and
  acceptance decision comes from the evaluator's own code, never from this lane.
- `RunnerPort`: runs cases under a harness version and memory snapshot for an arm. The
  real one runs `python -m adaptive_harness run` as a separate process.
- The improvement agent's model: a `contracts.interfaces.ModelClient`, always called
  with the `improvement_agent` role (CONTRACT.md section F).

Tests replace all three with fakes (tests/dmaic/fakes.py).
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence

from ..contracts.interfaces import ModelClient, Store
from ..contracts.paths import REPO_ROOT, load_acceptance, load_config, load_cycle
from ..contracts.rules import AcceptanceRules, CycleConfig

IMPROVEMENT_ROLE = "improvement_agent"


class PortError(Exception):
    """An external step (evaluator or runner process) failed; the phase cannot continue."""


# ---------------------------------------------------------------- evaluator


class EvaluatorPort(Protocol):
    def score(self, run_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Score and store runs that have no evaluation yet (a pinned version's runs are
        checked against its control plan). Returns defect summaries."""

    def rescore(self, run_id: str) -> dict[str, Any]:
        """Re-score a run in a fresh evaluator process and compare with the stored
        evaluation. Returns the evaluator's result plus `exit_code` (0 identical,
        4 differs, 3 key hash mismatch)."""

    def accept(self, experiment_id: str) -> dict[str, Any]:
        """Apply the acceptance rules R0..R4; the evaluator writes the verdicts."""


EXIT_OK, EXIT_ERROR, EXIT_KEY_HASH, EXIT_RESCORE_DIFFERS = 0, 2, 3, 4


class SubprocessEvaluator:
    """The evaluator as its own process: `python -m adaptive_harness.evaluate ...`."""

    def __init__(self, python: str | None = None, cwd: str | None = None, timeout: float = 1800.0) -> None:
        self.python = python or sys.executable
        self.cwd = cwd or str(REPO_ROOT)
        self.timeout = timeout

    def _call(self, args: list[str], ok: tuple[int, ...] = (EXIT_OK,)) -> tuple[int, Any, str]:
        proc = subprocess.run(
            [self.python, "-m", "adaptive_harness.evaluate", *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout,
        )
        payload: Any = None
        if proc.stdout.strip():
            try:
                payload = json.loads(proc.stdout)
            except ValueError:
                payload = None
        if proc.returncode not in ok:
            raise PortError(f"evaluator {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}")
        return proc.returncode, payload, proc.stderr

    def score(self, run_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        args: list[str] = []
        for r in run_ids:
            args += ["--run", r]
        return list(self._call(args)[1] or [])

    def rescore(self, run_id: str) -> dict[str, Any]:
        code, payload, stderr = self._call(
            ["--rescore", "--run", run_id], ok=(EXIT_OK, EXIT_RESCORE_DIFFERS, EXIT_KEY_HASH)
        )
        result = dict(payload or {})
        result.setdefault("run_id", run_id)
        result["exit_code"] = code
        if code == EXIT_KEY_HASH:
            result.update(identical=False, key_sha256_match=False, detail=stderr.strip())
        return result

    def accept(self, experiment_id: str) -> dict[str, Any]:
        return dict(self._call(["--accept", "--experiment", experiment_id])[1] or {})


# ---------------------------------------------------------------- runner


class RunnerPort(Protocol):
    def run_cases(
        self,
        *,
        experiment_id: str,
        arm: str,
        version_id: str,
        snapshot: str,
        case_ids: Sequence[str],
    ) -> list[str]:
        """Run each case once (fresh) and return the new run ids, in case order."""


class CliRunner:
    """The runner lane as its own process:

        python -m adaptive_harness run --experiment E --arm A --version V --snapshot S --case C [--case C ...]

    The new run ids are the runs of (experiment, arm) that did not exist before the call.
    """

    def __init__(self, store: Store, python: str | None = None, cwd: str | None = None, timeout: float = 7200.0) -> None:
        self.store = store
        self.python = python or sys.executable
        self.cwd = cwd or str(REPO_ROOT)
        self.timeout = timeout

    def run_cases(
        self,
        *,
        experiment_id: str,
        arm: str,
        version_id: str,
        snapshot: str,
        case_ids: Sequence[str],
    ) -> list[str]:
        if not case_ids:
            return []
        flt = {"experiment_id": experiment_id, "arm": arm}
        before = {r["_id"] for r in self.store.find("runs", flt)}
        args = ["--experiment", experiment_id, "--arm", arm, "--version", version_id, "--snapshot", snapshot]
        for c in case_ids:
            args += ["--case", c]
        proc = subprocess.run(
            [self.python, "-m", "adaptive_harness", "run", *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise PortError(f"runner exited {proc.returncode}: {proc.stderr.strip()}")
        new = [r for r in self.store.find("runs", flt, sort=[("started_at", 1)]) if r["_id"] not in before]
        order = {c: i for i, c in enumerate(case_ids)}
        new.sort(key=lambda r: order.get(r["case_id"], len(order)))
        return [r["_id"] for r in new]


# ---------------------------------------------------------------- model client

# Names the runner lane may expose for building its model client, tried in order.
_CLIENT_FACTORIES = ("build_model_client", "make_model_client", "model_client_from_env", "create_model_client")


def default_model_client() -> ModelClient:
    """The runner lane's model client (section F), built from the pinned configuration."""
    try:
        runner = importlib.import_module("adaptive_harness.runner")
    except ImportError as e:  # pragma: no cover - depends on the runner lane
        raise PortError(f"the runner lane's model client is not available: {e}") from None
    config = load_config()
    for name in _CLIENT_FACTORIES:
        factory = getattr(runner, name, None)
        if callable(factory):
            try:
                return factory(config)
            except TypeError:
                return factory()
    cls = getattr(runner, "ModelClient", None)
    if cls is not None:
        from_env = getattr(cls, "from_env", None)
        if callable(from_env):
            return from_env(config)
        return cls(config)
    raise PortError(
        "the runner lane exposes no model client factory "
        f"(looked for {', '.join(_CLIENT_FACTORIES)} or ModelClient in adaptive_harness.runner)"
    )


# ---------------------------------------------------------------- dependencies


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Deps:
    """Everything a phase needs. `model` is built on first use (only define, analyze,
    and improve call the improvement agent)."""

    store: Store
    evaluator: EvaluatorPort
    runner: RunnerPort
    model_factory: Callable[[], ModelClient] = default_model_client
    embed: Callable[[Sequence[str]], list[list[float]]] | None = None
    embed_query: Callable[[str], list[float]] | None = None
    cycle: CycleConfig = field(default_factory=load_cycle)
    acceptance: AcceptanceRules = field(default_factory=load_acceptance)
    now: Callable[[], datetime] = utcnow
    log: Callable[[str], None] = lambda msg: None
    _model: ModelClient | None = None

    @property
    def model(self) -> ModelClient:
        if self._model is None:
            self._model = self.model_factory()
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if self.embed is None:
            from ..store.embeddings import embed

            self.embed = embed
        return self.embed(texts)

    def embed_one_query(self, text: str) -> list[float]:
        if self.embed_query is None:
            from ..store.embeddings import embed_query

            self.embed_query = embed_query
        return self.embed_query(text)

    embedding_model_name: str | None = None  # set together with a custom `embed`

    def embedding_model(self) -> str:
        if self.embedding_model_name:
            return self.embedding_model_name
        from ..store.embeddings import EMBEDDING_MODEL

        return EMBEDDING_MODEL


def deps_from_env(log: Callable[[str], None] = print) -> Deps:
    """Real dependencies: MongoDB store, evaluator and runner processes, runner model client."""
    from dotenv import load_dotenv

    from ..store import MongoStore

    load_dotenv()
    store = MongoStore.from_env()
    return Deps(store=store, evaluator=SubprocessEvaluator(), runner=CliRunner(store), log=log)

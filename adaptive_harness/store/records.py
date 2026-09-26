"""Typed record helpers over any contracts.interfaces.Store (MongoStore or FakeStore).

Writes validate through the contract models (`Model(...).to_doc()`); reads return
models (`Model.model_validate(doc)`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..contracts.common import canonical_sha256
from ..contracts.errors import DuplicateKey
from ..contracts.interfaces import Store
from ..contracts.records import (
    Evaluation,
    Event,
    Experiment,
    HarnessVersion,
    Lesson,
    Phase,
    Run,
    Tollgate,
    Totals,
    event_id,
)
from .embeddings import EMBEDDING_DIMS, EMBEDDING_MODEL

PHASES: tuple[str, ...] = ("define", "measure", "analyze", "improve", "control")
LESSON_FILTER_FIELDS: tuple[str, ...] = ("scope", "status")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _model(cls, value):
    return value if isinstance(value, cls) else cls.model_validate(dict(value))


# ---------------------------------------------------------------- runs and events


def start_run(store: Store, run: Run | Mapping[str, Any]) -> str:
    """Record a new run (status None while it runs). Raises DuplicateKey if the id exists."""
    return store.insert("runs", _model(Run, run).to_doc())


def append_event(
    store: Store,
    run_id: str,
    type: str,
    content: Any = None,
    *,
    seq: int | None = None,
    stage_id: str | None = None,
    role: str | None = None,
    refs: Sequence[str] = (),
    context_manifest: Sequence[Mapping[str, Any]] | None = None,
    usage: Mapping[str, Any] | None = None,
    model: str | None = None,
    stop_reason: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Record one event and return its id ("<run_id>:<seq>").

    Pass `seq` explicitly to make a retried write idempotent: a duplicate (run_id, seq)
    counts as already written. Without `seq` the next free one is used.
    """
    if seq is None:
        seq = store.next_seq(run_id)
    event = Event(
        _id=event_id(run_id, seq),
        run_id=run_id,
        seq=seq,
        stage_id=stage_id,
        role=role,
        type=type,
        content=content,
        refs=list(refs),
        context_manifest=list(context_manifest) if context_manifest is not None else None,
        usage=dict(usage) if usage is not None else None,
        model=model,
        stop_reason=stop_reason,
        content_sha256=canonical_sha256(content),
        created_at=created_at or utcnow(),
    )
    return write_event(store, event)


def write_event(store: Store, event: Event | Mapping[str, Any]) -> str:
    """Record a prebuilt event idempotently and return its id."""
    ev = _model(Event, event)
    store.append_event(ev.to_doc())  # False means already written, which counts as written
    return ev.id


def finish_run(
    store: Store,
    run_id: str,
    status: str,
    *,
    output: Mapping[str, Any] | None = None,
    totals: Totals | Mapping[str, Any] | None = None,
    ended_at: datetime | None = None,
) -> Run:
    """Close a run with its status, output, and totals. Returns the stored run."""
    current = get_run(store, run_id)
    if current is None:
        raise KeyError(f"runs: {run_id}")
    fields: dict[str, Any] = {"status": status, "ended_at": ended_at or utcnow()}
    if output is not None:
        fields["output"] = dict(output)
    if totals is not None:
        fields["totals"] = _model(Totals, totals).model_dump()
    # Validate the result before writing so an invalid status never reaches the store.
    finished = Run.model_validate({**current.to_doc(), **fields})
    store.update("runs", run_id, fields)
    return finished


def get_run(store: Store, run_id: str) -> Run | None:
    doc = store.get("runs", run_id)
    return Run.model_validate(doc) if doc is not None else None


def events_for_run(store: Store, run_id: str) -> list[Event]:
    return [Event.model_validate(d) for d in store.find("events", {"run_id": run_id}, sort=[("seq", 1)])]


# ---------------------------------------------------------------- harness versions


def save_version(store: Store, version: HarnessVersion | Mapping[str, Any]) -> str:
    """Record a harness version. Saving one with pinned true pins it (and only it)."""
    v = _model(HarnessVersion, version)
    doc = v.to_doc()
    doc["pinned"] = False
    store.insert("harness_versions", doc)
    if v.pinned:
        store.pin_version(v.id)
    return v.id


def get_version(store: Store, version_id: str) -> HarnessVersion | None:
    doc = store.get("harness_versions", version_id)
    return HarnessVersion.model_validate(doc) if doc is not None else None


def update_version(store: Store, version_id: str, set_fields: Mapping[str, Any]) -> HarnessVersion:
    """$set fields on a version after validating the result. Use pin_version for pinned."""
    if "pinned" in set_fields:
        raise ValueError("use pin_version to change which version is pinned")
    current = get_version(store, version_id)
    if current is None:
        raise KeyError(f"harness_versions: {version_id}")
    updated = HarnessVersion.model_validate({**current.to_doc(), **dict(set_fields)})
    store.update("harness_versions", version_id, dict(set_fields))
    return updated


def pin_version(store: Store, version_id: str) -> None:
    """Pin this version and unpin every other (exactly one pinned at a time)."""
    store.pin_version(version_id)


def pinned_version(store: Store) -> HarnessVersion | None:
    doc = store.pinned_version()
    return HarnessVersion.model_validate(doc) if doc is not None else None


# ---------------------------------------------------------------- evaluations


def save_evaluation(store: Store, evaluation: Evaluation | Mapping[str, Any]) -> str:
    return store.insert("evaluations", _model(Evaluation, evaluation).to_doc())


def evaluations_for_run(store: Store, run_id: str) -> list[Evaluation]:
    docs = store.find("evaluations", {"run_id": run_id}, sort=[("created_at", 1)])
    return [Evaluation.model_validate(d) for d in docs]


# ---------------------------------------------------------------- lessons


def save_lesson(
    store: Store,
    lesson: Lesson | Mapping[str, Any],
    *,
    embed_fn: Callable[[Sequence[str]], list[list[float]]] | None = None,
) -> str:
    """Record a lesson, embedding its text when it has no embedding yet.

    The embedding model's name is stored in `embedding_model`; its dimensions are the
    length of `embedding` (checked here against the model's 384).
    """
    les = _model(Lesson, lesson)
    if not les.embedding:
        if embed_fn is None:
            from .embeddings import embed as embed_fn
        vector = embed_fn([les.text])[0]
        les = les.model_copy(update={"embedding": [float(x) for x in vector], "embedding_model": EMBEDDING_MODEL})
    if les.embedding_model is None:
        raise ValueError("a lesson with an embedding must name its embedding_model")
    if les.embedding_model == EMBEDDING_MODEL and len(les.embedding) != EMBEDDING_DIMS:
        raise ValueError(f"{EMBEDDING_MODEL} embeddings have {EMBEDDING_DIMS} dimensions, got {len(les.embedding)}")
    return store.insert("lessons", les.to_doc())


def get_lesson(store: Store, lesson_id: str) -> Lesson | None:
    doc = store.get("lessons", lesson_id)
    return Lesson.model_validate(doc) if doc is not None else None


def _lesson_filter(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, value in (filters or {}).items():
        if field not in LESSON_FILTER_FIELDS:
            raise ValueError(f"lesson filters are limited to {LESSON_FILTER_FIELDS}, not {field!r}")
        out[field] = value
    return out


def _hit(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "embedding"}


def search_lessons(
    store: Store,
    query_vector: Sequence[float],
    k: int,
    filters: Mapping[str, Any] | None,
    allowed_snapshots: Sequence[str],
) -> list[dict[str, Any]]:
    """Top-k lessons by vector similarity ($vectorSearch on MongoDB).

    `filters` holds equality on scope and status; `allowed_snapshots` limits which memory
    snapshots may be seen (a run on M0 passes ["M0"] and never sees M1 lessons). Each hit
    is the lesson document without its embedding, plus a float "score".
    """
    if k <= 0 or not allowed_snapshots:
        return []
    pre = _lesson_filter(filters)
    pre["snapshot"] = {"$in": list(allowed_snapshots)}
    return [_hit(d) for d in store.vector_search_lessons(list(query_vector), k, pre)]


def search_lessons_by_metadata(
    store: Store,
    filters: Mapping[str, Any] | None,
    k: int,
    allowed_snapshots: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Fallback retrieval without vectors: the k most recent lessons matching the filters."""
    if k <= 0:
        return []
    pre = _lesson_filter(filters)
    if allowed_snapshots is not None:
        if not allowed_snapshots:
            return []
        pre["snapshot"] = {"$in": list(allowed_snapshots)}
    docs = store.find("lessons", pre, sort=[("created_at", -1), ("_id", 1)], limit=k)
    return [_hit(d) for d in docs]


# ---------------------------------------------------------------- experiments


def save_experiment(store: Store, experiment: Experiment | Mapping[str, Any]) -> str:
    return store.insert("experiments", _model(Experiment, experiment).to_doc())


def get_experiment(store: Store, experiment_id: str) -> Experiment | None:
    doc = store.get("experiments", experiment_id)
    return Experiment.model_validate(doc) if doc is not None else None


def save_phase(
    store: Store,
    experiment_id: str,
    phase: str,
    artifact: Mapping[str, Any] | None,
    tollgate: Tollgate | Mapping[str, Any],
    *,
    status: str | None = None,
    completed_at: datetime | None = None,
) -> Phase:
    """Write one DMAIC phase's artifact and tollgate verdict into experiments.dmaic.<phase>.

    Status defaults to "passed" when the tollgate passed, else "stopped".
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, not {phase!r}")
    gate = _model(Tollgate, tollgate)
    record = Phase(
        status=status or ("passed" if gate.passed else "stopped"),
        artifact=dict(artifact) if artifact is not None else None,
        tollgate=gate,
        completed_at=completed_at or utcnow(),
    )
    current = get_experiment(store, experiment_id)
    if current is None:
        raise KeyError(f"experiments: {experiment_id}")
    store.update("experiments", experiment_id, {f"dmaic.{phase}": record.model_dump()})
    return record


def update_experiment(store: Store, experiment_id: str, set_fields: Mapping[str, Any]) -> Experiment:
    """$set top-level or dotted fields on an experiment after validating the result."""
    current = get_experiment(store, experiment_id)
    if current is None:
        raise KeyError(f"experiments: {experiment_id}")
    merged = current.to_doc()
    for path, value in set_fields.items():
        cur = merged
        parts = path.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
    updated = Experiment.model_validate(merged)
    store.update("experiments", experiment_id, dict(set_fields))
    return updated


__all__ = [
    "DuplicateKey",
    "PHASES",
    "append_event",
    "evaluations_for_run",
    "events_for_run",
    "finish_run",
    "get_experiment",
    "get_lesson",
    "get_run",
    "get_version",
    "pin_version",
    "pinned_version",
    "save_evaluation",
    "save_experiment",
    "save_lesson",
    "save_phase",
    "save_version",
    "search_lessons",
    "search_lessons_by_metadata",
    "start_run",
    "update_experiment",
    "update_version",
    "utcnow",
    "write_event",
]

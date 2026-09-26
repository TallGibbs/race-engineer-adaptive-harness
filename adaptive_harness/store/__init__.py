"""Lane store: the MongoDB Atlas record store. See CONTRACT.md section C.

    from adaptive_harness.store import MongoStore, start_run, append_event
    store = MongoStore.from_env()      # MONGODB_URI, MONGODB_DB (python-dotenv)

The helpers take any contracts.interfaces.Store, so tests pass a FakeStore.
"""

from .embeddings import EMBEDDING_DIMS, EMBEDDING_MODEL, embed, embed_query
from .mongo import VECTOR_INDEX, MongoStore, StoreConnectionError
from .records import (
    PHASES,
    append_event,
    evaluations_for_run,
    events_for_run,
    finish_run,
    get_experiment,
    get_lesson,
    get_run,
    get_version,
    pin_version,
    pinned_version,
    save_evaluation,
    save_experiment,
    save_lesson,
    save_phase,
    save_version,
    search_lessons,
    search_lessons_by_metadata,
    start_run,
    update_experiment,
    update_version,
    write_event,
)

__all__ = [
    "EMBEDDING_DIMS",
    "EMBEDDING_MODEL",
    "MongoStore",
    "PHASES",
    "StoreConnectionError",
    "VECTOR_INDEX",
    "append_event",
    "embed",
    "embed_query",
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
    "write_event",
]

"""Lesson embeddings with fastembed (BAAI/bge-small-en-v1.5, 384 dimensions).

The model is loaded once per process, on first use. Documents go through `embed`;
retrieval queries go through `embed_query`, which applies the model's query instruction.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMS = 384


@lru_cache(maxsize=1)
def _model() -> Any:
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=EMBEDDING_MODEL)


def _as_lists(vectors: Any) -> list[list[float]]:
    out = [[float(x) for x in v] for v in vectors]
    for v in out:
        if len(v) != EMBEDDING_DIMS:
            raise ValueError(f"{EMBEDDING_MODEL} returned {len(v)} dimensions, expected {EMBEDDING_DIMS}")
    return out


def embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed documents (lesson texts). Returns one 384-float list per text."""
    if isinstance(texts, str):
        raise TypeError("embed takes a sequence of texts, not a single string")
    if not texts:
        return []
    return _as_lists(_model().embed(list(texts)))


def embed_query(text: str) -> list[float]:
    """Embed one retrieval query."""
    return _as_lists(_model().query_embed(text))[0]

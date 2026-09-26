"""Context assembly from the configuration's context_policy (CONTRACT.md section A).

Each model call gets a system prompt made only of stable content (team, role charter,
stage purpose, reply protocol, then the stable policy items output_schema and tool_docs)
and a first user message of volatile content (task, prior stage outputs, lessons, open
objections), each in policy order. Every piece is listed in the call's context manifest.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contracts.common import sha256_hex
from ..contracts.config import HarnessConfig, StageDef
from ..contracts.interfaces import Store
from ..contracts.protocol import Task
from . import prompts

STABLE_ITEMS = ("output_schema", "tool_docs")

# (text, embedding model name) -> vector. Must use the same model the lessons were embedded with.
Embedder = Callable[[str, str], Sequence[float]]


def approx_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass
class Piece:
    item: str
    text: str
    id: str | None = None

    def manifest(self) -> dict[str, Any]:
        return {"item": self.item, "id": self.id, "sha256": sha256_hex(self.text), "approx_tokens": approx_tokens(self.text)}


@dataclass
class Context:
    system: str
    user: str
    pieces: list[Piece]
    refs: list[str] = field(default_factory=list)

    @property
    def manifest(self) -> list[dict[str, Any]]:
        return [p.manifest() for p in self.pieces]


@dataclass
class StageRecord:
    """What a finished stage left behind for later stages."""

    stage_id: str
    notes: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)  # (role, output, message event id)
    tool_results: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # (event id, tool, args)
    errors: list[str] = field(default_factory=list)


def fastembed_embedder() -> Embedder:
    cache: dict[str, Any] = {}

    def embed(text: str, model_name: str) -> Sequence[float]:
        try:  # the store lane's query embedding, when it matches the lessons' model
            from ..store import EMBEDDING_MODEL, embed_query

            if model_name == EMBEDDING_MODEL:
                return embed_query(text)
        except ImportError:
            pass
        if model_name not in cache:
            from fastembed import TextEmbedding

            cache[model_name] = TextEmbedding(model_name=model_name)
        return [float(x) for x in next(iter(cache[model_name].embed([text])))]

    return embed


class ContextBuilder:
    def __init__(
        self,
        config: HarnessConfig,
        task: Task,
        tools: Mapping[str, Any],
        store: Store,
        snapshot: str,
        records: dict[str, StageRecord],
        embedder: Embedder | None = None,
    ) -> None:
        self.config = config
        self.task = task
        self.tools = tools
        self.store = store
        self.snapshot = snapshot
        self.records = records
        self.embedder = embedder
        self._lessons: list[dict[str, Any]] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ public

    def stage_def(self, stage_id: str) -> StageDef:
        return self.config.stage_catalog.get(stage_id) or self.config.optional_stage_catalog[stage_id]

    def items_for(self, role: str, stage_id: str) -> list[str]:
        items = self.config.context_policy.roles.get(role, {}).get(stage_id)
        if items is not None:
            return list(items)
        default = ["task", "output_schema"]
        if self.stage_def(stage_id).tools:
            default.append("tool_docs")
        return default

    def build(self, role: str, stage_id: str) -> Context:
        sdef = self.stage_def(stage_id)
        purpose = prompts.STAGE_PURPOSE.get(sdef.name, sdef.name)
        protocol = prompts.TOOL_PROTOCOL if sdef.tools else prompts.FINISH_ONLY
        head = Piece("charter", "\n\n".join([
            prompts.TEAM,
            prompts.charter(role),
            f"Stage {stage_id} ({sdef.name}): {purpose}",
            protocol,
        ]), id=role)
        stable: list[Piece] = [head]
        volatile: list[Piece] = []
        for item in self.items_for(role, stage_id):
            piece = self._piece(item, stage_id, sdef)
            if piece is None:
                continue
            (stable if item in STABLE_ITEMS else volatile).append(piece)
        refs: list[str] = []
        for p in volatile:
            if p.item.startswith("prior:") and p.id:
                refs.extend(p.id.split(","))
        return Context(
            system="\n\n".join(p.text for p in stable),
            user="\n\n".join(p.text for p in volatile) or "Begin.",
            pieces=stable + volatile,
            refs=refs,
        )

    # ------------------------------------------------------------ items

    def _piece(self, item: str, stage_id: str, sdef: StageDef) -> Piece | None:
        if item == "task":
            return Piece("task", "Case:\n" + prompts.dumps(self.task.model_dump(mode="json")), id=self.task.id)
        if item == "output_schema":
            schema = prompts.output_schema(stage_id, self.task.type)
            return Piece("output_schema", "Output schema (for `output`):\n" + prompts.dumps(schema))
        if item == "tool_docs":
            if not sdef.tools:
                return None
            return Piece("tool_docs", prompts.tool_docs(dict(self.tools), sdef.tools))
        if item.startswith("prior:"):
            return self._prior(item, item[len("prior:"):])
        if item == "lessons":
            return self._lessons_piece()
        if item == "open_objections":
            return self._objections()
        return None

    def _prior(self, item: str, stage_id: str) -> Piece | None:
        if stage_id not in self.config.stages:
            return None
        rec = self.records.get(stage_id)
        if rec is None or (not rec.notes and not rec.tool_results):
            return Piece(item, f"Stage {stage_id}: no recorded output.")
        lines = [f"Stage {stage_id} output:"]
        ids: list[str] = []
        for role, output, ev in rec.notes:
            lines.append(f"[{role}, event {ev}] {prompts.dumps(output)}")
            ids.append(ev)
        if rec.tool_results:
            lines.append(f"Tool results recorded in stage {stage_id}:")
            for ev, tool, args in rec.tool_results:
                lines.append(f"- {ev}: {tool} {prompts.dumps(args)}")
        for err in rec.errors:
            lines.append(f"Error: {err}")
        return Piece(item, "\n".join(lines), id=",".join(ids) or None)

    def _objections(self) -> Piece:
        lines = []
        for stage_id in self.config.stages:
            rec = self.records.get(stage_id)
            if rec is None:
                continue
            for role, output, ev in rec.notes:
                for obj in output.get("objections") or output.get("open_objections") or []:
                    lines.append(f"- [{stage_id}, {role}, event {ev}] {obj}")
        text = "Open objections:\n" + ("\n".join(lines) if lines else "(none)")
        return Piece("open_objections", text)

    def _lessons_piece(self) -> Piece | None:
        lessons = self.lessons()
        if not lessons:
            return None
        lines = ["Lessons learned from earlier recorded runs:"]
        for l in lessons:
            lines.append(f"- [lesson {l['_id']}] {l.get('text', '')}")
        return Piece("lessons", "\n".join(lines), id=",".join(l["_id"] for l in lessons))

    def lessons(self) -> list[dict[str, Any]]:
        """The store's lesson search, limited to the run's memory snapshot. Retrieved once per run."""
        with self._lock:
            if self._lessons is None:
                self._lessons = self._search_lessons()
            return self._lessons

    def _search_lessons(self) -> list[dict[str, Any]]:
        settings = self.config.context_policy.lessons
        if settings.k <= 0:
            return []
        filters = {k: v for k, v in settings.filters.model_dump().items() if v is not None}
        filters["snapshot"] = self.snapshot
        probe = self.store.find("lessons", filters, limit=1)
        if not probe:
            return []  # nothing to retrieve (always the case on M0); skip embedding
        model_name = probe[0].get("embedding_model")
        if not model_name:
            return []
        embed = self.embedder or fastembed_embedder()
        query = f"{self.task.type}: {self.task.question}"
        vector = list(embed(query, model_name))
        return self.store.vector_search_lessons(vector, settings.k, filters)

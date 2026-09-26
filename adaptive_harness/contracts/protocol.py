"""Messages exchanged at run time: tasks, tool results, model replies (sections E, F, G)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import Field, TypeAdapter

from .common import CaseSet, CaseType, Strict, canonical_sha256
from .outputs import TargetRef


class Task(Strict):
    """One case as the harness sees it. Never carries anything from the answer key."""

    id: str
    set: CaseSet
    type: CaseType
    target: TargetRef
    as_of: date
    question: str


# ---------------------------------------------------------------- tools (E)


class ToolResult(Strict):
    text: str  # compact text the model reads
    payload: Any  # structured result
    sha256: str  # canonical SHA-256 of payload

    @classmethod
    def build(cls, text: str, payload: Any) -> "ToolResult":
        return cls(text=text, payload=payload, sha256=canonical_sha256(payload))


# ---------------------------------------------------------------- model client (F)


class ChatMessage(Strict):
    role: Literal["user", "assistant"]
    content: str


class ModelUsage(Strict):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class ModelResponse(Strict):
    text: str
    parsed: Any = None  # the JSON object parsed from text, when there is one
    usage: ModelUsage = Field(default_factory=ModelUsage)
    stop_reason: str | None = None
    latency_ms: int = 0
    model: str  # the model that answered


# ---------------------------------------------------------------- stage protocol (G)


class CallTool(Strict):
    action: Literal["call_tool"]
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    why: str


class Finish(Strict):
    action: Literal["finish"]
    output: dict[str, Any]


StageReply = Annotated[Union[CallTool, Finish], Field(discriminator="action")]
STAGE_REPLY_ADAPTER: TypeAdapter[CallTool | Finish] = TypeAdapter(StageReply)


def parse_stage_reply(obj: Any) -> CallTool | Finish:
    return STAGE_REPLY_ADAPTER.validate_python(obj)


class StageNote(Strict):
    """Output of every stage before S6 (S1 to S5 and X1 to X3), inside a Finish.

    `data` is free-form structured content the stage hands to later stages; its shape is
    set by the runner's charters, not by the contract.
    """

    summary: str
    objections: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

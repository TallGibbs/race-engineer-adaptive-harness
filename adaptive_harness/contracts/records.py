"""MongoDB documents (CONTRACT.md section C). Every model's `id` is stored as `_id`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .common import (
    CONTROLLABLE_CAUSES,
    Arm,
    CaseSet,
    CauseCategory,
    CheckId,
    Document,
    FailureCategory,
    Role,
    Snapshot,
    Strict,
)
from .config import ResolvedModel

COLLECTIONS: tuple[str, ...] = ("runs", "events", "harness_versions", "lessons", "evaluations", "experiments")

RunStatus = Literal["completed", "hold", "budget_exceeded", "harness_error"]
EventType = Literal["model_call", "tool_call", "tool_result", "message", "decision", "check", "error"]
VersionStatus = Literal["baseline", "candidate", "accepted", "rejected", "rolled_back"]
PlanStatus = Literal["armed", "in_control", "signal", "rolled_back"]
LessonStatus = Literal["provisional", "verified", "rejected"]
CheckResult = Literal["PASS", "FAIL", "HARNESS", "NA"]
PhaseStatus = Literal["passed", "stopped", "not_reached"]
Decision = Literal["no_project", "stopped", "accepted", "rejected"]


def event_id(run_id: str, seq: int) -> str:
    """Event _id: "<run_id>:<seq, 5 digits>"."""
    return f"{run_id}:{seq:05d}"


# ---------------------------------------------------------------- runs


class Totals(Strict):
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    wall_seconds: float = 0.0


class RunModel(Strict):
    provider: str
    assignment: dict[Role, str]


class Run(Document):
    id: str = Field(alias="_id")
    harness_version: str
    config_hash: str
    memory_snapshot: Snapshot
    case_id: str
    arm: Arm
    experiment_id: str | None = None
    model: RunModel
    fastf1_version: str
    started_at: datetime
    ended_at: datetime | None = None
    status: RunStatus | None = None  # None while the run is in progress
    totals: Totals = Field(default_factory=Totals)
    output: dict[str, Any] | None = None  # the brief (venue_brief or race_audit), as produced


# ---------------------------------------------------------------- events


class ContextManifestItem(Strict):
    item: str  # context catalog item, e.g. "task", "prior:S1", "lessons"
    id: str | None = None  # id of the source (event id, lesson id), when there is one
    sha256: str
    approx_tokens: int


class Usage(Strict):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class Event(Document):
    id: str = Field(alias="_id")
    run_id: str
    seq: int = Field(ge=0, le=99999)
    stage_id: str | None = None
    role: Role | None = None
    type: EventType
    content: Any = None
    refs: list[str] = Field(default_factory=list)
    context_manifest: list[ContextManifestItem] | None = None  # model_call only
    usage: Usage | None = None
    model: str | None = None  # model_call only: the model that answered
    stop_reason: str | None = None
    content_sha256: str
    created_at: datetime

    @model_validator(mode="after")
    def _id_matches(self) -> "Event":
        if self.id != event_id(self.run_id, self.seq):
            raise ValueError(f"event _id must be {event_id(self.run_id, self.seq)!r}")
        return self


# ---------------------------------------------------------------- harness versions


class PatchOp(Strict):
    """One RFC 6902 JSON Patch operation."""

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str
    value: Any = None
    from_: str | None = Field(default=None, alias="from")


class Change(Strict):
    op: PatchOp
    lesson_id: str  # the verified root cause (lesson) this change addresses
    why: str


class Validation(Strict):
    valid: bool
    reasons: list[str] = Field(default_factory=list)


class Signal(Strict):
    run_id: str
    case_id: str
    passed: int
    threshold: int
    repeated: bool


class ControlPlan(Strict):
    thresholds: dict[str, int]  # case_id -> checks passed in the pilot
    model: ResolvedModel  # the resolved assignment the plan covers
    fastf1_version: str
    reaction_plan: str
    status: PlanStatus
    confirmation_run_ids: list[str] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)


class HarnessVersion(Document):
    id: str = Field(alias="_id")
    parent_id: str | None
    config: dict[str, Any]  # a HarnessConfig as a dict
    config_hash: str
    changes: list[Change] = Field(default_factory=list)
    rationale: str | None = None
    expected_effect: str | None = None
    risks: str | None = None  # what could get worse, and which acceptance rule would catch it
    validation: Validation | None = None
    status: VersionStatus
    pinned: bool = False
    control_plan: ControlPlan | None = None
    decision_reasons: list[str] = Field(default_factory=list)
    created_at: datetime


# ---------------------------------------------------------------- lessons


class DefectRef(Strict):
    run_id: str
    case_id: str
    check_id: CheckId


class Lesson(Document):
    """A root cause from the analyze phase, kept as a lesson learned."""

    id: str = Field(alias="_id")
    defect: DefectRef
    failure_category: FailureCategory
    cause_category: CauseCategory
    controllable: bool
    why_chain: list[str] = Field(min_length=1, max_length=5)
    origin_event_id: str
    source_event_ids: list[str]
    text: str
    status: LessonStatus
    scope: str
    snapshot: Snapshot
    embedding: list[float] = Field(default_factory=list)
    embedding_model: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _controllable(self) -> "Lesson":
        if self.controllable != (self.cause_category in CONTROLLABLE_CAUSES):
            raise ValueError("controllable must be true exactly for method, material, measurement")
        return self


# ---------------------------------------------------------------- evaluations


class CheckVerdict(Strict):
    id: CheckId
    result: CheckResult
    category: FailureCategory | None = None  # set when result is FAIL


class Evaluation(Document):
    id: str = Field(alias="_id")
    run_id: str
    case_id: str
    arm: Arm
    experiment_id: str | None = None
    checks: list[CheckVerdict]
    opportunities: int
    defects: int
    evaluator_sha256: str
    key_sha256: str
    created_at: datetime

    @model_validator(mode="after")
    def _counts(self) -> "Evaluation":
        opp = sum(1 for c in self.checks if c.result in ("PASS", "FAIL"))
        dft = sum(1 for c in self.checks if c.result == "FAIL")
        if (opp, dft) != (self.opportunities, self.defects):
            raise ValueError("opportunities and defects must equal the PASS+FAIL and FAIL counts")
        return self


# ---------------------------------------------------------------- experiments


class Tollgate(Strict):
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class Phase(Strict):
    status: PhaseStatus = "not_reached"
    artifact: dict[str, Any] | None = None
    tollgate: Tollgate | None = None
    completed_at: datetime | None = None


class Dmaic(Strict):
    define: Phase = Field(default_factory=Phase)
    measure: Phase = Field(default_factory=Phase)
    analyze: Phase = Field(default_factory=Phase)
    improve: Phase = Field(default_factory=Phase)
    control: Phase = Field(default_factory=Phase)


class RuleOutcome(Strict):
    holds: bool
    detail: str


class Acceptance(Strict):
    R0: RuleOutcome
    R1: RuleOutcome
    R2: RuleOutcome
    R3: RuleOutcome
    R4: RuleOutcome


class ExperimentCase(Strict):
    id: str
    set: CaseSet


class Experiment(Document):
    """One document per improvement cycle."""

    id: str = Field(alias="_id")
    cases: list[ExperimentCase]
    arms: list[Arm]
    dmaic: Dmaic = Field(default_factory=Dmaic)
    acceptance: Acceptance | None = None
    decision: Decision | None = None
    candidate_version: str | None = None
    report_path: str | None = None
    created_at: datetime

    @field_validator("report_path")
    @classmethod
    def _relative(cls, v: str | None) -> str | None:
        if v and (v.startswith("/") or (len(v) > 1 and v[1] == ":")):
            raise ValueError("report_path must be relative to the repository root")
        return v

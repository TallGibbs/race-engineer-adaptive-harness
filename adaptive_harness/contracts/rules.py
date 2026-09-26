"""Acceptance rules (configs/acceptance.json) and DMAIC settings (configs/cycle.json).

Both files restate SPECIFICATION.md. If they ever differ, SPECIFICATION.md wins.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import CHECK_IDS, Arm, CaseSet, CaseType, CauseCategory, CheckId, FailureCategory, Snapshot, Strict


class ArmRef(Strict):
    arm: Arm
    version: Literal["current", "candidate"]
    snapshot: Snapshot


class AcceptanceRule(Strict):
    id: Literal["R0", "R1", "R2", "R3", "R4"]
    name: str
    statement: str
    case_set: CaseSet | None = None
    max_reruns: int | None = None  # R0
    strictly_more: bool | None = None  # R2
    repeats: Literal["worst_candidate_beats_best_current"] | None = None  # R2
    max_time_ratio: float | None = None  # R4


class AcceptanceRules(Strict):
    specification: Literal["SPECIFICATION.md"] = "SPECIFICATION.md"
    compare: dict[Literal["current", "candidate"], ArmRef]
    same_cases: Literal[True] = True
    same_memory_snapshot: Literal[True] = True
    decision: Literal["accept_only_if_all_hold"] = "accept_only_if_all_hold"
    on_reject: str
    rules: list[AcceptanceRule]

    @model_validator(mode="after")
    def _all_five(self) -> "AcceptanceRules":
        if [r.id for r in self.rules] != ["R0", "R1", "R2", "R3", "R4"]:
            raise ValueError("rules must be R0..R4 in order")
        return self


# ---------------------------------------------------------------- cycle.json


class Ctq(Strict):
    id: CheckId
    name: str
    statement: str
    failure_category: FailureCategory
    applies_to: list[CaseType]


class CauseDef(Strict):
    id: CauseCategory
    controllable: bool
    surface: Literal["/stages", "/context_policy", "/checks"] | None
    description: str


class ControlRule(Strict):
    pin_by: Literal["config_hash"] = "config_hash"
    threshold: Literal["checks_passed_in_pilot"] = "checks_passed_in_pilot"
    signal: Literal["passed_below_threshold"] = "passed_below_threshold"
    reruns_on_signal: int = 1
    on_repeated_signal: Literal["pin_parent_and_open_cycle"] = "pin_parent_and_open_cycle"
    new_cycle_evidence: list[CaseSet] = Field(default_factory=lambda: ["development"])
    out_of_plan: list[Literal["model_assignment", "fastf1_version"]]
    out_of_plan_action: Literal["flag_not_compare"] = "flag_not_compare"
    note: str


class PhaseDef(Strict):
    id: Literal["define", "measure", "analyze", "improve", "control"]
    tollgate: str
    stop_reason: str | None = None


class CycleConfig(Strict):
    specification: Literal["SPECIFICATION.md"] = "SPECIFICATION.md"
    improvement_role: Literal["improvement_agent"] = "improvement_agent"
    ctqs: list[Ctq]
    unit: Literal["case_run"] = "case_run"
    harness_result: str
    harness_reruns: int = 1
    reported: list[str]
    not_reported: list[str]
    cause_categories: list[CauseDef]
    evidence_rule: dict[Literal["define", "analyze", "improvement_prompt"], list[CaseSet]]
    verify_and_control_sets: list[CaseSet]
    lesson_scope: str
    lesson_snapshot_after_analyze: Snapshot
    why_chain_max: int = Field(ge=1, le=5)
    measure_optional_repeat: bool
    phases: list[PhaseDef]
    control: ControlRule

    @model_validator(mode="after")
    def _complete(self) -> "CycleConfig":
        if tuple(c.id for c in self.ctqs) != CHECK_IDS:
            raise ValueError("ctqs must be E1..E8 in order")
        if [p.id for p in self.phases] != ["define", "measure", "analyze", "improve", "control"]:
            raise ValueError("phases must be define, measure, analyze, improve, control")
        for c in self.cause_categories:
            if c.controllable != (c.surface is not None):
                raise ValueError(f"cause {c.id}: exactly the controllable causes have a surface")
        return self

"""Harness configuration (CONTRACT.md sections A and B)."""

from __future__ import annotations

import os
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from .common import ROLES, Role, Strict, TaskRole, canonical_sha256

ContextItem = str  # "task" | "output_schema" | "tool_docs" | "lessons" | "open_objections" | "prior:<stage id>"
CONTEXT_ITEMS: tuple[str, ...] = ("task", "output_schema", "tool_docs", "lessons", "open_objections")
TOOL_NAMES: tuple[str, ...] = ("list_events", "race_control_messages", "track_status", "interval")
REQUIRED_STAGES: tuple[str, ...] = ("S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7")
OPTIONAL_STAGES: tuple[str, ...] = ("X1", "X2", "X3")

# Environment variable holding each role's optional model override.
ROLE_ENV: dict[str, str] = {
    "race_engineer": "MODEL_NAME_RACE_ENGINEER",
    "statistician": "MODEL_NAME_STATISTICIAN",
    "data_engineer": "MODEL_NAME_DATA_ENGINEER",
    "improvement_agent": "MODEL_NAME_IMPROVEMENT",
}
ENV_PREFIX = "env:"


def is_valid_context_item(item: str) -> bool:
    if item in CONTEXT_ITEMS:
        return True
    if item.startswith("prior:"):
        return item[len("prior:"):] in REQUIRED_STAGES + OPTIONAL_STAGES
    return False


# ---------------------------------------------------------------- model block


class RoleModels(Strict):
    race_engineer: str | None = None
    statistician: str | None = None
    data_engineer: str | None = None
    improvement_agent: str | None = None


class ModelBlock(Strict):
    """Values are literal names or "env:<VAR>" references resolved at run time.

    The API key is never stored. No sampling parameters or token limits exist.
    """

    provider: str
    base_url: str | None = None
    default: str
    roles: RoleModels


class ResolvedModel(Strict):
    """What actually runs: every role mapped to a concrete model name."""

    provider: str
    base_url: str | None = None
    assignment: dict[Role, str]


def _resolve_value(value: str | None, env: Mapping[str, str]) -> str | None:
    if value is None:
        return None
    if value.startswith(ENV_PREFIX):
        got = env.get(value[len(ENV_PREFIX):], "")
        return got.strip() or None
    return value


def resolve_model(block: ModelBlock, env: Mapping[str, str] | None = None) -> ResolvedModel:
    """Resolve the model block: a role override when set and non-empty, else the default."""
    env = os.environ if env is None else env
    provider = _resolve_value(block.provider, env)
    default = _resolve_value(block.default, env)
    if not provider or not default:
        raise ValueError("model provider and default model name must be set (MODEL_PROVIDER, MODEL_NAME)")
    assignment: dict[str, str] = {}
    for role in ROLES:
        assignment[role] = _resolve_value(getattr(block.roles, role), env) or default
    return ResolvedModel(provider=provider, base_url=_resolve_value(block.base_url, env), assignment=assignment)


# ---------------------------------------------------------------- stages


class Budgets(Strict):
    max_model_calls: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_wall_seconds: int = Field(ge=1)


class StageDef(Strict):
    """One stage in the fixed catalog. kind "code" stages run no model (S7)."""

    name: str
    kind: Literal["model", "code"] = "model"
    roles: list[TaskRole] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    parallel_group: str | None = None  # consecutive stages sharing a group run in parallel, blind to each other

    @field_validator("tools")
    @classmethod
    def _known_tools(cls, v: list[str]) -> list[str]:
        for t in v:
            if t not in TOOL_NAMES:
                raise ValueError(f"unknown tool {t!r}")
        return v


class OptionalStageDef(StageDef):
    before: str | None = None  # the stage must sit somewhere before this stage
    after: str | None = None  # the stage must sit somewhere after this stage


# ---------------------------------------------------------------- context policy


class LessonFilters(Strict):
    status: str | None = None
    scope: str | None = None


class LessonSettings(Strict):
    k: int = Field(ge=0, le=5)
    filters: LessonFilters


class ContextPolicy(Strict):
    """roles[role][stage_id] -> ordered list of context items.

    A stage with no entry for a role gets the runner default: task, output_schema,
    and tool_docs when the stage has tools.
    """

    lessons: LessonSettings
    roles: dict[TaskRole, dict[str, list[ContextItem]]]

    @field_validator("roles")
    @classmethod
    def _items_from_catalog(cls, v: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
        for role, stages in v.items():
            for stage_id, items in stages.items():
                if stage_id not in REQUIRED_STAGES + OPTIONAL_STAGES:
                    raise ValueError(f"context_policy: unknown stage {stage_id!r} for {role}")
                for item in items:
                    if not is_valid_context_item(item):
                        raise ValueError(f"context_policy: item {item!r} is not in the catalog")
        return v


# ---------------------------------------------------------------- checks


class SchemaCheck(Strict):
    enabled: Literal[True] = True
    editable: Literal[False] = False


class ToggleCheck(Strict):
    enabled: bool


class SourceAgreementCheck(Strict):
    enabled: bool
    tolerance_laps: int = Field(ge=0, le=2)


class MinRacesCheck(Strict):
    enabled: bool
    value: int = Field(ge=1, le=10)


class Checks(Strict):
    schema_: SchemaCheck = Field(alias="schema")
    venue_match: ToggleCheck
    source_agreement: SourceAgreementCheck
    min_races_for_rate: MinRacesCheck


# ---------------------------------------------------------------- the configuration


class HarnessConfig(Strict):
    version_id: str
    parent_id: str | None
    task_family: str = "neutralization_brief"
    model: ModelBlock
    budgets: Budgets
    stages: list[str]
    stage_catalog: dict[str, StageDef]
    optional_stage_catalog: dict[str, OptionalStageDef]
    context_policy: ContextPolicy
    checks: Checks
    change_cap: int = Field(ge=0)

    @model_validator(mode="after")
    def _stage_order(self) -> "HarnessConfig":
        problems = stage_order_problems(self.stages, self.optional_stage_catalog)
        if problems:
            raise ValueError("; ".join(problems))
        if set(self.stage_catalog) != set(REQUIRED_STAGES):
            raise ValueError("stage_catalog must define exactly S1..S7 (S2a, S2b)")
        if set(self.optional_stage_catalog) != set(OPTIONAL_STAGES):
            raise ValueError("optional_stage_catalog must define exactly X1..X3")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")

    def config_hash(self) -> str:
        return config_hash(self.to_dict())


def config_hash(config: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the whole configuration."""
    return canonical_sha256(config)


def stage_order_problems(stages: list[str], optional: Mapping[str, OptionalStageDef]) -> list[str]:
    """Section B's invariants on /stages. Empty list means valid."""
    problems: list[str] = []
    if len(set(stages)) != len(stages):
        problems.append("stages: duplicate stage id")
    for s in stages:
        if s not in REQUIRED_STAGES and s not in OPTIONAL_STAGES:
            problems.append(f"stages: {s!r} is not in the stage catalog")
    for s in REQUIRED_STAGES:
        if s not in stages:
            problems.append(f"stages: required stage {s} was removed")
    if problems:
        return problems
    pos = {s: i for i, s in enumerate(stages)}
    if pos["S4"] > pos["S5"]:
        problems.append("stages: S4 must stay before S5")
    if stages[-2:] != ["S6", "S7"]:
        problems.append("stages: S6 then S7 must stay last")
    for x in OPTIONAL_STAGES:
        if x not in pos or x not in optional:
            continue
        d = optional[x]
        if d.before and pos[x] > pos[d.before]:
            problems.append(f"stages: {x} is only allowed before {d.before}")
        if d.after and pos[x] < pos[d.after]:
            problems.append(f"stages: {x} is only allowed after {d.after}")
    return problems


# Section B: the only JSON Pointer prefixes a proposal may touch, and the surface each belongs to.
EDITABLE_PATHS: dict[str, str] = {
    "/stages": "method",
    "/context_policy": "material",
    "/checks/venue_match": "measurement",
    "/checks/source_agreement": "measurement",
    "/checks/min_races_for_rate": "measurement",
}


def surface_of(path: str) -> str | None:
    """The cause category (surface) an edit path belongs to, or None when the path is fixed."""
    for prefix, surface in EDITABLE_PATHS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return surface
    return None

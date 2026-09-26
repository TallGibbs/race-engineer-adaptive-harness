"""Shared types and hashing helpers used by every contract module."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

TaskRole = Literal["race_engineer", "statistician", "data_engineer"]
Role = Literal["race_engineer", "statistician", "data_engineer", "improvement_agent"]
ROLES: tuple[str, ...] = ("race_engineer", "statistician", "data_engineer", "improvement_agent")
TASK_ROLES: tuple[str, ...] = ("race_engineer", "statistician", "data_engineer")

CaseSet = Literal["development", "held_out", "control"]
CaseType = Literal["venue_brief", "race_audit"]
Snapshot = Literal["M0", "M1"]
Arm = Literal["baseline", "repeat", "memory_only", "candidate", "confirmation"]
CheckId = Literal["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"]
CHECK_IDS: tuple[str, ...] = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
FailureCategory = Literal[
    "schema_invalid",
    "venue_mismatch",
    "coverage_mismatch",
    "indicator_mismatch",
    "arithmetic_mismatch",
    "disposition_wrong",
    "evidence_missing",
    "budget_exceeded",
]
CauseCategory = Literal["method", "material", "measurement", "machine", "people", "environment"]
CONTROLLABLE_CAUSES: tuple[str, ...] = ("method", "material", "measurement")


class Strict(BaseModel):
    """Base for contract models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Document(Strict):
    """Base for MongoDB documents. The `id` field is stored as `_id`."""

    def to_doc(self) -> dict[str, Any]:
        """Dict ready for the store (keeps datetimes as datetimes)."""
        return self.model_dump(by_alias=True)

    def to_json_doc(self) -> dict[str, Any]:
        """JSON-safe dict (datetimes as ISO strings)."""
        return self.model_dump(by_alias=True, mode="json")


def canonical_json(obj: Any) -> bytes:
    """The one canonical JSON form used for every hash in the project."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))

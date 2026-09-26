"""Load the neutralization_brief cases as Task objects.

Reads data/cases.json only. The answer key is never opened here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from adaptive_harness.contracts.protocol import Task

CASES_PATH = Path(__file__).resolve().parents[2] / "data" / "cases.json"


def load_tasks(
    path: str | Path | None = None,
    *,
    sets: list[str] | None = None,
    only_default: bool = False,
) -> list[Task]:
    """All cases as Task objects, in file order.

    sets: keep only these case sets (development, held_out, control).
    only_default: keep only cases marked default in cases.json.
    """
    raw = json.loads(Path(path or CASES_PATH).read_text(encoding="utf-8"))
    as_of = date.fromisoformat(raw["as_of"])
    tasks = []
    for case in raw["cases"]:
        if sets is not None and case["set"] not in sets:
            continue
        if only_default and not case.get("default", False):
            continue
        tasks.append(
            Task(
                id=case["id"],
                set=case["set"],
                type=case["type"],
                target=case["target"],
                as_of=as_of,
                question=raw["questions"][case["type"]],
            )
        )
    return tasks


def load_task(case_id: str, path: str | Path | None = None) -> Task:
    for t in load_tasks(path):
        if t.id == case_id:
            return t
    raise KeyError(f"no case {case_id!r}")

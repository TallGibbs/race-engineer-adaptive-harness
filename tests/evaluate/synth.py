"""Synthetic key, briefs, runs, and events for the evaluator tests.

Every value here is invented. Nothing is read from or copied out of data/key.json.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from adaptive_harness.contracts import canonical_sha256, event_id
from adaptive_harness.evaluate.core import Evaluator
from adaptive_harness.evaluate.keyfile import load_key
from adaptive_harness.testing import FakeStore

T0 = datetime(2031, 1, 1, tzinfo=timezone.utc)
ASSIGN = {"race_engineer": "m-a", "statistician": "m-a", "data_engineer": "m-a", "improvement_agent": "m-b"}
BUDGETS = {"max_model_calls": 20, "max_tool_calls": 40, "max_wall_seconds": 600}

# Wilson 90 for 1 of 3 (computed by hand: centre 0.4124, half-width 0.3341).
R_1_OF_3 = {"value": 0.333, "interval": [0.078, 0.746], "method": "wilson_90"}
R_2_OF_3 = {"value": 0.667, "interval": [0.254, 0.922], "method": "wilson_90"}

SYNTH_KEY = {
    "key_version": 1,
    "as_of": "2031-01-01",
    "cases": {
        "TV1": {
            "type": "venue_brief",
            "target": {"year": 2030, "round": 5},
            "venue_location": "Testville",
            "races": [
                {"year": 2028, "round": 3, "sc": True, "vsc": False, "red_flag": False},
                {"year": 2029, "round": 4, "sc": False, "vsc": True, "red_flag": False},
                {"year": 2030, "round": 5, "sc": False, "vsc": False, "red_flag": True},
            ],
            "counts": {"n_races": 3, "n_sc": 1, "n_vsc": 1, "n_any": 2},
            "disposition": "GO",
            "thin_sample": True,
        },
        "TV0": {
            "type": "venue_brief",
            "target": {"year": 2030, "round": 9},
            "venue_location": "Nowhere Park",
            "races": [],
            "counts": {"n_races": 0, "n_sc": 0, "n_vsc": 0, "n_any": 0},
            "disposition": "HOLD",
            "thin_sample": True,
        },
        "TA1": {"type": "race_audit", "target": {"year": 2030, "round": 7}, "sc": False, "vsc": True, "red_flag": True},
    },
}


def write_key(tmp: Path, obj: dict[str, Any], sha: str | None = None) -> tuple[Path, Path]:
    key_path, sha_path = tmp / "key.json", tmp / "key.sha256"
    key_path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    sha_path.write_text((sha or canonical_sha256(obj)) + "\n", encoding="utf-8")
    return key_path, sha_path


@pytest.fixture
def key_paths(tmp_path: Path) -> tuple[Path, Path]:
    return write_key(tmp_path, SYNTH_KEY)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def ev(store: FakeStore, key_paths) -> Evaluator:
    return Evaluator(store, load_key(*key_paths), source_sha256="evsha")


# ---------------------------------------------------------------- briefs


def row(year: int, rnd: int, sc: bool, vsc: bool, red: bool, evidence: list[str], location: str = "Testville") -> dict:
    return {"year": year, "round": rnd, "location": location, "event_name": f"Test GP {year}",
            "sc": sc, "vsc": vsc, "red_flag": red, "evidence": evidence}


def good_brief(run_id: str) -> dict:
    """A correct TV1 brief. Evidence ids assume add_tool_events(run_id, TV1 races)."""
    return {
        "case_id": "TV1",
        "venue": {"location": "Testville", "from_target": {"year": 2030, "round": 5}, "evidence": [event_id(run_id, 5)]},
        "races": [
            row(2028, 3, True, False, False, [event_id(run_id, 1)]),
            row(2029, 4, False, True, False, [event_id(run_id, 3)]),
            row(2030, 5, False, False, True, [event_id(run_id, 5)]),
        ],
        "counts": {"n_races": 3, "n_sc": 1, "n_vsc": 1, "n_any": 2},
        "rates": copy.deepcopy({"sc": R_1_OF_3, "vsc": R_1_OF_3, "any": R_2_OF_3}),
        "disposition": "GO",
        "thin_sample": True,
        "hold_reason": None,
        "open_objections": [],
    }


def hold_brief() -> dict:
    return {
        "case_id": "TV0",
        "venue": {"location": "Nowhere Park", "from_target": {"year": 2030, "round": 9}, "evidence": []},
        "races": [],
        "counts": {"n_races": 0, "n_sc": 0, "n_vsc": 0, "n_any": 0},
        "rates": {"sc": None, "vsc": None, "any": None},
        "disposition": "HOLD",
        "thin_sample": True,
        "hold_reason": "no race qualifies",
        "open_objections": [],
    }


def good_audit(run_id: str) -> dict:
    return {
        "case_id": "TA1",
        "race": {"year": 2030, "round": 7, "event_name": "Test GP 2030", "location": "Sampletown"},
        "sc": False, "vsc": True, "red_flag": True,
        "evidence": [event_id(run_id, 1)],
        "open_objections": [],
    }


# ---------------------------------------------------------------- runs and events

TV1_RACES = [(2028, 3), (2029, 4), (2030, 5)]


def add_event(store: FakeStore, run_id: str, seq: int, type_: str, content: Any, refs: list[str] | None = None) -> str:
    eid = event_id(run_id, seq)
    store.append_event({
        "_id": eid, "run_id": run_id, "seq": seq, "stage_id": "S4", "role": "data_engineer", "type": type_,
        "content": content, "refs": refs or [], "context_manifest": None, "usage": None, "model": None,
        "stop_reason": None, "content_sha256": canonical_sha256(content), "created_at": T0,
    })
    return eid


def add_tool_events(store: FakeStore, run_id: str, races: list[tuple[int, int]]) -> None:
    """For race i: a tool_call at seq 2i and its tool_result at seq 2i+1."""
    for i, (year, rnd) in enumerate(races):
        call = add_event(store, run_id, 2 * i, "tool_call",
                         {"tool": "race_control_messages", "args": {"year": year, "round": rnd}})
        add_event(store, run_id, 2 * i + 1, "tool_result", {"text": "synthetic", "sha256": "x"}, refs=[call])


def add_run(
    store: FakeStore,
    run_id: str,
    case_id: str,
    output: dict | None,
    *,
    status: str = "completed",
    arm: str = "baseline",
    experiment_id: str | None = "x1",
    version: str = "v1",
    started: int = 0,
    wall: float = 10.0,
    assignment: dict | None = None,
    fastf1: str = "9.9.9",
    snapshot: str = "M0",
    totals: dict | None = None,
) -> str:
    store.insert("runs", {
        "_id": run_id, "harness_version": version, "config_hash": "h", "memory_snapshot": snapshot,
        "case_id": case_id, "arm": arm, "experiment_id": experiment_id,
        "model": {"provider": "fake", "assignment": assignment or ASSIGN}, "fastf1_version": fastf1,
        "started_at": T0 + timedelta(minutes=started), "ended_at": T0 + timedelta(minutes=started + 1),
        "status": status,
        "totals": totals or {"model_calls": 10, "tool_calls": 6, "input_tokens": 0, "output_tokens": 0,
                             "cache_read_tokens": 0, "wall_seconds": wall},
        "output": output,
    })
    return run_id


def add_good_run(store: FakeStore, run_id: str, case_id: str = "TV1", **kw) -> str:
    """A run that passes every applicable check for its case."""
    if case_id == "TV1":
        add_tool_events(store, run_id, TV1_RACES)
        return add_run(store, run_id, case_id, good_brief(run_id), **kw)
    if case_id == "TA1":
        add_tool_events(store, run_id, [(2030, 7)])
        return add_run(store, run_id, case_id, good_audit(run_id), **kw)
    kw.setdefault("status", "hold")
    return add_run(store, run_id, case_id, hold_brief(), **kw)


def results(evaluation: dict) -> dict[str, str]:
    return {c["id"]: c["result"] for c in evaluation["checks"]}

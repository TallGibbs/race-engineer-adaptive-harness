"""Synthetic records for store tests. Values are invented."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ASSIGN = {"race_engineer": "m", "statistician": "m", "data_engineer": "m", "improvement_agent": "m"}


def run_doc(run_id: str = "r1", **over) -> dict:
    return {
        "_id": run_id, "harness_version": "v1", "config_hash": "h", "memory_snapshot": "M0",
        "case_id": "T1", "arm": "baseline", "experiment_id": "x1",
        "model": {"provider": "fake", "assignment": ASSIGN}, "fastf1_version": "3.8.3",
        "started_at": NOW, **over,
    }


def lesson_doc(lesson_id: str = "L1", *, embedding=None, minutes: int = 0, **over) -> dict:
    doc = {
        "_id": lesson_id, "defect": {"run_id": "r1", "case_id": "T1", "check_id": "E3"},
        "failure_category": "coverage_mismatch", "cause_category": "method", "controllable": True,
        "why_chain": ["a", "b"], "origin_event_id": "r1:00001", "source_event_ids": ["r1:00001"],
        "text": f"lesson {lesson_id}", "status": "verified", "scope": "neutralization_brief", "snapshot": "M1",
        "embedding": embedding if embedding is not None else [], "embedding_model": "test-embedder" if embedding else None,
        "created_at": NOW + timedelta(minutes=minutes),
    }
    doc.update(over)
    return doc


def version_doc(version_id: str = "v1", pinned: bool = False, **over) -> dict:
    return {
        "_id": version_id, "parent_id": None if version_id == "v1" else "v1", "config": {"version_id": version_id},
        "config_hash": f"h-{version_id}", "status": "baseline" if version_id == "v1" else "candidate",
        "pinned": pinned, "created_at": NOW, **over,
    }


def evaluation_doc(eval_id: str = "e1", run_id: str = "r1") -> dict:
    return {
        "_id": eval_id, "run_id": run_id, "case_id": "T1", "arm": "baseline", "experiment_id": "x1",
        "checks": [{"id": "E1", "result": "PASS", "category": None},
                   {"id": "E2", "result": "FAIL", "category": "venue_mismatch"}],
        "opportunities": 2, "defects": 1, "evaluator_sha256": "a", "key_sha256": "b", "created_at": NOW,
    }


def experiment_doc(exp_id: str = "x1") -> dict:
    return {"_id": exp_id, "cases": [{"id": "T1", "set": "development"}], "arms": ["baseline"], "created_at": NOW}

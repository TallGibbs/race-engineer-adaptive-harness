"""Control check: in control, a signal, a repeated signal, and runs outside the plan."""

from __future__ import annotations

import json

from adaptive_harness.contracts import HarnessVersion, load_config

from .synth import ASSIGN, T0, TV1_RACES, add_good_run, add_run, add_tool_events, good_brief
from .test_cli import run_cli

PLAN_FASTF1 = "9.9.9"


def pin_v2_with_plan(store, thresholds=None, status="armed"):
    plan = {
        "thresholds": thresholds or {"TV1": 8, "TA1": 4},
        "model": {"provider": "fake", "base_url": None, "assignment": ASSIGN},
        "fastf1_version": PLAN_FASTF1,
        "reaction_plan": "rerun the case once; on a repeat pin the parent and open a new cycle",
        "status": status,
    }
    for vid, parent, pinned in (("v1", None, False), ("v2", "v1", True)):
        store.insert("harness_versions", HarnessVersion(
            _id=vid, parent_id=parent, config=load_config().to_dict(), config_hash="h",
            status="accepted" if vid == "v2" else "baseline", pinned=pinned,
            control_plan=plan if vid == "v2" else None, created_at=T0).to_doc())


def bad_run(store, rid, **kw):
    add_tool_events(store, rid, TV1_RACES)
    return add_run(store, rid, "TV1", good_brief(rid) | {"thin_sample": False}, version="v2", **kw)


def control_events(store, rid):
    return [e["content"] for e in store.find("events", {"run_id": rid, "type": "check"})]


def plan(store):
    return store.get("harness_versions", "v2")["control_plan"]


def test_in_control_run(store, key_paths, capsys):
    pin_v2_with_plan(store)
    add_good_run(store, "c1", version="v2", arm="confirmation")
    capsys.readouterr()
    assert run_cli(store, key_paths, "--control", "--run", "c1") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["control"] == "in_control" and (out["passed"], out["threshold"]) == (8, 8)
    assert plan(store)["status"] == "in_control" and plan(store)["signals"] == []
    assert control_events(store, "c1")[0]["control"] == "in_control"


def test_signal_then_repeat(store, key_paths):
    pin_v2_with_plan(store)
    bad_run(store, "s1", started=1)
    assert run_cli(store, key_paths, "--control", "--run", "s1") == 0
    p = plan(store)
    assert p["status"] == "signal"
    assert p["signals"] == [{"run_id": "s1", "case_id": "TV1", "passed": 7, "threshold": 8, "repeated": False}]
    bad_run(store, "s2", started=2)
    run_cli(store, key_paths, "--control", "--run", "s2")
    assert plan(store)["signals"][-1] == {"run_id": "s2", "case_id": "TV1", "passed": 7, "threshold": 8,
                                          "repeated": True}


def test_signal_cleared_by_in_control_rerun(store, key_paths):
    pin_v2_with_plan(store)
    bad_run(store, "s1", started=1)
    run_cli(store, key_paths, "--control", "--run", "s1")
    add_good_run(store, "s2", version="v2", started=2)
    run_cli(store, key_paths, "--control", "--run", "s2")
    assert plan(store)["status"] == "in_control"
    bad_run(store, "s3", started=3)
    run_cli(store, key_paths, "--control", "--run", "s3")
    assert plan(store)["signals"][-1]["repeated"] is False


def test_scoring_a_pinned_run_applies_control_automatically(store, key_paths):
    pin_v2_with_plan(store)
    bad_run(store, "s1")
    assert run_cli(store, key_paths, "--run", "s1") == 0
    assert plan(store)["status"] == "signal"
    # applying it again does not record a second signal
    run_cli(store, key_paths, "--control", "--run", "s1")
    assert len(plan(store)["signals"]) == 1 and len(control_events(store, "s1")) == 1


def test_different_role_model_is_outside_the_plan(store, key_paths, capsys):
    pin_v2_with_plan(store)
    bad_run(store, "o1", assignment={**ASSIGN, "statistician": "m-other"})
    capsys.readouterr()
    assert run_cli(store, key_paths, "--control", "--run", "o1") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["control"] == "outside_plan" and "statistician" in out["reasons"][0]
    assert plan(store)["signals"] == [] and plan(store)["status"] == "armed"


def test_different_fastf1_is_outside_the_plan(store, key_paths, capsys):
    pin_v2_with_plan(store)
    add_good_run(store, "o2", version="v2", fastf1="0.0.1")
    capsys.readouterr()
    run_cli(store, key_paths, "--control", "--run", "o2")
    out = json.loads(capsys.readouterr().out)
    assert out["control"] == "outside_plan" and "FastF1" in out["reasons"][0]


def test_harness_run_is_not_compared(store, key_paths, capsys):
    pin_v2_with_plan(store)
    add_run(store, "h1", "TV1", None, status="harness_error", version="v2")
    capsys.readouterr()
    run_cli(store, key_paths, "--control", "--run", "h1")
    assert json.loads(capsys.readouterr().out)["control"] == "not_compared"
    assert plan(store)["signals"] == []


def test_unpinned_version_has_no_control(store, key_paths):
    pin_v2_with_plan(store)
    add_good_run(store, "u1", version="v1")
    assert run_cli(store, key_paths, "--control", "--run", "u1") == 2
    assert run_cli(store, key_paths, "--run", "u1") == 0  # scoring still works
    assert control_events(store, "u1") == []


def test_rolled_back_plan_is_not_applied(store, key_paths):
    pin_v2_with_plan(store, status="rolled_back")
    bad_run(store, "s1")
    assert run_cli(store, key_paths, "--control", "--run", "s1") == 2

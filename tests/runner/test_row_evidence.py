"""row_evidence: every race row cites a same-run tool_result whose arguments match that race.
Synthetic briefs and tool results only."""

from __future__ import annotations

import copy

from adaptive_harness.runner import checks

from adaptive_harness.runner.coordinator import Coordinator
from adaptive_harness.testing import FakeStore

from .helpers import AUDIT, BRIEF, RESOLVED, RoleModel, call, config, fake_tools, finish, run_case, standard_scripts


def tr(tool, **args):
    return {"tool": tool, "args": args, "text": "t", "payload": {}, "sha256": "x"}


RESULTS = {
    "run1:00010": tr("list_events", year=2029),
    "run1:00011": tr("race_control_messages", year=2029, round=1),
    "run1:00012": tr("track_status", year=2030, round=1),
    "run1:00013": tr("interval", k=1, n=2),
    "run1:00014": tr("list_events", year=2030),
}


def brief(*evidence_per_row):
    b = copy.deepcopy(BRIEF)
    for row, ev in zip(b["races"], evidence_per_row):
        row["evidence"] = list(ev)
    return b


def test_rows_citing_matching_same_run_results_pass():
    b = brief(["run1:00010", "run1:00011"], ["run1:00012"])
    assert checks.row_evidence(b, "venue_brief", RESULTS) == (True, [])


def test_row_citing_only_a_schedule_or_an_interval_fails():
    ok, why = checks.row_evidence(brief(["run1:00010"], ["run1:00012"]), "venue_brief", RESULTS)
    assert not ok and why == ["race 2029 round 1 cites no recorded tool_result of this run for that race"]
    ok, why = checks.row_evidence(brief(["run1:00011"], ["run1:00013", "run1:00014"]), "venue_brief", RESULTS)
    assert not ok and why == ["race 2030 round 1 cites no recorded tool_result of this run for that race"]


def test_interval_with_matching_arguments_still_does_not_count():
    results = dict(RESULTS, **{"run1:00015": tr("interval", year=2029, round=1)})
    assert not checks.row_evidence(brief(["run1:00015"], ["run1:00012"]), "venue_brief", results)[0]


def test_citation_to_another_runs_event_fails():
    ok, why = checks.row_evidence(brief(["run0:00011"], ["run1:00012"]), "venue_brief", RESULTS)
    assert not ok and "2029 round 1" in why[0]


def test_result_for_another_race_or_no_evidence_fails():
    assert not checks.row_evidence(brief(["run1:00012"], ["run1:00012"]), "venue_brief", RESULTS)[0]
    assert not checks.row_evidence(brief([], ["run1:00012"]), "venue_brief", RESULTS)[0]


def test_race_audit_uses_the_top_level_evidence():
    audit = {"race": {"year": 2029, "round": 1}, "sc": False, "vsc": False, "red_flag": False,
             "evidence": ["run1:00011"]}
    assert checks.row_evidence(audit, "race_audit", RESULTS) == (True, [])
    assert not checks.row_evidence(dict(audit, evidence=["run1:00010"]), "race_audit", RESULTS)[0]


def test_no_race_rows_passes():
    assert checks.row_evidence(dict(BRIEF, races=[]), "venue_brief", RESULTS) == (True, [])


def check_events(events):
    return {e["content"]["check"]: e["content"] for e in events if e["type"] == "check"}


def test_not_run_when_absent_or_off():
    _, events, *_ = run_case()
    assert "row_evidence" not in check_events(events)
    _, events, *_ = run_case(cfg=config(checks__row_evidence={"enabled": False}))
    assert "row_evidence" not in check_events(events)


def test_enabled_check_holds_a_brief_without_row_evidence():
    scripts = standard_scripts()
    scripts["race_engineer"].append(finish(BRIEF))  # the repair round returns the brief unchanged
    run, events, *_ = run_case(scripts, cfg=config(checks__row_evidence={"enabled": True}))
    got = check_events(events)["row_evidence"]
    assert not got["passed"] and len(got["reasons"]) == 2
    decision = [e for e in events if e["type"] == "decision"][-1]["content"]
    assert run["status"] == "hold" and decision["disposition"] == "HOLD"
    assert any(r.startswith("row_evidence: ") for r in decision["reasons"])


def audit_run(evidence, run_id="R-fixed"):
    """A race audit whose data engineer records one result for 2030 round 2, under a fixed run id."""
    scripts = standard_scripts()
    scripts["data_engineer"][2] = call("race_control_messages", year=2030, round=2)
    audit = {"case_id": "T2", "race": {"year": 2030, "round": 2, "event_name": "E", "location": "L"},
             "sc": False, "vsc": False, "red_flag": False, "evidence": evidence, "open_objections": []}
    scripts["race_engineer"][2] = finish(audit)
    store = FakeStore()
    co = Coordinator(config(checks__row_evidence={"enabled": True}), store, RoleModel(scripts), fake_tools(),
                     RESOLVED, "0.0-test")
    co.run(AUDIT, run_id=run_id)
    return store.find("events", {"run_id": run_id}, sort=[("seq", 1)])


def test_enabled_check_passes_with_same_run_evidence_and_fails_on_another_runs():
    events = audit_run(["none"])
    cited = next(e["_id"] for e in events if e["type"] == "tool_result" and e["content"]["tool"] != "interval")
    assert check_events(audit_run([cited]))["row_evidence"]["passed"]
    assert not check_events(audit_run([cited], run_id="R-other"))["row_evidence"]["passed"]

"""E1 to E8: each check's PASS and FAIL, NA for a race audit, counts, and HARNESS."""

from __future__ import annotations

import copy

import pytest

from adaptive_harness.contracts import Evaluation, event_id
from adaptive_harness.evaluate.checks import CATEGORY, wilson_90

from .synth import (
    BUDGETS,
    R_1_OF_3,
    add_event,
    add_good_run,
    add_run,
    add_tool_events,
    good_audit,
    good_brief,
    results,
    TV1_RACES,
)

ALL_PASS = {f"E{i}": "PASS" for i in range(1, 9)}


def test_wilson_matches_hand_computation():
    assert wilson_90(1, 3) == (0.333, 0.078, 0.746)
    assert wilson_90(0, 4)[:2] == (0.0, 0.0)
    assert wilson_90(4, 4)[2] == 1.0


def test_correct_brief_passes_every_check(ev, store):
    add_good_run(store, "r1")
    e = ev.score("r1").to_doc()
    assert results(e) == ALL_PASS
    assert (e["opportunities"], e["defects"]) == (8, 0)
    assert e["evaluator_sha256"] == "evsha" and len(e["key_sha256"]) == 64


def test_correct_hold_brief_passes(ev, store):
    add_good_run(store, "r0", "TV0")
    assert results(ev.score("r0").to_doc()) == ALL_PASS


def _brief_run(store, mutate, run_id="r1"):
    add_tool_events(store, run_id, TV1_RACES)
    brief = good_brief(run_id)
    mutate(brief)
    add_run(store, run_id, "TV1", brief)
    return run_id


def _drop_last_race(b):
    b["races"].pop()
    b["counts"] = {"n_races": 2, "n_sc": 1, "n_vsc": 1, "n_any": 2}
    b["rates"] = {"sc": {"value": 0.5, "interval": [0.146, 0.854], "method": "wilson_90"},
                  "vsc": {"value": 0.5, "interval": [0.146, 0.854], "method": "wilson_90"},
                  "any": {"value": 1.0, "interval": [0.425, 1.0], "method": "wilson_90"}}


def _set(path, value):
    def mutate(b):
        cur = b
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = value
    return mutate


FAILS = [
    ("E1", _set(("unknown_field",), 1)),
    ("E1", _set(("disposition",), "MAYBE")),
    ("E2", _set(("venue", "location"), "Elsewhere")),
    ("E2", _set(("races", 0, "location"), "Elsewhere")),
    ("E3", _drop_last_race),
    ("E4", _set(("races", 1, "vsc"), False)),
    ("E5", _set(("counts", "n_sc"), 2)),
    ("E5", _set(("rates", "sc", "interval"), [0.078, 0.75])),
    ("E5", _set(("rates", "any"), None)),
    ("E6", _set(("thin_sample",), False)),
    ("E6", _set(("disposition",), "HOLD")),
    ("E7", _set(("races", 0, "evidence"), [])),
]


@pytest.mark.parametrize("check_id,mutate", FAILS)
def test_each_check_fails_on_its_defect(ev, store, check_id, mutate):
    _brief_run(store, mutate)
    e = ev.score("r1").to_doc()
    got = results(e)
    assert got[check_id] == "FAIL"
    failed = {c["id"]: c["category"] for c in e["checks"] if c["result"] == "FAIL"}
    assert failed[check_id] == CATEGORY[check_id]
    assert e["defects"] == len(failed) and e["opportunities"] == 8


def test_isolated_defects_do_not_spill_over(ev, store):
    _brief_run(store, _set(("races", 1, "vsc"), False))
    got = results(ev.score("r1").to_doc())
    # the indicator error changes the recomputed counts too, so E5 fails as well
    assert {k for k, v in got.items() if v == "FAIL"} == {"E4", "E5"}
    add_tool_events(store, "r2", TV1_RACES)
    add_run(store, "r2", "TV1", good_brief("r2") | {"thin_sample": False})
    assert {k for k, v in results(ev.score("r2").to_doc()).items() if v == "FAIL"} == {"E6"}


def test_race_from_another_venue_fails_venue_and_coverage(ev, store):
    def extra(b):
        b["races"].append({**b["races"][0], "year": 2027, "round": 2, "location": "Othertown"})
    _brief_run(store, extra)
    got = results(ev.score("r1").to_doc())
    assert got["E2"] == got["E3"] == "FAIL"


def test_arithmetic_within_tolerance_passes(ev, store):
    _brief_run(store, _set(("rates", "sc"), {**R_1_OF_3, "value": 0.3335, "interval": [0.0785, 0.7465]}))
    assert results(ev.score("r1").to_doc())["E5"] == "PASS"
    _brief_run(store, _set(("rates", "sc"), {**R_1_OF_3, "value": 0.3355}), run_id="r2")
    assert results(ev.score("r2").to_doc())["E5"] == "FAIL"


def test_hold_with_rates_fails_disposition(ev, store):
    add_good_run(store, "r0", "TV0")
    run = store.get("runs", "r0")
    store.update("runs", "r0", {"output.disposition": "GO"})
    assert results(ev.score("r0").to_doc())["E6"] == "FAIL"
    store.update("runs", "r0", {"output": {**run["output"], "rates": {"sc": R_1_OF_3, "vsc": None, "any": None}}})
    got = results(ev.score("r0").to_doc())
    assert got["E6"] == "FAIL" and got["E5"] == "FAIL"


# ---------------------------------------------------------------- E7 details


def test_evidence_must_be_a_tool_result_of_this_run_about_that_race(ev, store):
    # cites a tool_call, not a tool_result
    _brief_run(store, _set(("races", 0, "evidence"), [event_id("r1", 0)]), run_id="r1")
    assert results(ev.score("r1").to_doc())["E7"] == "FAIL"
    # cites a tool result about another race
    _brief_run(store, _set(("races", 0, "evidence"), [event_id("r2", 3)]), run_id="r2")
    assert results(ev.score("r2").to_doc())["E7"] == "FAIL"
    # cites a tool result recorded in another run
    _brief_run(store, _set(("races", 0, "evidence"), [event_id("r1", 1)]), run_id="r3")
    assert results(ev.score("r3").to_doc())["E7"] == "FAIL"
    # cites an event that does not exist
    _brief_run(store, _set(("races", 0, "evidence"), [event_id("r4", 77)]), run_id="r4")
    assert results(ev.score("r4").to_doc())["E7"] == "FAIL"


def test_evidence_args_may_be_carried_by_the_result_itself(ev, store):
    add_event(store, "r5", 0, "tool_result", {"tool": "track_status", "args": {"year": 2030, "round": 7}})
    add_run(store, "r5", "TA1", good_audit("r5") | {"evidence": [event_id("r5", 0)]})
    assert results(ev.score("r5").to_doc())["E7"] == "PASS"


# ---------------------------------------------------------------- race audit


def test_race_audit_marks_venue_checks_na(ev, store):
    add_good_run(store, "a1", "TA1")
    e = ev.score("a1").to_doc()
    assert results(e) == {"E1": "PASS", "E2": "NA", "E3": "NA", "E4": "PASS", "E5": "NA", "E6": "NA",
                          "E7": "PASS", "E8": "PASS"}
    assert (e["opportunities"], e["defects"]) == (4, 0)


def test_race_audit_failures(ev, store):
    add_tool_events(store, "a2", [(2030, 7)])
    add_run(store, "a2", "TA1", good_audit("a2") | {"red_flag": False})
    assert results(ev.score("a2").to_doc())["E4"] == "FAIL"
    add_tool_events(store, "a3", [(2030, 6)])
    add_run(store, "a3", "TA1", good_audit("a3"))
    e = ev.score("a3").to_doc()
    assert results(e)["E7"] == "FAIL" and (e["opportunities"], e["defects"]) == (4, 1)


# ---------------------------------------------------------------- E8 and status


def test_budget(ev, store):
    add_good_run(store, "b1", status="budget_exceeded")
    assert results(ev.score("b1").to_doc())["E8"] == "FAIL"
    add_good_run(store, "b2", totals={"model_calls": BUDGETS["max_model_calls"] + 1, "tool_calls": 1, "wall_seconds": 5.0})
    assert results(ev.score("b2").to_doc())["E8"] == "FAIL"


def test_no_output_fails_every_applicable_check_but_budget(ev, store):
    add_run(store, "n1", "TV1", None, status="budget_exceeded")
    e = ev.score("n1").to_doc()
    assert set(results(e).values()) == {"FAIL"} and e["defects"] == 8


def test_harness_run_scores_harness_never_fail(ev, store):
    add_run(store, "h1", "TV1", None, status="harness_error")
    e = ev.score("h1").to_doc()
    assert set(results(e).values()) == {"HARNESS"}
    assert (e["opportunities"], e["defects"]) == (0, 0)
    add_run(store, "h2", "TA1", None, status="harness_error")
    got = results(ev.score("h2").to_doc())
    assert {k for k, v in got.items() if v == "HARNESS"} == {"E1", "E4", "E7", "E8"}
    assert {k for k, v in got.items() if v == "NA"} == {"E2", "E3", "E5", "E6"}


def test_evaluation_documents_validate(ev, store):
    add_good_run(store, "r1")
    _brief_run(store, _set(("counts", "n_sc"), 3), run_id="r2")
    for rid in ("r1", "r2"):
        Evaluation.model_validate(copy.deepcopy(ev.score(rid).to_doc()))

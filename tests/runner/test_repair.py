"""S7 repair round: when an enabled catalog check fails, the S6 role revises the brief once."""

from __future__ import annotations

from .helpers import BRIEF, config, finish, run_case, standard_scripts

MIN5 = {"checks__min_races_for_rate": {"enabled": True, "value": 5}}
NO_RATES = dict(BRIEF, rates={"sc": None, "vsc": None, "any": None})  # passes min_races_for_rate


def s7(events):
    return [e for e in events if e["stage_id"] == "S7"]


def s6_outputs(events):
    return [e for e in events if e["stage_id"] == "S6" and e["type"] == "message"]


def scripts_with(*repairs):
    scripts = standard_scripts()
    scripts["race_engineer"] += [finish(r) for r in repairs]
    return scripts


def test_v1_s7_path_is_unchanged():
    run, events, model, _ = run_case(scripts_with(NO_RATES))  # a spare reply that must not be used
    assert [(e["type"], e["content"].get("check")) for e in s7(events)] == [("check", "schema"), ("decision", None)]
    assert len(model.calls("race_engineer")) == 3 and len(s6_outputs(events)) == 1
    assert run["status"] == "completed" and run["output"] == BRIEF


def test_schema_failure_gets_no_repair():
    bad = dict(BRIEF, extra_field=1)
    for cfg in (config(), config(**MIN5)):
        scripts = standard_scripts(bad)
        scripts["race_engineer"].append(finish(NO_RATES))  # a spare reply that must not be used
        run, events, model, _ = run_case(scripts, cfg=cfg)
        assert [e["content"].get("check") for e in s7(events)] == ["schema", None]
        assert len(model.calls("race_engineer")) == 3
        assert run["status"] == "hold" and run["output"] == bad


def test_failing_check_triggers_one_repair_that_can_complete_go():
    run, events, model, _ = run_case(scripts_with(NO_RATES), cfg=config(**MIN5))
    assert len(model.calls("race_engineer")) == 4
    repair_prompt = model.calls("race_engineer")[3]["messages"][-1]["content"]
    assert "min_races_for_rate: rates reported on 2 races" in repair_prompt
    first, revised = s6_outputs(events)
    checks = [e for e in s7(events) if e["type"] == "check"]
    assert [c["content"]["check"] for c in checks] == ["schema", "min_races_for_rate", "schema", "min_races_for_rate"]
    assert not checks[1]["content"]["passed"] and checks[3]["content"]["passed"]
    assert {checks[0]["_id"], checks[1]["_id"]} <= set(revised["refs"])
    assert revised["content"] == NO_RATES and checks[2]["refs"] == [revised["_id"]]
    decision = [e for e in events if e["type"] == "decision"][-1]
    assert decision["content"] == {"status": "completed", "disposition": "GO", "reasons": []}
    assert run["status"] == "completed" and run["output"] == NO_RATES


def test_still_failing_brief_ends_hold_with_no_second_round():
    run, events, model, _ = run_case(scripts_with(BRIEF, NO_RATES), cfg=config(**MIN5))
    assert len(model.calls("race_engineer")) == 4  # the spare second reply is never asked for
    assert len(s6_outputs(events)) == 2
    decision = [e for e in events if e["type"] == "decision"][-1]["content"]
    assert decision["disposition"] == "HOLD" and decision["reasons"][0].startswith("min_races_for_rate: ")
    assert run["status"] == "hold" and run["output"] == BRIEF


def test_repair_counts_against_the_budget():
    _, _, model, _ = run_case(scripts_with(NO_RATES), cfg=config(**MIN5))
    used = sum(len(model.calls(r)) for r in ("race_engineer", "statistician", "data_engineer"))
    run, *_ = run_case(scripts_with(NO_RATES), cfg=config(**MIN5, budgets__max_model_calls=used - 1))
    assert run["status"] == "budget_exceeded"

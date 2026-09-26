from __future__ import annotations

import copy
import json

import pytest

from adaptive_harness.__main__ import main
from adaptive_harness.runner import checks, cli

from .helpers import BRIEF, RoleModel, standard_scripts


def lists(year=2030, loc="Testville"):
    return {"tool": "list_events", "args": {"year": year},
            "payload": {"events": [{"round": 1, "location": loc}]}}


def test_schema_check():
    assert checks.schema_check(BRIEF, "venue_brief") == (True, [])
    ok, why = checks.schema_check(dict(BRIEF, extra=1), "venue_brief")
    assert not ok and any("extra" in w for w in why)
    assert checks.schema_check(None, "venue_brief")[0] is False


def test_venue_match():
    assert checks.venue_match(BRIEF, "venue_brief", [lists()]) == (True, [])
    ok, why = checks.venue_match(BRIEF, "venue_brief", [lists(loc="Elsewhere")])
    assert not ok and "Elsewhere" in why[0]
    assert not checks.venue_match(BRIEF, "venue_brief", [])[0]
    moved = copy.deepcopy(BRIEF)
    moved["races"][0]["location"] = "Other"
    assert not checks.venue_match(moved, "venue_brief", [lists()])[0]
    assert checks.venue_match({}, "race_audit", []) == (True, [])


def test_min_races_for_rate():
    assert checks.min_races_for_rate(BRIEF, "venue_brief", 2)[0]
    assert not checks.min_races_for_rate(BRIEF, "venue_brief", 3)[0]
    no_rates = dict(BRIEF, rates={"sc": None, "vsc": None, "any": None})
    assert checks.min_races_for_rate(no_rates, "venue_brief", 3)[0]


def _src(tool, year, rnd, sc, lap=None):
    return {"tool": tool, "args": {"year": year, "round": rnd},
            "payload": {"sc": sc, "vsc": False, "red_flag": False, "sc_laps": [lap] if lap else []}}


def test_source_agreement():
    both = []
    for r in BRIEF["races"]:
        both += [_src("race_control_messages", r["year"], r["round"], r["sc"], 10 if r["sc"] else None),
                 _src("track_status", r["year"], r["round"], r["sc"], 11 if r["sc"] else None)]
    assert checks.source_agreement(BRIEF, "venue_brief", both, 1) == (True, [])
    ok, why = checks.source_agreement(BRIEF, "venue_brief", both, 0)
    assert not ok and "first laps" in why[0]
    ok, why = checks.source_agreement(BRIEF, "venue_brief", both[:1] + both[2:], 1)
    assert not ok and "no comparable track_status" in why[0]
    flipped = copy.deepcopy(both)
    flipped[1]["payload"]["sc"] = False
    assert not checks.source_agreement(BRIEF, "venue_brief", flipped, 2)[0]


def test_resolve_config(tmp_path):
    store = cli.open_store("memory")
    by_path = cli.resolve_config("configs/v1.json", store)
    assert cli.resolve_config("v1", store).config_hash() == by_path.config_hash()
    assert cli.resolve_config("pinned", store).version_id == "v1"
    with pytest.raises(SystemExit):
        cli.resolve_config("v9", store)


def test_cli_run_prints_assignment_and_run_ids(monkeypatch, capsys, tmp_path):
    for var, value in {"MODEL_PROVIDER": "fake", "MODEL_NAME": "default-model",
                       "MODEL_NAME_RACE_ENGINEER": "re-override", "MODEL_NAME_STATISTICIAN": "",
                       "MODEL_NAME_DATA_ENGINEER": "", "MODEL_NAME_IMPROVEMENT": "", "MODEL_BASE_URL": ""}.items():
        monkeypatch.setenv(var, value)
    made = []

    def fake_build(resolved, env=None):
        brief = dict(BRIEF, case_id="P1")
        m = RoleModel(standard_scripts(brief), assignment=resolved.assignment)
        made.append(m)
        return m

    monkeypatch.setattr("adaptive_harness.runner.models.build_client", fake_build)
    code = main(["run", "--", "--config", "configs/v1.json", "--snapshot", "M0", "--cases", "P1",
                 "--store", "memory", "--tools", "synthetic", "--dump", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "race_engineer: re-override" in out and "statistician: default-model" in out
    line = out.strip().splitlines()[-1].split("\t")
    assert line[0].startswith("P1-") and line[1] == "P1"

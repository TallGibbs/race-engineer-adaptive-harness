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


def _rc(year, rnd, sc=False, vsc=False, red=False, sc_lap=10, start_behind=False):
    rows = [{"lap": 1, "category": "Flag", "message": "GREEN LIGHT - PIT EXIT OPEN", "flag": "GREEN", "scope": "Track"}]
    if start_behind:
        rows.append({"lap": 1, "category": "Other", "message": "RACE WILL START BEHIND THE SAFETY CAR",
                     "flag": None, "scope": None})
    if sc:
        rows.append({"lap": sc_lap, "category": "SafetyCar", "message": "SAFETY CAR DEPLOYED", "flag": None, "scope": None})
    if vsc:
        rows.append({"lap": 20, "category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED", "flag": None,
                     "scope": None})
    if red:
        rows.append({"lap": 30, "category": "Flag", "message": "RED FLAG", "flag": "RED", "scope": "Track"})
    return {"tool": "race_control_messages", "args": {"year": year, "round": rnd},
            "payload": {"year": year, "round": rnd, "event_name": "E", "messages": rows}}


def _ts(year, rnd, sc=False, vsc=False, red=False):
    rows = [{"time": "0:00:00.000", "status": "1", "message": "AllClear"}]
    if sc:
        rows.append({"time": "0:20:00.000", "status": "4", "message": "SCDeployed"})
    if vsc:
        rows.append({"time": "0:30:00.000", "status": "6", "message": "VSCDeployed"})
    if red:
        rows.append({"time": "0:40:00.000", "status": "5", "message": "Red"})
    return {"tool": "track_status", "args": {"year": year, "round": rnd},
            "payload": {"year": year, "round": rnd, "event_name": "E", "track_status": rows}}


def test_flags_from_race_control_rows():
    f = checks.flags_from_race_control(_rc(2030, 1, sc=True, vsc=True, red=True, sc_lap=7)["payload"])
    assert f == {"sc": (True, 7), "vsc": (True, 20), "red_flag": (True, 30)}
    assert checks.flags_from_race_control(_rc(2030, 1)["payload"]) == {
        "sc": (False, None), "vsc": (False, None), "red_flag": (False, None)}
    assert checks.flags_from_race_control(_rc(2030, 1, start_behind=True)["payload"])["sc"] == (True, 1)
    assert checks.flags_from_race_control({"messages": "bad"}) is None


def test_flags_from_track_status_rows():
    assert checks.flags_from_track_status(_ts(2030, 1, vsc=True)["payload"]) == {
        "sc": (False, None), "vsc": (True, None), "red_flag": (False, None)}
    by_message = {"track_status": [{"time": "0:10:00.000", "status": None, "message": "SCDeployed"}]}
    assert checks.flags_from_track_status(by_message)["sc"] == (True, None)
    assert checks.flags_from_track_status({"status": []}) is None


def test_source_agreement():
    both = []
    for r in BRIEF["races"]:
        both += [_rc(r["year"], r["round"], sc=r["sc"]), _ts(r["year"], r["round"], sc=r["sc"])]
    assert checks.source_agreement(BRIEF, "venue_brief", both, 1) == (True, [])
    ok, why = checks.source_agreement(BRIEF, "venue_brief", both[:1] + both[2:], 1)
    assert not ok and "no comparable track_status" in why[0]
    disagree = both[:1] + [_ts(2029, 1, sc=False)] + both[2:]
    ok, why = checks.source_agreement(BRIEF, "venue_brief", disagree, 2)
    assert not ok and "disagree on sc" in why[0]
    wrong_brief = copy.deepcopy(BRIEF)
    wrong_brief["races"][1]["vsc"] = True
    ok, why = checks.source_agreement(wrong_brief, "venue_brief", both, 1)
    assert not ok and "brief says vsc=True" in why[0]
    audit = {"race": {"year": 2030, "round": 2}, "sc": False, "vsc": True, "red_flag": False}
    assert checks.source_agreement(audit, "race_audit", [_rc(2030, 2, vsc=True), _ts(2030, 2, vsc=True)], 0)[0]


def test_synthetic_tools_match_the_derivations():
    from datetime import date

    from adaptive_harness.runner.synthetic import synthetic_tools

    tools = synthetic_tools()
    for rnd in range(1, 8):
        rc = tools.call("race_control_messages", {"year": 2025, "round": rnd}, date(2026, 1, 1)).payload
        ts = tools.call("track_status", {"year": 2025, "round": rnd}, date(2026, 1, 1)).payload
        a, b = checks.flags_from_race_control(rc), checks.flags_from_track_status(ts)
        assert {k: v[0] for k, v in a.items()} == {k: v[0] for k, v in b.items()}


def test_open_store_prefers_mongo_from_env(monkeypatch):
    import adaptive_harness.store as store_lane

    class FakeMongoStore:
        made = []

        def __init__(self, client, db_name):
            raise AssertionError("the plain constructor needs a client and a db name")

        @classmethod
        def from_env(cls):
            obj = object.__new__(cls)
            cls.made.append(obj)
            return obj

    monkeypatch.setattr(store_lane, "MongoStore", FakeMongoStore, raising=False)
    assert cli.open_store("mongo") is FakeMongoStore.made[0]

    monkeypatch.delattr(store_lane, "MongoStore")
    monkeypatch.setattr(store_lane, "open_store", lambda: "fallback", raising=False)
    assert cli.open_store("mongo") == "fallback"


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
    code = main(["run", "--config", "configs/v1.json", "--snapshot", "M0", "--cases", "P1",
                 "--store", "memory", "--tools", "synthetic", "--dump", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "race_engineer: re-override" in out and "statistician: default-model" in out
    line = out.strip().splitlines()[-1].split("\t")
    assert line[0].startswith("P1-") and line[1] == "P1"

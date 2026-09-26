"""Report lane: gathering, markdown report, Mermaid maps, and CLI (FakeStore, no network)."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from adaptive_harness.contracts import load_config
from adaptive_harness.contracts.common import CHECK_IDS
from adaptive_harness.report import cli, maps, markdown
from adaptive_harness.report.data import load
from adaptive_harness.store.records import save_phase
from adaptive_harness.testing import FakeStore

T0 = datetime(2031, 1, 1, tzinfo=timezone.utc)
ASSIGN = {"race_engineer": "m-big", "statistician": "m-small", "data_engineer": "m-small", "improvement_agent": "m-big"}
CASES = [("D1", "development", "venue_brief"), ("D2", "development", "race_audit"),
         ("H1", "held_out", "venue_brief"), ("P1", "control", "venue_brief")]
AUDIT_NA = {"E2", "E3", "E5", "E6"}


def verdicts(case_type: str, fails: set[str]) -> list[dict]:
    out = []
    for cid in CHECK_IDS:
        if case_type == "race_audit" and cid in AUDIT_NA:
            out.append({"id": cid, "result": "NA"})
        elif cid in fails:
            out.append({"id": cid, "result": "FAIL", "category": "evidence_missing"})
        else:
            out.append({"id": cid, "result": "PASS"})
    return out


def add_arm(store, arm, version, snapshot, fails, minutes):
    for i, (case, _, ctype) in enumerate(CASES):
        rid = f"{case}-{arm}"
        store.insert("runs", {
            "_id": rid, "harness_version": version, "config_hash": f"hash-{version}", "memory_snapshot": snapshot,
            "case_id": case, "arm": arm, "experiment_id": "x1", "model": {"provider": "fake", "assignment": ASSIGN},
            "fastf1_version": "0", "started_at": T0 + timedelta(minutes=minutes + i), "status": "completed",
            "totals": {"model_calls": 3, "tool_calls": 2, "input_tokens": 100, "output_tokens": 10,
                       "cache_read_tokens": 50, "wall_seconds": 12.5},
        })
        for seq, model in enumerate(["m-big", "m-small", "m-small"]):
            store.insert("events", {"_id": f"{rid}:{seq:05d}", "run_id": rid, "seq": seq, "stage_id": "S1",
                                    "role": "race_engineer", "type": "model_call", "content": {}, "model": model,
                                    "usage": {"input_tokens": 30, "output_tokens": 3, "cache_read_tokens": 10},
                                    "created_at": T0})
        store.insert("events", {"_id": f"{rid}:00003", "run_id": rid, "seq": 3, "stage_id": "S7", "role": None,
                                "type": "check", "content": {"check": "schema (strict)", "passed": True},
                                "created_at": T0})
        f = fails.get(case, set())
        checks = verdicts(ctype, f)
        opps = sum(c["result"] in ("PASS", "FAIL") for c in checks)
        store.insert("evaluations", {"_id": f"ev-{rid}", "run_id": rid, "case_id": case, "arm": arm,
                                     "experiment_id": "x1", "checks": checks, "opportunities": opps,
                                     "defects": len(f), "evaluator_sha256": "e", "key_sha256": "k",
                                     "created_at": T0})


def version(vid, parent, config, status, pinned):
    return {"_id": vid, "parent_id": parent, "config": config, "config_hash": f"hash-{vid}", "changes": [],
            "status": status, "pinned": pinned, "decision_reasons": [], "created_at": T0}


@pytest.fixture
def store():
    s = FakeStore()
    v1 = load_config().to_dict()
    v2 = copy.deepcopy(v1)
    v2["version_id"], v2["parent_id"] = "v2", "v1"
    v2["checks"]["venue_match"]["enabled"] = True
    s.insert("harness_versions", version("v1", None, v1, "baseline", True))
    s.insert("harness_versions", version("v2", "v1", v2, "rejected", False))
    add_arm(s, "baseline", "v1", "M0", {"D2": {"E7"}, "H1": {"E7"}}, 0)
    add_arm(s, "memory_only", "v1", "M1", {"D2": {"E7"}, "P1": {"E2", "E7"}}, 60)
    add_arm(s, "candidate", "v2", "M1", {"D2": {"E7"}}, 120)
    s.insert("lessons", {"_id": "L1", "defect": {"run_id": "D2-baseline", "case_id": "D2", "check_id": "E7"},
                         "failure_category": "evidence_missing", "cause_category": "measurement",
                         "controllable": True, "why_chain": ["first why", "second why"],
                         "origin_event_id": "D2-baseline:00003", "source_event_ids": ["D2-baseline:00003"],
                         "text": "a lesson", "status": "verified", "scope": "neutralization_brief",
                         "snapshot": "M1", "created_at": T0})
    s.insert("experiments", {"_id": "x1", "cases": [{"id": c, "set": st} for c, st, _ in CASES],
                             "arms": ["baseline"], "candidate_version": "v2", "decision": "rejected",
                             "acceptance": {r: {"holds": r != "R2", "detail": f"detail {r}"}
                                            for r in ("R0", "R1", "R2", "R3", "R4")},
                             "created_at": T0})
    save_phase(s, "x1", "define", {"charter": {"baseline": {"arm": "baseline", "defects": 1, "opportunities": 12,
                                                              "runs": 2, "failure_categories": {}},
                                                 "ctqs": [], "scope": [], "goal": ["g1"], "cases": ["D1", "D2"],
                                                 "evidence": ["development"], "out_of_scope": ["model"]},
                                   "problem_statement": "The problem (in brief)."},
               {"passed": True, "reasons": ["1 development defect(s) in the baseline arm"]})
    save_phase(s, "x1", "measure", {"hashes": {"evaluator_sha256": ["e"], "key_sha256": ["k"]}, "rescore": [],
                                    "harness_reruns": []}, {"passed": True, "reasons": ["measurement valid"]})
    save_phase(s, "x1", "analyze", {"defects": 1, "snapshot": "M1", "verified": ["L1"],
                                    "verified_controllable": ["L1"], "provisional": [],
                                    "root_causes": [{"lesson_id": "L1", "cause_category": "measurement",
                                                     "controllable": True, "status": "verified", "problems": [],
                                                     "defect": {"run_id": "D2-baseline", "case_id": "D2",
                                                                "check_id": "E7", "category": "evidence_missing"},
                                                     "agent": {"model": "m-big", "usage": {"input_tokens": 5,
                                                                                           "output_tokens": 1}}}]},
               {"passed": True, "reasons": ["1 verified controllable root cause(s)"]})
    save_phase(s, "x1", "improve", {"candidate_version": "v2", "from": "v1", "retrieval": "vector",
                                    "root_causes": ["L1"], "validation": {"valid": True, "reasons": []},
                                    "proposal": {"changes": [{"lesson_id": "L1", "why": "because",
                                                              "op": {"op": "replace",
                                                                     "path": "/checks/venue_match/enabled",
                                                                     "value": True}}],
                                                 "rationale": "r", "expected_effect": "e", "risks": "k"}},
               {"passed": False, "reasons": ["R2 improvement: candidate worst 11, current best 11"]})
    return s


def test_load_picks_arms_in_run_order_and_usage_by_model(store):
    d = load(store, "x1")
    assert list(d.arms) == ["baseline", "memory_only", "candidate"]
    assert d.cases == ["D1", "D2", "H1", "P1"]
    assert d.arms["candidate"].versions == ["v2"] and d.arms["memory_only"].snapshot == "M1"
    u = d.arms["baseline"].usage_by_model
    assert u["m-big"]["model_calls"] == 4 and u["m-small"]["input_tokens"] == 8 * 30
    assert d.arms["baseline"].totals["wall_seconds"] == 50.0
    assert [l["_id"] for l in d.lessons] == ["L1"] and "D2-baseline:00003" in d.events
    with pytest.raises(KeyError):
        load(store, "nope")


def test_report_has_every_phase_with_its_verdict(store):
    text = markdown.render(load(store, "x1"), {"define": "Optional cases were used, not added."})
    for heading in ("## Define", "## Measure", "## Analyze", "## Improve", "## Control", "## Limits"):
        assert heading in text
    assert "Optional cases were used, not added." in text
    assert "**Tollgate: PASSED**" in text and "**Tollgate: DID NOT PASS** (stopped)" in text
    assert "**Tollgate: not reached.**" in text and "Confirmation: not run." in text
    assert "Where the cycle stopped: improve" in text
    # DPO always carries its counts; no sigma claim beyond the disclaimer
    assert "| baseline (v1 on M0) | 4 | 0.071 (2/28) |" in text
    assert "| memory_only (v1 on M1) | 4 | 0.107 (3/28) |" in text
    assert "sigma level" in text and "no sigma level" in text.lower()
    # the case-by-check table has one column per arm and marks differing verdicts
    assert "| P1 | control | E2 venue ◆ | PASS | FAIL | PASS |" in text
    assert "| R2 | **no** | detail R2 |" in text
    # tokens per model, the cache-write note, and no price without operator prices
    assert "| baseline | m-big | 4 | 120 | 12 | 40 |" in text
    assert "includes prompt-cache writes" in text and "No per-token prices were supplied" in text
    assert "run chart" in text.lower() and "xychart-beta" in text
    assert "1. first why" in text and '"check": "schema (strict)"' in text


def test_run_chart_omitted_when_it_does_not_render(store):
    text = markdown.render(load(store, "x1"), xychart_ok=False)
    assert "xychart-beta" not in text and "did not render" in text


def test_diff_marks_every_kind_of_change():
    old = load_config().to_dict()
    new = copy.deepcopy(old)
    new["stages"] = ["X1", "S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"]
    new["context_policy"]["roles"]["statistician"]["S5"] = ["task", "output_schema"]
    new["checks"]["source_agreement"]["enabled"] = True
    marks, removed = maps.diff_marks(old, new)
    assert marks["X1"] == ["added"] and "context changed" in marks["S5"] and marks["S7"] == ["checks changed"]
    assert removed == []
    marks, removed = maps.diff_marks(new, old)
    assert removed == ["X1"]
    swapped = copy.deepcopy(old)
    swapped["stages"] = ["X2", "S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"]
    reordered = copy.deepcopy(swapped)
    reordered["stages"] = ["S1", "X2", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"]
    marks, _ = maps.diff_marks(swapped, reordered)
    assert "moved" in marks["X2"] or "moved" in marks["S1"]
    assert any("source_agreement" in c for c in maps.change_summary(old, new))


def test_maps_are_generated_stamped_and_parseable_labels(store):
    d = load(store, "x1")
    diagrams = cli.build_maps(store, d, ["v1", "v2"])
    names = [n for n, _, _ in diagrams]
    assert names == ["v1_process", "dmaic_cycle", "v1_vs_v2"]
    src = dict((n, s) for n, _, s in diagrams)
    # process map: stable ids, stamp with hash and run ids; S2 not executed in these synthetic runs
    assert "v1_S4" in src["v1_process"] and "hash-v1" in src["v1_process"] and "D1-baseline" in src["v1_process"]
    assert "[not executed]" in src["v1_process"] and "[not executed]" not in "\n".join(
        l for l in src["v1_process"].splitlines() if "v1_S1[" in l or "v1_S7[" in l)
    # dmaic map: verdicts quoted on edges, control dashed and not reached
    cyc = src["dmaic_cycle"]
    assert 'define -->|"passed: 1 development defect(s) in the<br/>baseline arm"| measure' in cyc
    assert 'improve -.->|"not reached"| control' in cyc and "class control notreached" in cyc
    assert "decision: rejected" in cyc
    # every edge label is quoted, so parentheses cannot break the Mermaid parser
    for line in cyc.splitlines():
        if "|" in line and "-->" in line or "-.->" in line:
            assert '|"' in line
    cmp_ = src["v1_vs_v2"]
    assert "[checks changed]" in cmp_ and "class v2_S7 chk" in cmp_ and "legend" in cmp_
    text = cli.maps_markdown(d, diagrams, {"v1": d.versions["v1"], "v2": d.versions["v2"]}, {})
    assert text.count("```mermaid") == 3 and "venue_match" in text and "Legend:" in text


def test_cli_report_and_maps_write_files(store, tmp_path, monkeypatch, capsys):
    store.close = lambda: None
    monkeypatch.setattr(cli, "open_store", lambda: store)
    monkeypatch.setattr(cli, "mermaid_cli", lambda: None)
    (tmp_path / "x1.notes.json").write_text('{"define": "operator note"}', encoding="utf-8")
    out = tmp_path / "x1.md"
    assert cli.report(["--experiment", "x1", "--out", str(out)]) == 0
    assert "operator note" in out.read_text(encoding="utf-8")
    assert "report_path" not in store.get("experiments", "x1")  # outside the repo: not recorded
    assert "not inside the repository" in capsys.readouterr().err
    mp = tmp_path / "maps.md"
    assert cli.maps(["--versions", "v1,v2", "--experiment", "x1", "--out", str(mp)]) == 0
    assert mp.exists() and "not exported" in capsys.readouterr().err
    assert cli.report(["--experiment", "nope", "--out", str(out)]) == 1


def test_dispatcher_routes_report_and_maps():
    import adaptive_harness.__main__ as entry

    assert entry._handler("report") is cli.report and entry._handler("maps") is cli.maps


def test_improve_shows_proposal_history(store):
    store.insert("harness_versions", {**version("v0x", "v1", load_config().to_dict(), "rejected", False),
                                      "changes": [{"op": {"op": "replace", "path": "/checks/venue_match/enabled",
                                                          "value": True}, "lesson_id": "L0", "why": "w"}],
                                      "decision_reasons": ["R2 improvement: worst 11, best 11"]})
    exp = store.get("experiments", "x1")
    exp["dmaic"]["improve"]["artifact"]["history"] = ["v0x"]
    store.update("experiments", "x1", {"dmaic": exp["dmaic"]})
    text = markdown.render(load(store, "x1"))
    assert "### Proposal history shown to the improvement agent" in text
    assert "| v0x | rejected | replace `/checks/venue_match/enabled` = true | R2 improvement: worst 11, best 11 |" in text


def test_maps_export_folder_follows_output_name(store, tmp_path, monkeypatch):
    store.close = lambda: None
    monkeypatch.setattr(cli, "open_store", lambda: store)
    monkeypatch.setattr(cli, "mermaid_cli", lambda: ["mmdc"])
    seen = []

    def fake_render(src, outputs, cli_=None, timeout=0):
        seen.extend(outputs)
        return True, "ok"

    monkeypatch.setattr(cli, "render_mermaid", fake_render)
    out = tmp_path / "maps_x1.md"
    assert cli.maps(["--versions", "v1,v2", "--experiment", "x1", "--out", str(out)]) == 0
    assert {p.parent.name for p in seen} == {"maps_x1"}
    assert "(maps_x1/v1_process.svg)" in out.read_text(encoding="utf-8")

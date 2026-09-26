"""Proposal validation: path whitelist, change cap, root-cause citation, bounds."""

from __future__ import annotations

import pytest

from adaptive_harness.contracts import load_config
from adaptive_harness.dmaic.validate import PatchError, apply_patch, validate_proposal

LESSONS = {
    "L-method": {"_id": "L-method", "status": "verified", "controllable": True, "cause_category": "method"},
    "L-material": {"_id": "L-material", "status": "verified", "controllable": True, "cause_category": "material"},
    "L-measure": {"_id": "L-measure", "status": "verified", "controllable": True, "cause_category": "measurement"},
    "L-machine": {"_id": "L-machine", "status": "verified", "controllable": False, "cause_category": "machine"},
}


def parent():
    return load_config().to_dict()


def proposal(*changes):
    return {"changes": list(changes), "rationale": "r", "expected_effect": "e", "risks": "R2 would catch it"}


def change(op, path, value=None, lesson="L-method", **extra):
    o = {"op": op, "path": path, **extra}
    if value is not None:
        o["value"] = value
    return {"op": o, "lesson_id": lesson, "why": "addresses the cited root cause"}


def check(p):
    return validate_proposal(p, parent(), LESSONS, new_version_id="v2")


def test_valid_method_change_builds_the_candidate():
    config, changes, reasons = check(proposal(change("add", "/stages/0", "X1")))
    assert reasons == []
    assert config["stages"][:2] == ["X1", "S1"]
    assert config["version_id"] == "v2" and config["parent_id"] == "v1"
    assert len(changes) == 1


def test_valid_changes_on_each_surface():
    config, _, reasons = check(proposal(
        change("replace", "/context_policy/lessons/k", 5, lesson="L-material"),
        change("replace", "/checks/venue_match/enabled", True, lesson="L-measure"),
        change("add", "/stages/5", "X3"),
    ))
    assert reasons == []
    assert config["context_policy"]["lessons"]["k"] == 5
    assert config["checks"]["venue_match"]["enabled"] is True
    assert config["stages"].index("X3") > config["stages"].index("S4")


def test_stages_change_citing_a_material_root_cause_is_rejected():
    config, _, reasons = check(proposal(change("add", "/stages/0", "X1", lesson="L-material")))
    assert config is None
    assert any("/stages/0 is a method surface but root cause L-material is a material cause" in r for r in reasons)


@pytest.mark.parametrize("path,value", [
    ("/model/default", "another-model"),
    ("/model/roles/statistician", "another-model"),
    ("/budgets/max_model_calls", 40),
    ("/evaluator", {"strict": False}),
    ("/checks/schema/enabled", False),
    ("/change_cap", 9),
    ("/stage_catalog/S4/tools", []),
])
def test_change_touching_fixed_paths_is_rejected(path, value):
    config, _, reasons = check(proposal(change("replace", path, value)))
    assert config is None
    assert any(f"{path} is not an editable path" in r for r in reasons)


def test_move_from_a_fixed_path_is_rejected():
    config, _, reasons = check(proposal(change("move", "/stages/0", **{"from": "/model/default"})))
    assert config is None and any("from '/model/default' is not an editable path" in r for r in reasons)


def test_fourth_change_is_rejected():
    ops = [change("add", "/stages/0", "X1"), change("add", "/stages/5", "X3"), change("add", "/stages/2", "X2"),
           change("replace", "/checks/venue_match/enabled", True, lesson="L-measure")]
    config, _, reasons = check(proposal(*ops))
    assert config is None
    assert "4 changes exceed the change cap of 3" in reasons


def test_three_changes_are_within_the_cap():
    ops = [change("add", "/stages/0", "X1"), change("add", "/stages/6", "X3"), change("add", "/stages/2", "X2")]
    assert check(proposal(*ops))[2] == []


def test_uncontrollable_or_unknown_citations_are_rejected():
    _, _, reasons = check(proposal(change("add", "/stages/0", "X1", lesson="L-machine")))
    assert any("not controllable" in r for r in reasons)
    _, _, reasons = check(proposal(change("add", "/stages/0", "X1", lesson="L-nowhere")))
    assert any("not a verified controllable root cause given to the agent" in r for r in reasons)


def test_bounds_and_stage_order_are_enforced():
    _, _, reasons = check(proposal(change("replace", "/context_policy/lessons/k", 9, lesson="L-material")))
    assert any(r.startswith("schema:") for r in reasons)
    _, _, reasons = check(proposal(change("remove", "/stages/4")))  # S4 removed
    assert any("required stage S4 was removed" in r for r in reasons)
    _, _, reasons = check(proposal(change("add", "/stages/-", "X1")))  # X1 after S7
    assert reasons
    _, _, reasons = check(proposal(change("replace", "/checks/min_races_for_rate/value", 11, lesson="L-measure")))
    assert any(r.startswith("schema:") for r in reasons)


def test_lesson_retrieval_stays_on_verified_lessons():
    _, _, reasons = check(proposal(
        change("replace", "/context_policy/lessons/filters/status", "provisional", lesson="L-material")))
    assert "lesson retrieval must stay limited to verified lessons" in reasons


def test_missing_text_and_no_op_changes_are_rejected():
    p = proposal(change("replace", "/checks/venue_match/enabled", False, lesson="L-measure"))
    assert "the changes leave the configuration unchanged" in check(p)[2]
    p = {**proposal(change("add", "/stages/0", "X1")), "risks": ""}
    assert "the proposal has no risks" in check(p)[2]
    assert check(None)[2] == ["the proposal is not a JSON object"]
    assert check({"changes": []})[2] == ["the proposal has no changes"]


def test_apply_patch_operations():
    doc = {"a": [1, 2], "b": {"c": 1}}
    assert apply_patch(doc, [{"op": "add", "path": "/a/-", "value": 3}])["a"] == [1, 2, 3]
    assert apply_patch(doc, [{"op": "move", "from": "/a/0", "path": "/a/1"}])["a"] == [2, 1]
    assert apply_patch(doc, [{"op": "copy", "from": "/b/c", "path": "/b/d"}])["b"] == {"c": 1, "d": 1}
    assert doc == {"a": [1, 2], "b": {"c": 1}}  # the input is not modified
    with pytest.raises(PatchError):
        apply_patch(doc, [{"op": "replace", "path": "/b/zz", "value": 1}])
    with pytest.raises(PatchError):
        apply_patch(doc, [{"op": "test", "path": "/b/c", "value": 2}])


def test_row_evidence_can_be_added_on_the_measurement_surface():
    config, _, reasons = check(proposal(change("add", "/checks/row_evidence", {"enabled": True}, lesson="L-measure")))
    assert reasons == []
    assert config["checks"]["row_evidence"] == {"enabled": True}
    config, _, reasons = check(proposal(change("add", "/checks/row_evidence", {"enabled": True}, lesson="L-material")))
    assert config is None and any("measurement surface" in r for r in reasons)


def test_bounds_list_every_editable_check():
    from adaptive_harness.contracts import CHECK_BOUNDS
    from adaptive_harness.dmaic.prompts import BOUNDS
    for path in CHECK_BOUNDS:
        assert f"- {path} (" in BOUNDS

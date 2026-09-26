import copy
import json

import pytest
from pydantic import ValidationError

from adaptive_harness.contracts import (
    CHECK_IDS,
    CONFIGS_DIR,
    HarnessConfig,
    load_acceptance,
    load_config,
    load_cycle,
    resolve_model,
    stage_order_problems,
    surface_of,
)
from adaptive_harness.contracts.paths import REPO_ROOT


def raw(name):
    return json.loads((CONFIGS_DIR / name).read_text(encoding="utf-8"))


def test_v1_valid_and_baseline():
    c = load_config()
    assert c.version_id == "v1" and c.parent_id is None
    assert c.stages == ["S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"]
    assert (c.budgets.max_model_calls, c.budgets.max_tool_calls, c.budgets.max_wall_seconds) == (20, 40, 600)
    assert c.change_cap == 3
    assert c.context_policy.lessons.k == 3
    assert c.checks.schema_.enabled and not c.checks.venue_match.enabled
    assert c.checks.source_agreement.tolerance_laps == 1 and c.checks.min_races_for_rate.value == 1
    assert len(c.config_hash()) == 64


def test_v1_lessons_only_at_first_stage_of_each_role():
    c = load_config()
    for role, stages in c.context_policy.roles.items():
        order = [s for s in c.stages if s in stages]
        for s in order:
            assert ("lessons" in stages[s]) == (s == order[0]), (role, s)


def test_no_sampling_params_token_limits_or_keys_in_configs():
    text = (CONFIGS_DIR / "v1.json").read_text(encoding="utf-8").lower()
    for word in ("temperature", "top_p", "top_k", "max_tokens", "api_key", "token_budget"):
        assert word not in text


def test_acceptance_and_cycle_valid():
    a = load_acceptance()
    assert [r.id for r in a.rules] == ["R0", "R1", "R2", "R3", "R4"]
    assert a.rules[4].max_time_ratio == 1.5 and a.rules[0].max_reruns == 1
    cy = load_cycle()
    assert tuple(c.id for c in cy.ctqs) == CHECK_IDS
    na_for_audit = {c.id for c in cy.ctqs if "race_audit" not in c.applies_to}
    assert na_for_audit == {"E2", "E3", "E5", "E6"}
    assert {c.id for c in cy.cause_categories if c.controllable} == {"method", "material", "measurement"}


def _norm(text):
    return " ".join(text.split()).lower()


def test_rule_files_cite_the_specification():
    spec = _norm((REPO_ROOT / "SPECIFICATION.md").read_text(encoding="utf-8"))
    for r in load_acceptance().rules:
        assert _norm(f"{r.id} {r.name}: {r.statement}") in spec, r.id
    for c in load_cycle().ctqs:
        assert _norm(f"{c.id} {c.name}: {c.statement}") in spec, c.id
    assert _norm(load_cycle().control.note) in spec
    assert _norm(load_cycle().harness_result) in spec


def test_resolve_model_role_override_else_default():
    m = load_config().model
    env = {"MODEL_PROVIDER": "p", "MODEL_NAME": "base", "MODEL_NAME_STATISTICIAN": "stat", "MODEL_NAME_RACE_ENGINEER": " "}
    r = resolve_model(m, env)
    assert r.assignment == {"race_engineer": "base", "statistician": "stat", "data_engineer": "base",
                            "improvement_agent": "base"}
    with pytest.raises(ValueError):
        resolve_model(m, {})


@pytest.mark.parametrize(
    "stages,ok",
    [
        (["X1", "S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"], True),
        (["S1", "S2a", "S2b", "X2", "S3", "S4", "X3", "S5", "S6", "S7"], True),
        (["S1", "S2b", "S2a", "S3", "S4", "S5", "S6", "S7"], True),
        (["S1", "S2a", "S2b", "S3", "S5", "S4", "S6", "S7"], False),
        (["S1", "S2a", "S2b", "S3", "S4", "S5", "S7", "S6"], False),
        (["S1", "S2a", "S2b", "S4", "S5", "S6", "S7"], False),
        (["S1", "X1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"], False),
        (["S1", "S2a", "S2b", "S3", "S4", "S5", "X2", "S6", "S7"], False),
        (["X3", "S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7"], False),
        (["S1", "S2a", "S2b", "S3", "S4", "S5", "S6", "S7", "X9"], False),
    ],
)
def test_stage_order_bounds(stages, ok):
    c = load_config()
    assert (stage_order_problems(stages, c.optional_stage_catalog) == []) == ok
    data = copy.deepcopy(c.to_dict())
    data["stages"] = stages
    if ok:
        HarnessConfig.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            HarnessConfig.model_validate(data)


@pytest.mark.parametrize(
    "path,value",
    [
        ("/context_policy/lessons/k", 6),
        ("/context_policy/lessons/filters/snapshot", "M1"),
        ("/context_policy/roles/statistician/S5", ["gossip"]),
        ("/checks/source_agreement/tolerance_laps", 3),
        ("/checks/min_races_for_rate/value", 11),
        ("/checks/schema/enabled", False),
    ],
)
def test_bounds_reject_out_of_range(path, value):
    data = copy.deepcopy(load_config().to_dict())
    *parents, leaf = path.strip("/").split("/")
    cur = data
    for p in parents:
        cur = cur[p]
    cur[leaf] = value
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate(data)


def test_editable_surfaces():
    assert surface_of("/stages/2") == "method"
    assert surface_of("/context_policy/lessons/k") == "material"
    assert surface_of("/checks/venue_match/enabled") == "measurement"
    for fixed in ("/checks/schema/enabled", "/model/default", "/budgets/max_model_calls", "/change_cap",
                  "/stage_catalog/S4/tools", "/optional_stage_catalog/X1/before"):
        assert surface_of(fixed) is None, fixed

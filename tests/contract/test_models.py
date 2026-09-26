import copy

import pytest
from pydantic import ValidationError

from adaptive_harness.contracts import export, outputs, parse_stage_reply, records
from adaptive_harness.contracts.paths import SCHEMAS_DIR

from .samples import HOLD_BRIEF, VENUE_BRIEF, samples


@pytest.mark.parametrize("model,data", samples(), ids=lambda x: getattr(x, "__name__", ""))
def test_round_trip(model, data):
    obj = model.model_validate(data)
    # python round trip
    again = model.model_validate(obj.model_dump(by_alias=True))
    assert again == obj
    # JSON round trip
    again = model.model_validate_json(obj.model_dump_json(by_alias=True))
    assert again.model_dump(by_alias=True, mode="json") == obj.model_dump(by_alias=True, mode="json")


def test_every_exported_model_has_a_sample():
    sampled = {m for m, _ in samples()}
    assert set(export.MODELS.values()) <= sampled


def test_exported_schemas_are_current():
    for name, schema in export.all_schemas().items():
        path = SCHEMAS_DIR / f"{name}.schema.json"
        assert path.exists(), f"missing {path.name}; run python -m adaptive_harness.contracts.export"
        assert path.read_text(encoding="utf-8") == export.render(schema), f"{path.name} is stale"


def test_outputs_reject_unknown_fields():
    bad = copy.deepcopy(VENUE_BRIEF)
    bad["extra"] = 1
    with pytest.raises(ValidationError):
        outputs.VenueBrief.model_validate(bad)
    outputs.VenueBrief.model_validate(HOLD_BRIEF)


def test_stage_reply_union():
    assert parse_stage_reply({"action": "finish", "output": {}}).action == "finish"
    assert parse_stage_reply({"action": "call_tool", "tool": "t", "args": {}, "why": "w"}).tool == "t"
    with pytest.raises(ValidationError):
        parse_stage_reply({"action": "dance"})


def test_event_id_format():
    assert records.event_id("run-1", 7) == "run-1:00007"
    data = dict(samples()[15][1])
    data["_id"] = "r1:1"
    with pytest.raises(ValidationError):
        records.Event.model_validate(data)


def test_evaluation_counts_are_consistent():
    data = copy.deepcopy(dict(samples()[18][1]))
    data["defects"] = 0
    with pytest.raises(ValidationError):
        records.Evaluation.model_validate(data)


def test_lesson_controllable_matches_category():
    data = copy.deepcopy(dict(samples()[17][1]))
    data["cause_category"] = "machine"
    with pytest.raises(ValidationError):
        records.Lesson.model_validate(data)

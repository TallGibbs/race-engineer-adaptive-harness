"""row_evidence joined the check catalog after v1: optional, absent means off, and absent
from the canonical serialization, so stored configurations keep their hashes."""

import copy
import json

import pytest
from pydantic import ValidationError

from adaptive_harness.contracts import CHECK_BOUNDS, CONFIGS_DIR, EDITABLE_PATHS, HarnessConfig, load_config, surface_of
from adaptive_harness.contracts.common import canonical_sha256

# The hash of configs/v1.json as stored before row_evidence was added to the catalog.
V1_HASH = "e2f2dfa286b03eb3b3334d6bfb226f9503a5f8534693430191d982c4a8144538"


def raw_v1():
    return json.loads((CONFIGS_DIR / "v1.json").read_text(encoding="utf-8"))


def test_v1_hash_is_unchanged():
    assert load_config().config_hash() == V1_HASH
    assert canonical_sha256(raw_v1()) == V1_HASH


def test_absent_row_evidence_validates_as_off_and_is_not_serialized():
    d = raw_v1()
    assert "row_evidence" not in d["checks"]
    c = HarnessConfig.model_validate(d)
    assert c.checks.row_evidence is None
    assert "row_evidence" not in c.to_dict()["checks"]
    assert "row_evidence" not in c.model_dump(by_alias=True)["checks"]
    assert c.to_dict() == d


def test_present_row_evidence_round_trips_and_changes_the_hash():
    d = copy.deepcopy(raw_v1())
    d["checks"]["row_evidence"] = {"enabled": False}
    c = HarnessConfig.model_validate(d)
    assert c.to_dict()["checks"]["row_evidence"] == {"enabled": False}
    assert c.config_hash() != V1_HASH
    d["checks"]["row_evidence"] = {"enabled": True, "extra": 1}
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate(d)


def test_row_evidence_is_an_editable_measurement_path():
    assert EDITABLE_PATHS["/checks/row_evidence"] == "measurement"
    assert surface_of("/checks/row_evidence") == "measurement"
    assert surface_of("/checks/row_evidence/enabled") == "measurement"
    assert set(CHECK_BOUNDS) == {p for p in EDITABLE_PATHS if p.startswith("/checks/")}

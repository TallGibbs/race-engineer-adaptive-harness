import json
from pathlib import Path

from adaptive_harness.contracts import DATA_DIR, canonical_sha256

import examples.neutralization_brief.task as task_module
from examples.neutralization_brief.task import load_task, load_tasks


def test_key_hash_matches_frozen_hash():
    # Sanctioned exception to the key rule: this contract test hashes the key and
    # compares against data/key.sha256. It never inspects the key's values.
    obj = json.loads((DATA_DIR / "key.json").read_text(encoding="utf-8"))
    expected = (DATA_DIR / "key.sha256").read_text(encoding="utf-8").split()[0]
    assert canonical_sha256(obj) == expected


def test_tasks_load_from_cases_only():
    tasks = load_tasks()
    cases = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    assert [t.id for t in tasks] == [c["id"] for c in cases["cases"]]
    for t in tasks:
        assert t.as_of.isoformat() == cases["as_of"]
        assert t.question == cases["questions"][t.type]
    assert {t.set for t in load_tasks(sets=["development"])} == {"development"}
    assert all(c.get("default") for c in cases["cases"] if c["id"] in {t.id for t in load_tasks(only_default=True)})
    assert load_task(tasks[0].id) == tasks[0]


def test_task_loader_never_references_the_key():
    source = Path(task_module.__file__).read_text(encoding="utf-8")
    assert "key" + ".json" not in source and "key" + ".sha256" not in source

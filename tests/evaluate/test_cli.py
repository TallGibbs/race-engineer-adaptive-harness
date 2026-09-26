"""The evaluator process: hash refusal, storing, re-scoring, export, and the module entry point."""

from __future__ import annotations

import json
import subprocess
import sys

from adaptive_harness.contracts import REPO_ROOT, Evaluation
from adaptive_harness.evaluate import cli
from adaptive_harness.evaluate.keyfile import KeyHashMismatch, evaluator_sha256, load_key

from .synth import SYNTH_KEY, add_good_run, add_run, write_key

import pytest


def run_cli(store, key_paths, *argv):
    return cli.main(list(argv), store=store, key_path=key_paths[0], sha_path=key_paths[1])


def test_key_hash_mismatch_refuses_to_score(tmp_path, store, capsys):
    paths = write_key(tmp_path, SYNTH_KEY, sha="0" * 64)
    add_good_run(store, "r1")
    assert run_cli(store, paths, "--run", "r1") == 3
    assert store.find("evaluations") == []
    assert "refusing" in capsys.readouterr().err


def test_tampered_key_is_refused(tmp_path):
    key_path, sha_path = write_key(tmp_path, SYNTH_KEY)
    tampered = json.loads(key_path.read_text(encoding="utf-8"))
    tampered["cases"]["TA1"]["sc"] = True
    key_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(KeyHashMismatch):
        load_key(key_path, sha_path)


def test_hash_is_canonical_not_textual(tmp_path):
    key_path, sha_path = write_key(tmp_path, SYNTH_KEY)
    key_path.write_text(json.dumps(SYNTH_KEY, indent=7, sort_keys=False), encoding="utf-8")
    assert load_key(key_path, sha_path).sha256 == sha_path.read_text(encoding="utf-8").strip()


def test_evaluate_run_stores_hashes_and_counts(store, key_paths, capsys):
    add_good_run(store, "r1")
    assert run_cli(store, key_paths, "--run", "r1") == 0
    [doc] = store.find("evaluations")
    ev = Evaluation.model_validate(doc)
    assert ev.evaluator_sha256 == evaluator_sha256()
    assert ev.key_sha256 == key_paths[1].read_text(encoding="utf-8").strip()
    assert (ev.opportunities, ev.defects) == (8, 0)
    out = json.loads(capsys.readouterr().out)
    assert out[0]["written"] is True
    # a second run of the command keeps the stored evaluation
    assert run_cli(store, key_paths, "--run", "r1") == 0
    assert json.loads(capsys.readouterr().out)[0]["written"] is False


def test_experiment_scores_all_runs(store, key_paths):
    add_good_run(store, "r1")
    add_good_run(store, "a1", "TA1")
    add_run(store, "h1", "TV1", None, status="harness_error")
    add_good_run(store, "other", experiment_id="x2")
    assert run_cli(store, key_paths, "--experiment", "x1") == 0
    assert sorted(e["run_id"] for e in store.find("evaluations")) == ["a1", "h1", "r1"]


def test_rescore_matches(store, key_paths, capsys):
    add_good_run(store, "r1")
    run_cli(store, key_paths, "--run", "r1")
    capsys.readouterr()
    assert run_cli(store, key_paths, "--rescore", "--run", "r1") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["identical"] and out["differing_checks"] == []
    assert len(store.find("evaluations")) == 1  # re-scoring never writes


def test_rescore_differs_exits_4_and_names_checks(store, key_paths, capsys):
    add_good_run(store, "r1")
    run_cli(store, key_paths, "--run", "r1")
    stored = store.get("evaluations", "eval:r1")
    checks = stored["checks"]
    checks[2] = {"id": "E3", "result": "FAIL", "category": "coverage_mismatch"}
    checks[6] = {"id": "E7", "result": "FAIL", "category": "evidence_missing"}
    store.update("evaluations", "eval:r1", {"checks": checks, "defects": 2})
    capsys.readouterr()
    assert run_cli(store, key_paths, "--rescore", "--run", "r1") == 4
    captured = capsys.readouterr()
    assert json.loads(captured.out)["differing_checks"] == ["E3", "E7"]
    assert "E3, E7" in captured.err


def test_rescore_without_stored_evaluation_is_an_error(store, key_paths):
    add_good_run(store, "r1")
    assert run_cli(store, key_paths, "--rescore", "--run", "r1") == 2


def test_export_gives_categories_and_counts_only(store, key_paths, capsys):
    add_good_run(store, "r1")
    add_run(store, "r2", "TV1", None, status="budget_exceeded")
    run_cli(store, key_paths, "--experiment", "x1")
    capsys.readouterr()
    assert run_cli(store, key_paths, "--export", "--experiment", "x1") == 0
    out = {r["run_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert out["r1"]["defects"] == 0 and out["r1"]["failures"] == []
    assert out["r2"]["defects"] == 8
    assert {f["category"] for f in out["r2"]["failures"]} >= {"schema_invalid", "budget_exceeded"}
    assert set(out["r2"]) == {"run_id", "case_id", "arm", "experiment_id", "opportunities", "defects",
                              "harness", "failures"}
    assert set(out["r2"]["failures"][0]) == {"check_id", "category"}


def test_usage_errors(store, key_paths):
    with pytest.raises(SystemExit):
        run_cli(store, key_paths)
    with pytest.raises(SystemExit):
        run_cli(store, key_paths, "--accept", "--run", "r1")


def test_unknown_run_is_an_error(store, key_paths):
    assert run_cli(store, key_paths, "--run", "nope") == 2


def test_runs_as_its_own_process():
    proc = subprocess.run([sys.executable, "-m", "adaptive_harness.evaluate", "--help"],
                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0 and "--rescore" in proc.stdout


def test_evaluator_hash_ignores_line_endings(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    crlf = evaluator_sha256(tmp_path)
    (tmp_path / "a.py").write_bytes(b"x = 1\ny = 2\n")
    assert evaluator_sha256(tmp_path) == crlf
    (tmp_path / "a.py").write_bytes(b"x = 2\n")
    assert evaluator_sha256(tmp_path) != crlf

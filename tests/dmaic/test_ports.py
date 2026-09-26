"""The real runner and model-client ports, with the subprocess and client builder faked."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from adaptive_harness.dmaic import ports
from adaptive_harness.dmaic.ports import CliRunner, PortError, parse_run_lines

RUNNER_OUT = (
    "harness v2 (0123456789ab), provider fake\n"
    "  race_engineer: m-a\n"
    "  improvement_agent: m-b\n"
    "r-held\tH1\tcompleted\n"
    "r-dev\tD1\tharness_error\n"
)


def fake_run(returncode: int, stdout: str, calls: list):
    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="boom")
    return run


def test_cli_runner_builds_the_run_command_and_parses_run_lines(monkeypatch):
    calls: list = []
    monkeypatch.setattr(subprocess, "run", fake_run(1, RUNNER_OUT, calls))  # exit 1: a run ended HARNESS
    ids = CliRunner(python="py").run_cases(
        experiment_id="exp1", arm="candidate", version_id="v2", snapshot="M1", case_ids=["D1", "H1"]
    )
    assert ids == ["r-dev", "r-held"]  # in case order
    assert calls[0] == ["py", "-m", "adaptive_harness", "run", "--config", "v2", "--snapshot", "M1",
                        "--cases", "D1,H1", "--arm", "candidate", "--experiment", "exp1"]


def test_cli_runner_raises_when_the_runner_fails(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(2, "", []))
    with pytest.raises(PortError, match="runner exited 2"):
        CliRunner().run_cases(experiment_id="e", arm="baseline", version_id="v1", snapshot="M0", case_ids=["D1"])
    monkeypatch.setattr(subprocess, "run", fake_run(1, "harness v1\n", []))
    with pytest.raises(PortError):
        CliRunner().run_cases(experiment_id="e", arm="baseline", version_id="v1", snapshot="M0", case_ids=["D1"])


def test_parse_run_lines_skips_headers():
    assert parse_run_lines(RUNNER_OUT) == [("r-held", "H1", "completed"), ("r-dev", "D1", "harness_error")]


def test_default_model_client_uses_the_runner_builder(monkeypatch):
    import adaptive_harness.runner.models as models

    seen = {}
    env = {"MODEL_PROVIDER": "anthropic", "MODEL_NAME": "m-default", "MODEL_NAME_IMPROVEMENT": "m-improve"}
    for var in ("MODEL_BASE_URL", "MODEL_NAME_RACE_ENGINEER", "MODEL_NAME_STATISTICIAN", "MODEL_NAME_DATA_ENGINEER"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(models, "build_client", lambda resolved: seen.setdefault("resolved", resolved) and "client")
    assert ports.default_model_client() == "client"
    assert seen["resolved"].assignment["improvement_agent"] == "m-improve"
    assert seen["resolved"].assignment["statistician"] == "m-default"

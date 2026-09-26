"""Integration wiring of `python -m adaptive_harness` to lane handlers (no network)."""

from __future__ import annotations

import adaptive_harness.__main__ as entry
from adaptive_harness.store import append_event, cli as store_cli, start_run
from adaptive_harness.testing import FakeStore

from .store.samples import run_doc


def _fake_store(monkeypatch) -> FakeStore:
    s = FakeStore()
    s.close = lambda: None
    monkeypatch.setattr(store_cli.MongoStore, "from_env", classmethod(lambda cls, **kw: s))
    return s


def test_show_falls_back_to_store_lane_until_report_provides_it():
    handler = entry._handler("show")
    report_cli = None
    try:
        import adaptive_harness.report.cli as report_cli  # noqa: F811
    except ModuleNotFoundError:
        pass
    if report_cli is not None and hasattr(report_cli, "show"):
        assert handler is report_cli.show
    else:
        assert handler is store_cli.show


def test_show_run_prints_events(monkeypatch, capsys):
    s = _fake_store(monkeypatch)
    start_run(s, run_doc())
    append_event(s, "r1", "message", {"text": "framing"}, seq=0, stage_id="S1", role="race_engineer")
    assert entry.main(["show", "run", "r1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("run r1") and "framing" in out


def test_show_run_not_found(monkeypatch, capsys):
    _fake_store(monkeypatch)
    assert entry.main(["show", "run", "no-such-run"]) == 1
    assert "no run 'no-such-run'" in capsys.readouterr().err


def test_fallback_only_for_listed_subcommands(monkeypatch):
    monkeypatch.setitem(entry.SUBCOMMANDS, "zzz-test", ("nolane", "test"))
    try:
        entry._handler("zzz-test")
    except entry.NotImplementedYet:
        pass
    else:
        raise AssertionError("expected NotImplementedYet")


def _capture_handler(monkeypatch, name: str) -> list[list[str]]:
    seen: list[list[str]] = []

    def handler(argv):
        seen.append(argv)
        return 0

    monkeypatch.setattr(entry, "_handler", lambda n: handler if n == name else None)
    return seen


def test_leading_flag_passes_through_unchanged(monkeypatch):
    seen = _capture_handler(monkeypatch, "run")
    argv = ["--config", "configs/v1.json", "--cases", "T1", "-x", "--", "tail"]
    assert entry.main(["run", *argv]) == 0
    assert seen == [argv]


def test_flag_first_reaches_real_lane_handler(monkeypatch, capsys):
    s = _fake_store(monkeypatch)
    start_run(s, run_doc())
    assert entry.main(["show", "--width", "60", "run", "r1"]) == 0
    assert capsys.readouterr().out.startswith("run r1")


def test_no_args_and_unknown_command(capsys):
    assert entry.main([]) == 0
    assert "<command>" in capsys.readouterr().out
    try:
        entry.main(["no-such-command"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected argparse to reject an unknown command")

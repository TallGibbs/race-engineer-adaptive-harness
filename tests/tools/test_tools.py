"""Tools lane tests: synthetic schedules and sessions, no network."""

from __future__ import annotations

import os
import re
from datetime import date

import pandas as pd
import pytest
import requests

from adaptive_harness.contracts.common import canonical_sha256
from adaptive_harness.contracts.errors import AsOfViolation, DataUnavailable, FetchError
from adaptive_harness.contracts.interfaces import Tool
from adaptive_harness.contracts.protocol import ToolResult
from examples.neutralization_brief import tools as tools_mod
from examples.neutralization_brief.tools import build_tools

AS_OF = date(2026, 9, 25)


def schedule_frame(year: int = 2024) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"RoundNumber": 1, "EventName": "Alpha Grand Prix", "OfficialEventName": "FORMULA 1 ALPHA GP",
             "Location": "Alphaville", "Country": "Alphaland", "EventDate": pd.Timestamp(f"{year}-03-02"),
             "EventFormat": "conventional"},
            {"RoundNumber": 2, "EventName": "Beta Grand Prix", "OfficialEventName": "FORMULA 1 BETA GP",
             "Location": "Beta City", "Country": "Betaland", "EventDate": pd.Timestamp(f"{year}-09-25"),
             "EventFormat": "sprint_qualifying"},
            {"RoundNumber": 3, "EventName": "Gamma Grand Prix", "OfficialEventName": "FORMULA 1 GAMMA GP",
             "Location": "Gamma", "Country": "Gammaland", "EventDate": pd.Timestamp(f"{year}-11-30"),
             "EventFormat": "conventional"},
        ]
    )


def messages_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Time": pd.Timestamp("2024-03-02 15:03:00"), "Category": "Flag", "Message": "GREEN LIGHT - PIT EXIT OPEN",
             "Status": None, "Flag": "GREEN", "Scope": "Track", "Sector": float("nan"),
             "RacingNumber": None, "Lap": 1.0},
            {"Time": pd.Timestamp("2024-03-02 15:40:12"), "Category": "Other", "Message": "CAR 99 UNDER INVESTIGATION",
             "Status": None, "Flag": None, "Scope": None, "Sector": float("nan"),
             "RacingNumber": "99", "Lap": 12.0},
            {"Time": pd.Timestamp("2024-03-02 14:50:00"), "Category": "Other", "Message": "RISK OF RAIN 10%",
             "Status": None, "Flag": None, "Scope": None, "Sector": float("nan"),
             "RacingNumber": None, "Lap": float("nan")},
        ]
    )


def status_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Time": pd.Timedelta("0:05:12.500"), "Status": "1", "Message": "AllClear"},
            {"Time": pd.Timedelta("1:02:03.045"), "Status": "4",
             "Message": "SCDeployed"},
        ]
    )


class FakeSession:
    def __init__(self, messages: pd.DataFrame | None = None, status: pd.DataFrame | None = None,
                 fail_loads: int = 0) -> None:
        self._messages = messages
        self._status = status
        self.fail_loads = fail_loads
        self.load_calls: list[dict] = []

    def load(self, **kwargs) -> None:
        self.load_calls.append(kwargs)
        if self.fail_loads > 0:
            self.fail_loads -= 1
            raise ConnectionError("C:\\secret\\cache\\path unreachable")

    @property
    def race_control_messages(self) -> pd.DataFrame:
        return self._messages if self._messages is not None else pd.DataFrame(
            columns=["Time", "Category", "Message", "Status", "Flag", "Scope", "Sector", "RacingNumber", "Lap"])

    @property
    def track_status(self) -> pd.DataFrame:
        return self._status if self._status is not None else pd.DataFrame(columns=["Time", "Status", "Message"])


class FakeBackend:
    def __init__(self, session: FakeSession | None = None, schedule: pd.DataFrame | None = None) -> None:
        self.session = session or FakeSession(messages_frame(), status_frame())
        self._schedule = schedule
        self.session_requests: list[tuple[int, int]] = []

    def schedule(self, year: int) -> pd.DataFrame:
        return self._schedule if self._schedule is not None else schedule_frame(year)

    def race_session(self, year: int, round: int) -> FakeSession:
        self.session_requests.append((year, round))
        return self.session


def make(backend: FakeBackend | None = None) -> dict:
    return build_tools(backend or FakeBackend(), retry_pause=0)


def assert_valid_result(result: ToolResult) -> None:
    assert isinstance(result, ToolResult)
    assert result.sha256 == canonical_sha256(result.payload)
    assert not re.search(r"[A-Za-z]:\\|/home/|/Users/|/tmp/", result.text)


# ---------------------------------------------------------------- registry


def test_build_tools_names_and_interface():
    tools = make()
    assert set(tools) == {"list_events", "race_control_messages", "track_status", "interval"}
    for name, tool in tools.items():
        assert isinstance(tool, Tool)
        assert tool.name == name
        assert tool.description
        assert tool.input_schema["type"] == "object"


def test_default_build_uses_fastf1_backend():
    assert isinstance(build_tools()["list_events"].backend, tools_mod.FastF1Backend)


def test_requests_get_a_default_timeout(monkeypatch):
    seen = {}

    def fake_send(self, request, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise requests.ConnectionError("stop here")

    monkeypatch.setattr(requests.Session, "send", fake_send)
    with pytest.raises(requests.ConnectionError):
        requests.Session().get("http://example.invalid/")
    assert seen["timeout"] == (30, 120)
    with pytest.raises(requests.ConnectionError):
        requests.Session().get("http://example.invalid/", timeout=5)
    assert seen["timeout"] == 5


# ---------------------------------------------------------------- list_events


def test_list_events_rows_and_text():
    result = make()["list_events"].call({"year": 2024}, AS_OF)
    assert_valid_result(result)
    events = result.payload["events"]
    assert events[0] == {
        "round": 1, "event_name": "Alpha Grand Prix", "official_event_name": "FORMULA 1 ALPHA GP",
        "location": "Alphaville", "country": "Alphaland", "event_date": "2024-03-02",
        "event_format": "conventional",
    }
    assert len(events) == 3
    lines = result.text.splitlines()
    assert "1 | Alpha Grand Prix | FORMULA 1 ALPHA GP | Alphaville | Alphaland | 2024-03-02 | conventional" in lines


def test_list_events_allowed_for_any_year():
    result = make()["list_events"].call({"year": 2030}, AS_OF)
    assert result.payload["year"] == 2030


def test_list_events_empty_schedule_is_unavailable():
    backend = FakeBackend(schedule=schedule_frame().iloc[0:0])
    with pytest.raises(DataUnavailable):
        make(backend)["list_events"].call({"year": 2024}, AS_OF)


def test_list_events_schedule_failure_retries_then_fetch_error():
    class Failing(FakeBackend):
        calls = 0

        def schedule(self, year):
            Failing.calls += 1
            raise ConnectionError("down")

    with pytest.raises(FetchError):
        make(Failing())["list_events"].call({"year": 2024}, AS_OF)
    assert Failing.calls == 2


# ---------------------------------------------------------------- as-of guard


@pytest.mark.parametrize("tool", ["race_control_messages", "track_status"])
def test_as_of_guard_blocks_race_on_as_of_date(tool):
    backend = FakeBackend()
    with pytest.raises(AsOfViolation):
        make(backend)[tool].call({"year": 2026, "round": 2}, AS_OF)  # dated 2026-09-25
    assert backend.session_requests == []


@pytest.mark.parametrize("tool", ["race_control_messages", "track_status"])
def test_as_of_guard_blocks_race_after_as_of(tool):
    with pytest.raises(AsOfViolation):
        make()[tool].call({"year": 2026, "round": 3}, AS_OF)


@pytest.mark.parametrize("tool", ["race_control_messages", "track_status"])
def test_as_of_guard_allows_race_before_as_of(tool):
    make()[tool].call({"year": 2026, "round": 1}, AS_OF)


@pytest.mark.parametrize("tool", ["race_control_messages", "track_status"])
def test_unknown_round_is_unavailable(tool):
    with pytest.raises(DataUnavailable):
        make()[tool].call({"year": 2024, "round": 42}, AS_OF)


# ---------------------------------------------------------------- race_control_messages


def test_race_control_messages_compact_and_rows():
    backend = FakeBackend()
    result = make(backend)["race_control_messages"].call({"year": 2024, "round": 1}, AS_OF)
    assert_valid_result(result)
    assert backend.session.load_calls == [dict(laps=False, telemetry=False, weather=False, messages=True)]
    msgs = result.payload["messages"]
    assert len(msgs) == 3  # nothing filtered, order kept
    assert msgs[0] == {"time": "2024-03-02T15:03:00", "lap": 1, "category": "Flag",
                       "message": "GREEN LIGHT - PIT EXIT OPEN", "flag": "GREEN", "scope": "Track"}
    assert msgs[2]["lap"] is None and msgs[2]["flag"] is None
    lines = result.text.splitlines()
    assert lines[1:] == [
        "1 | Flag | GREEN LIGHT - PIT EXIT OPEN",
        "12 | Other | CAR 99 UNDER INVESTIGATION",
        "- | Other | RISK OF RAIN 10%",
    ]


def test_empty_messages_means_fetch_failed():
    backend = FakeBackend(FakeSession(messages=None, status=status_frame()))
    with pytest.raises(FetchError):
        make(backend)["race_control_messages"].call({"year": 2024, "round": 1}, AS_OF)
    assert len(backend.session.load_calls) == 2  # retried once


def test_failed_load_is_retried_once_then_succeeds():
    backend = FakeBackend(FakeSession(messages_frame(), status_frame(), fail_loads=1))
    result = make(backend)["race_control_messages"].call({"year": 2024, "round": 1}, AS_OF)
    assert len(result.payload["messages"]) == 3
    assert len(backend.session.load_calls) == 2


def test_failed_load_twice_is_fetch_error_without_paths():
    backend = FakeBackend(FakeSession(messages_frame(), status_frame(), fail_loads=2))
    with pytest.raises(FetchError) as info:
        make(backend)["race_control_messages"].call({"year": 2024, "round": 1}, AS_OF)
    assert "secret" not in str(info.value)
    assert len(backend.session.load_calls) == 2


# ---------------------------------------------------------------- track_status


def test_track_status_compact_and_rows():
    backend = FakeBackend()
    result = make(backend)["track_status"].call({"year": 2024, "round": 1}, AS_OF)
    assert_valid_result(result)
    assert backend.session.load_calls == [dict(laps=True, telemetry=False, weather=False, messages=False)]
    assert result.payload["track_status"] == [
        {"time": "0:05:12.500", "status": "1", "message": "AllClear"},
        {"time": "1:02:03.045", "status": "4", "message": "SCDeployed"},
    ]
    assert result.text.splitlines()[1:] == ["0:05:12.500 | 1 | AllClear", "1:02:03.045 | 4 | SCDeployed"]


def test_empty_track_status_means_fetch_failed():
    backend = FakeBackend(FakeSession(messages=messages_frame(), status=None))
    with pytest.raises(FetchError):
        make(backend)["track_status"].call({"year": 2024, "round": 1}, AS_OF)


def test_sha256_changes_with_payload():
    a = make()["track_status"].call({"year": 2024, "round": 1}, AS_OF)
    b = make(FakeBackend(FakeSession(messages_frame(), status_frame().iloc[:1])))["track_status"].call(
        {"year": 2024, "round": 1}, AS_OF)
    assert a.sha256 != b.sha256


# ---------------------------------------------------------------- interval


@pytest.mark.parametrize(
    "k,n,value,low,high",
    [(4, 7, 0.571, 0.289, 0.814), (0, 5, 0.0, 0.0, 0.351), (5, 5, 1.0, 0.649, 1.0), (1, 1, 1.0, 0.27, 1.0)],
)
def test_interval_values(k, n, value, low, high):
    result = make()["interval"].call({"k": k, "n": n}, AS_OF)
    assert_valid_result(result)
    assert result.payload == {"k": k, "n": n, "value": value, "interval": [low, high], "method": "wilson_90"}


def test_interval_text():
    result = make()["interval"].call({"k": 4, "n": 7}, AS_OF)
    assert result.text == "k=4 n=7 value=0.571 interval=[0.289, 0.814] method=wilson_90"


def test_interval_n_zero_is_unavailable():
    with pytest.raises(DataUnavailable):
        make()["interval"].call({"k": 0, "n": 0}, AS_OF)


@pytest.mark.parametrize("args", [{"k": 3, "n": 2}, {"k": -1, "n": 2}, {"k": "x", "n": 2}, {"n": 2}])
def test_interval_bad_args(args):
    with pytest.raises(DataUnavailable):
        make()["interval"].call(args, AS_OF)


# ---------------------------------------------------------------- descriptions


def test_descriptions_state_returns_only():
    for tool in make().values():
        assert "Returns" in tool.description
        assert not re.search(r"\b(look for|check whether|should|safety car|neutraliz)", tool.description, re.I)


# ---------------------------------------------------------------- optional smoke test


@pytest.mark.skipif(not os.environ.get("FASTF1_CACHE_DIR"), reason="FASTF1_CACHE_DIR not set")
def test_smoke_cached_race():
    tools = build_tools()
    schedule = tools["list_events"].call({"year": 2023}, AS_OF)
    assert_valid_result(schedule)
    first = schedule.payload["events"][0]["round"]
    rcm = tools["race_control_messages"].call({"year": 2023, "round": first}, AS_OF)
    assert_valid_result(rcm)
    assert rcm.payload["messages"]
    ts = tools["track_status"].call({"year": 2023, "round": first}, AS_OF)
    assert_valid_result(ts)
    assert ts.payload["track_status"]

"""Synthetic fixtures for runner tests. Values are invented, never from the key."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from adaptive_harness.contracts import HarnessConfig, ResolvedModel, Task, load_config
from adaptive_harness.runner.coordinator import Coordinator
from adaptive_harness.testing import FakeModel, FakeStore, FakeTool, FakeTools

TASK = Task(id="T1", set="development", type="venue_brief", target={"year": 2030, "round": 1},
            as_of=date(2031, 1, 1), question="How often at the venue?")
AUDIT = Task(id="T2", set="development", type="race_audit", target={"year": 2030, "round": 2},
             as_of=date(2031, 1, 1), question="Was it neutralized?")

RATE = {"value": 0.5, "interval": [0.2, 0.8], "method": "wilson_90"}
BRIEF = {
    "case_id": "T1",
    "venue": {"location": "Testville", "from_target": {"year": 2030, "round": 1}, "evidence": ["e"]},
    "races": [
        {"year": 2029, "round": 1, "location": "Testville", "event_name": "Test Grand Prix",
         "sc": True, "vsc": False, "red_flag": False, "evidence": ["e"]},
        {"year": 2030, "round": 1, "location": "Testville", "event_name": "Test Grand Prix",
         "sc": False, "vsc": False, "red_flag": False, "evidence": ["e"]},
    ],
    "counts": {"n_races": 2, "n_sc": 1, "n_vsc": 0, "n_any": 1},
    "rates": {"sc": RATE, "vsc": RATE, "any": RATE},
    "disposition": "GO",
    "thin_sample": True,
    "hold_reason": None,
    "open_objections": [],
}

ASSIGN = {"race_engineer": "re-model", "statistician": "stat-model", "data_engineer": "de-model",
          "improvement_agent": "imp-model"}
RESOLVED = ResolvedModel(provider="fake", base_url=None, assignment=ASSIGN)


def note(summary: str, objections: Sequence[str] = (), **data: Any) -> dict:
    return {"action": "finish", "output": {"summary": summary, "objections": list(objections), "data": data}}


def call(tool: str, **args: Any) -> dict:
    return {"action": "call_tool", "tool": tool, "args": args, "why": "need it"}


def finish(output: dict) -> dict:
    return {"action": "finish", "output": output}


class RoleModel:
    """One scripted FakeModel per role, so concurrent stages get deterministic replies."""

    def __init__(self, scripts: Mapping[str, Iterable[Any]], assignment: Mapping[str, str] = ASSIGN) -> None:
        self.models = {role: FakeModel(script, assignment=assignment) for role, script in scripts.items()}

    def complete(self, role, system, messages, json_schema=None):
        if role not in self.models:
            self.models[role] = FakeModel([], assignment=ASSIGN)
        return self.models[role].complete(role, system, messages, json_schema)

    def calls(self, role: str) -> list[dict]:
        return self.models[role].calls


def standard_scripts(brief: dict | None = None) -> dict[str, list]:
    return {
        "race_engineer": [note("framed", ["define the venue"]), note("challenge re"), finish(brief or BRIEF)],
        "statistician": [note("S2A-MARKER plan"), note("challenge stat"), call("interval", k=1, n=2), note("rates done")],
        "data_engineer": [note("S2B-MARKER plan"), note("challenge de"), call("list_events", year=2030),
                          note("gathered")],
    }


def fake_tools(**overrides: Any) -> FakeTools:
    tools = FakeTools()
    tools.add(FakeTool("list_events", default={"year": 2030, "events": [
        {"round": 1, "event_name": "Test Grand Prix", "location": "Testville"}]}))
    tools.add(FakeTool("race_control_messages", default={"messages": []}))
    tools.add(FakeTool("track_status", default={"status": []}))
    tools.add(FakeTool("interval", default={"value": 0.5, "interval": [0.2, 0.8]}))
    for name, tool in overrides.items():
        tools[name] = tool
    return tools


def config(**changes: Any) -> HarnessConfig:
    d = copy.deepcopy(load_config().to_dict())
    for key, value in changes.items():
        cur = d
        parts = key.split("__")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = value
    return HarnessConfig.model_validate(d)


def run_case(scripts=None, *, cfg: HarnessConfig | None = None, tools=None, task: Task = TASK,
             store: FakeStore | None = None, snapshot: str = "M0", embedder=None, model=None):
    store = store or FakeStore()
    model = model or RoleModel(scripts if scripts is not None else standard_scripts())
    co = Coordinator(cfg or load_config(), store, model, tools if tools is not None else fake_tools(),
                     RESOLVED, "0.0-test", embedder=embedder)
    run_id = co.run(task, snapshot=snapshot, arm="baseline", experiment_id="exp-test")
    run = store.get("runs", run_id)
    events = store.find("events", {"run_id": run_id}, sort=[("seq", 1)])
    return run, events, model, store

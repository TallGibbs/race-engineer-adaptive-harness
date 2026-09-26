"""The coordinator: runs one case under one configuration (CONTRACT.md sections A and G).

Stages run in config order. Consecutive stages sharing a parallel_group run concurrently,
and a multi-role stage (S3) gives each role one concurrent response; blindness comes from
the context policy. Tool stages use the JSON action protocol with the coordinator
executing tools. S7 is code: it validates the S6 output and runs the enabled checks.
Every model call, tool call, tool result, message, decision, check, and error is an event.
"""

from __future__ import annotations

import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from ..contracts.config import HarnessConfig, ResolvedModel
from ..contracts.errors import (
    AsOfViolation,
    BudgetExceeded,
    DataUnavailable,
    FetchError,
    MeasurementSystemFailure,
)
from ..contracts.interfaces import ModelClient, Store
from ..contracts.protocol import CallTool, Finish, StageNote, Task, parse_stage_reply
from ..contracts.records import Run, RunModel
from . import checks, prompts
from .context import Context, ContextBuilder, Embedder, StageRecord
from .models import BAD_STOP_REASONS, extract_json
from .recorder import Recorder, now

OUTPUT_STAGE = "S6"
VALIDATE_STAGE = "S7"


class InvalidReply(ValueError):
    pass


@dataclass
class Conversation:
    stage_id: str
    role: str
    ctx: Context
    messages: list[dict[str, str]] = field(default_factory=list)
    sent: int = 0  # messages already recorded on an earlier model_call event
    refs: list[str] = field(default_factory=list)


def new_run_id(case_id: str) -> str:
    return f"{case_id}-{now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def stage_groups(config: HarnessConfig) -> list[list[str]]:
    """Stages in config order, with consecutive stages of one parallel_group grouped."""
    groups: list[list[str]] = []
    last_group = None
    for s in config.stages:
        sdef = config.stage_catalog.get(s) or config.optional_stage_catalog[s]
        g = sdef.parallel_group
        if g is not None and g == last_group:
            groups[-1].append(s)
        else:
            groups.append([s])
        last_group = g
    return groups


class Coordinator:
    def __init__(
        self,
        config: HarnessConfig,
        store: Store,
        model: ModelClient,
        tools: Mapping[str, Any],
        resolved: ResolvedModel,
        fastf1_version: str,
        embedder: Embedder | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.model = model
        self.tools = tools
        self.resolved = resolved
        self.fastf1_version = fastf1_version
        self.embedder = embedder

    def run(
        self,
        task: Task,
        snapshot: str = "M0",
        arm: str = "baseline",
        experiment_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        return _CaseRun(self, task, snapshot, arm, experiment_id, run_id or new_run_id(task.id)).execute()


class _CaseRun:
    def __init__(self, co: Coordinator, task: Task, snapshot: str, arm: str, experiment_id: str | None, run_id: str):
        self.co = co
        self.config = co.config
        self.task = task
        self.snapshot = snapshot
        self.arm = arm
        self.experiment_id = experiment_id
        self.run_id = run_id
        self.records: dict[str, StageRecord] = {}
        self.rec = Recorder(co.store, run_id, co.config.budgets)
        self.builder = ContextBuilder(co.config, task, co.tools, co.store, snapshot, self.records, co.embedder)
        self.status: str | None = None
        self.output: dict[str, Any] | None = None

    # ------------------------------------------------------------ run

    def execute(self) -> str:
        run = Run(
            _id=self.run_id,
            harness_version=self.config.version_id,
            config_hash=self.config.config_hash(),
            memory_snapshot=self.snapshot,
            case_id=self.task.id,
            arm=self.arm,
            experiment_id=self.experiment_id,
            model=RunModel(provider=self.co.resolved.provider, assignment=self.co.resolved.assignment),
            fastf1_version=self.co.fastf1_version,
            started_at=now(),
        )
        self.co.store.insert("runs", run.to_doc())
        try:
            for group in stage_groups(self.config):
                self._run_group(group)
        except BudgetExceeded as e:
            self._fatal("budget_exceeded", e)
        except MeasurementSystemFailure as e:
            self._fatal("harness_error", e)
        except Exception as e:  # a harness bug: never a process defect
            self._fatal("harness_error", e, detail=traceback.format_exc())
        if self.status is None:
            self.status = "harness_error"
        self.co.store.update("runs", self.run_id, {
            "ended_at": now(),
            "status": self.status,
            "totals": self.rec.final_totals().model_dump(),
            "output": self.output,
        })
        return self.run_id

    def _fatal(self, status: str, e: BaseException, detail: str | None = None) -> None:
        self.status = status
        content = {"error": type(e).__name__, "message": str(e), "status": status}
        if detail:
            content["traceback"] = detail
        self.rec.emit("error", content)

    def _run_group(self, group: list[str]) -> None:
        if len(group) == 1:
            self._run_stage(group[0])
            return
        with ThreadPoolExecutor(max_workers=len(group)) as pool:
            futures = [pool.submit(self._run_stage, s) for s in group]
            errors = []
            for f in futures:
                try:
                    f.result()
                except BaseException as e:  # wait for every member before failing
                    errors.append(e)
            if errors:
                raise errors[0]

    def _run_stage(self, stage_id: str) -> None:
        if stage_id == VALIDATE_STAGE:
            self._validate()
            return
        sdef = self.builder.stage_def(stage_id)
        record = StageRecord(stage_id)
        roles = list(sdef.roles)
        if len(roles) == 1:
            results = [self._run_role(stage_id, roles[0], record)]
        else:
            with ThreadPoolExecutor(max_workers=len(roles)) as pool:
                futures = [pool.submit(self._run_role, stage_id, r, record) for r in roles]
                results, errors = [], []
                for f in futures:
                    try:
                        results.append(f.result())
                    except BaseException as e:
                        errors.append(e)
                if errors:
                    raise errors[0]
        for role, res in zip(roles, results):
            if res is not None:
                record.notes.append((role, res[0], res[1]))
        self.records[stage_id] = record

    # ------------------------------------------------------------ one role in one stage

    def _run_role(self, stage_id: str, role: str, record: StageRecord) -> tuple[dict[str, Any], str] | None:
        sdef = self.builder.stage_def(stage_id)
        ctx = self.builder.build(role, stage_id)
        conv = Conversation(stage_id, role, ctx, [{"role": "user", "content": ctx.user}], refs=list(ctx.refs))
        while True:
            got = self._ask(conv, bool(sdef.tools))
            if got is None:
                record.errors.append(f"{role}: no valid reply")
                return None
            reply, call_id = got
            if isinstance(reply, Finish):
                msg_id = self.rec.emit("message", reply.output, stage_id=stage_id, role=role, refs=[call_id])
                if stage_id == OUTPUT_STAGE:
                    self.output = reply.output
                return reply.output, msg_id
            self._call_tool(conv, reply, call_id, list(sdef.tools), record)

    def _ask(self, conv: Conversation, tool_stage: bool) -> tuple[CallTool | Finish, str] | None:
        """One model call, plus one repair retry when the reply is not valid JSON for the protocol."""
        for attempt in ("initial", "repair"):
            self.rec.reserve_model_call()
            resp = self.co.model.complete(conv.role, conv.ctx.system, conv.messages)
            content: dict[str, Any] = {
                "attempt": attempt,
                "input": conv.messages[conv.sent:],
                "response": resp.text,
                "latency_ms": resp.latency_ms,
            }
            if conv.sent == 0:
                content["system"] = conv.ctx.system
            call_id = self.rec.emit(
                "model_call",
                content,
                stage_id=conv.stage_id,
                role=conv.role,
                refs=conv.refs,
                context_manifest=conv.ctx.manifest,
                usage=resp.usage.model_dump(),
                model=resp.model,
                stop_reason=resp.stop_reason,
            )
            self.rec.add_usage(resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cache_read_tokens)
            conv.messages.append({"role": "assistant", "content": resp.text})
            conv.sent = len(conv.messages)
            self.rec.check_wall()
            if resp.stop_reason in BAD_STOP_REASONS:
                self.rec.emit("error", {"error": "stop_reason", "stop_reason": resp.stop_reason,
                                        "message": "the reply was refused or cut off and was not read"},
                              stage_id=conv.stage_id, role=conv.role, refs=[call_id])
                return None
            try:
                reply = self._parse(resp.parsed if isinstance(resp.parsed, dict) else extract_json(resp.text),
                                    conv.stage_id, tool_stage)
                return reply, call_id
            except InvalidReply as e:
                self.rec.emit("error", {"error": "invalid_reply", "attempt": attempt, "message": str(e)},
                              stage_id=conv.stage_id, role=conv.role, refs=[call_id])
                if attempt == "initial":
                    conv.messages.append({"role": "user", "content": f"{e} {prompts.REPAIR}"})
        return None

    def _parse(self, obj: Any, stage_id: str, tool_stage: bool) -> CallTool | Finish:
        if not isinstance(obj, dict):
            raise InvalidReply("Your reply was not a JSON object.")
        try:
            reply = parse_stage_reply(obj)
        except ValidationError as e:
            raise InvalidReply(f"Your reply did not follow the protocol ({_brief_errors(e)}).") from None
        if isinstance(reply, CallTool) and not tool_stage:
            raise InvalidReply("This stage has no tools; reply with a finish action.")
        if isinstance(reply, Finish) and stage_id != OUTPUT_STAGE:
            try:
                StageNote.model_validate(reply.output)
            except ValidationError as e:
                raise InvalidReply(f"Your output is not a valid stage note ({_brief_errors(e)}).") from None
        return reply

    def _call_tool(self, conv: Conversation, reply: CallTool, call_id: str, allowed: list[str], record: StageRecord) -> None:
        stage_id, role = conv.stage_id, conv.role
        tool = self.co.tools.get(reply.tool) if reply.tool in allowed else None
        if tool is None:
            err = self.rec.emit("error", {"error": "tool_not_available", "tool": reply.tool, "allowed": allowed},
                                stage_id=stage_id, role=role, refs=[call_id])
            conv.messages.append({"role": "user", "content": f"Error (event {err}): tool {reply.tool!r} is not "
                                  f"available in this stage. Available: {', '.join(allowed)}."})
            conv.refs.append(err)
            return
        self.rec.reserve_tool_call()
        tc_id = self.rec.emit("tool_call", {"tool": reply.tool, "args": reply.args, "why": reply.why},
                              stage_id=stage_id, role=role, refs=[call_id])
        try:
            result = tool.call(reply.args, self.task.as_of)
        except FetchError as e:
            self.rec.emit("error", {"error": "FetchError", "tool": reply.tool, "args": reply.args, "message": str(e)},
                          stage_id=stage_id, role=role, refs=[tc_id])
            raise
        except DataUnavailable as e:
            content = {"tool": reply.tool, "args": reply.args, "error": "DataUnavailable", "text": str(e)}
            tr_id = self.rec.emit("tool_result", content, stage_id=stage_id, role=role, refs=[tc_id])
            record.tool_results.append((tr_id, reply.tool, reply.args))
            text = f"tool_result event {tr_id} ({reply.tool}): data unavailable: {e}"
            ref = tr_id
        except MeasurementSystemFailure:
            raise
        except Exception as e:
            kind = type(e).__name__
            ref = self.rec.emit("error", {"error": kind, "tool": reply.tool, "args": reply.args, "message": str(e)},
                                stage_id=stage_id, role=role, refs=[tc_id])
            text = f"Error (event {ref}) from {reply.tool}: {kind}: {e}"
            if isinstance(e, AsOfViolation):
                text = f"Error (event {ref}): the request reaches past the case's as-of date: {e}"
        else:
            content = {"tool": reply.tool, "args": reply.args, "text": result.text,
                       "payload": result.payload, "sha256": result.sha256}
            tr_id = self.rec.emit("tool_result", content, stage_id=stage_id, role=role, refs=[tc_id])
            record.tool_results.append((tr_id, reply.tool, reply.args))
            text = f"tool_result event {tr_id} ({reply.tool}):\n{result.text}"
            ref = tr_id
        conv.messages.append({"role": "user", "content": text})
        conv.refs.append(ref)

    # ------------------------------------------------------------ S7

    def _validate(self) -> None:
        brief = self.output
        s6 = self.records.get(OUTPUT_STAGE)
        refs = [ev for _, _, ev in s6.notes] if s6 else []
        tool_results = self.rec.find(type="tool_result")
        tr_ids = [e["_id"] for e in tool_results]
        contents = [e["content"] for e in tool_results]
        cfg = self.config.checks
        results: list[tuple[str, bool, list[str], list[str]]] = []
        ok, why = checks.schema_check(brief, self.task.type)
        results.append(("schema", ok, why, refs))
        if ok:
            if cfg.venue_match.enabled:
                results.append(("venue_match", *checks.venue_match(brief, self.task.type, contents), refs + tr_ids))
            if cfg.source_agreement.enabled:
                results.append(("source_agreement", *checks.source_agreement(
                    brief, self.task.type, contents, cfg.source_agreement.tolerance_laps), refs + tr_ids))
            if cfg.min_races_for_rate.enabled:
                results.append(("min_races_for_rate", *checks.min_races_for_rate(
                    brief, self.task.type, cfg.min_races_for_rate.value), refs))
        check_ids = []
        reasons: list[str] = []
        for name, passed, why, crefs in results:
            check_ids.append(self.rec.emit("check", {"check": name, "passed": passed, "reasons": why},
                                           stage_id=VALIDATE_STAGE, refs=crefs))
            reasons.extend(f"{name}: {w}" for w in why)
        if reasons:
            self.status = "hold"
            decision = {"status": "hold", "disposition": "HOLD", "reasons": reasons}
        else:
            disposition = brief.get("disposition", "GO") if isinstance(brief, dict) else "GO"
            self.status = "hold" if disposition == "HOLD" else "completed"
            decision = {"status": self.status, "disposition": disposition, "reasons": []}
        self.rec.emit("decision", decision, stage_id=VALIDATE_STAGE, refs=check_ids)


def _brief_errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()[:5])

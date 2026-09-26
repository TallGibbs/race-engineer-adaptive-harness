"""Store lane CLI: `init-db`, and `show run <id>` for the demo."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable

from ..contracts.errors import DuplicateKey
from ..contracts.interfaces import Store
from ..contracts.records import Event, HarnessVersion, Run
from .mongo import MongoStore, StoreConnectionError
from .records import events_for_run, get_run, get_version, pin_version, pinned_version, save_version, utcnow


def register_baseline(store: Store, log=print) -> None:
    """Register configs/v1.json as harness version v1 (baseline, pinned) if absent.

    When v1 exists nothing is overwritten; if no version is pinned at all, v1 is pinned.
    """
    from ..contracts.paths import load_config

    config = load_config()
    existing = get_version(store, config.version_id)
    if existing is None:
        try:
            save_version(store, HarnessVersion(
                _id=config.version_id,
                parent_id=config.parent_id,
                config=config.to_dict(),
                config_hash=config.config_hash(),
                status="baseline",
                pinned=pinned_version(store) is None,
                created_at=utcnow(),
            ))
            log(f"registered harness version {config.version_id} (config {config.config_hash()[:12]})")
        except DuplicateKey:
            log(f"harness version {config.version_id} already registered")
    else:
        if existing.config_hash != config.config_hash():
            log(f"warning: stored {config.version_id} config_hash differs from configs/{config.version_id}.json")
        log(f"harness version {config.version_id} already registered")
    if pinned_version(store) is None:
        pin_version(store, config.version_id)
    log(f"pinned version: {pinned_version(store).id}")


def init_db(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m adaptive_harness init-db",
                                     description="Create collections, indexes, and the lessons_vector index; "
                                                 "register v1 as the pinned baseline.")
    parser.add_argument("--wait", type=float, default=300.0, help="seconds to wait for the vector index (default 300)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    log = (lambda msg: None) if args.quiet else (lambda msg: print(msg, flush=True))
    try:
        store = MongoStore.from_env()
    except StoreConnectionError as e:
        print(f"init-db: {e}", file=sys.stderr)
        return 1
    try:
        log(f"database: {store.db.name}")
        store.init(wait_seconds=args.wait, log=log)
        register_baseline(store, log=log)
    except TimeoutError as e:
        print(f"init-db: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    log("init-db: done")
    return 0


# ---------------------------------------------------------------- show run


def _compact(value: Any, width: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def _event_body(ev: Event) -> Any:
    c = ev.content
    if isinstance(c, dict):
        if ev.type == "tool_call" and "tool" in c:
            return f"{c['tool']}({_compact(c.get('args'), 10_000)})"
        for key in ("text", "summary", "message", "decision", "result"):
            if isinstance(c.get(key), str):
                return c[key]
    return c


def format_run(run: Run | None, events: Iterable[Event], width: int = 110) -> list[str]:
    lines: list[str] = []
    if run is not None:
        lines.append(
            f"run {run.id}  case {run.case_id}  version {run.harness_version}  memory {run.memory_snapshot}  "
            f"arm {run.arm}  status {run.status or 'running'}"
        )
        t = run.totals
        lines.append(
            f"  model_calls {t.model_calls}  tool_calls {t.tool_calls}  tokens in/out {t.input_tokens}/{t.output_tokens}"
            f"  wall {t.wall_seconds:.1f}s"
        )
    for ev in events:
        head = f"{ev.seq:>5} {ev.stage_id or '-':<4} {ev.role or '-':<14} {ev.type:<11}"
        extra = []
        if ev.model:
            extra.append(f"[{ev.model}]")
        if ev.refs:
            extra.append("refs " + ",".join(r.rsplit(":", 1)[-1] for r in ev.refs))
        prefix = head + (" " + " ".join(extra) if extra else "")
        body = _compact(_event_body(ev), max(20, width - len(prefix) - 1))
        lines.append(f"{prefix} {body}".rstrip())
    return lines


def show_run(store: Store, run_id: str, width: int = 110) -> list[str]:
    run = get_run(store, run_id)
    events = events_for_run(store, run_id)
    if run is None and not events:
        raise KeyError(f"no run {run_id!r}")
    return format_run(run, events, width=width)


def show(argv: list[str]) -> int:
    """`show run <id>`: print a run's events in order, compactly.

    The `show` subcommand is dispatched to the report lane; its handler delegates
    `show run` here.
    """
    parser = argparse.ArgumentParser(prog="python -m adaptive_harness show")
    parser.add_argument("kind", choices=["run"])
    parser.add_argument("id")
    parser.add_argument("--width", type=int, default=110)
    args = parser.parse_args(argv)
    try:
        store = MongoStore.from_env()
    except StoreConnectionError as e:
        print(f"show: {e}", file=sys.stderr)
        return 1
    try:
        for line in show_run(store, args.id, width=args.width):
            print(line)
    except KeyError as e:
        print(f"show: {e.args[0]}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0

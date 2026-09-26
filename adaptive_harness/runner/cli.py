"""`python -m adaptive_harness run`: run cases under a harness version.

    python -m adaptive_harness run --config configs/v1.json --snapshot M0 --cases D1,H1,P1
        [--arm baseline] [--experiment ID] [--store mongo|memory] [--tools real|synthetic]

--config takes a file path, a version id from the store (v1, v2), or "pinned".
The shared dispatcher (adaptive_harness/__main__.py) currently rejects a flag as the
first argument after the subcommand, so until it is fixed pass `--` first:
`python -m adaptive_harness run -- --config ...`. Direct calls to run(argv) accept both.

Cases run concurrently, at most three at once. Prints the resolved role-to-model
assignment at startup, then one line per run: run id, case, status.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path
from typing import Any

from ..contracts.config import HarnessConfig, resolve_model
from ..contracts.paths import CONFIGS_DIR, load_config
from ..contracts.records import HarnessVersion
from .coordinator import Coordinator
from .recorder import now

MAX_CONCURRENT_CASES = 3


def fastf1_version() -> str:
    try:
        return metadata.version("fastf1")
    except metadata.PackageNotFoundError:
        return "not installed"


def open_store(kind: str) -> Any:
    if kind == "memory":
        from ..testing import FakeStore

        store = FakeStore()
        store.init()
        v1 = load_config(CONFIGS_DIR / "v1.json")
        store.insert("harness_versions", HarnessVersion(
            _id=v1.version_id, parent_id=None, config=v1.to_dict(), config_hash=v1.config_hash(),
            status="baseline", pinned=True, created_at=now(),
        ).to_doc())
        return store
    from .. import store as store_lane

    for name in ("open_store", "get_store", "MongoStore"):
        factory = getattr(store_lane, name, None)
        if factory is not None:
            return factory()
    raise SystemExit("the MongoDB store is not available yet (lane store); use --store memory")


def open_tools(kind: str) -> Any:
    if kind == "synthetic":
        from .synthetic import synthetic_tools

        return synthetic_tools()
    try:
        from examples.neutralization_brief.tools import build_tools
    except ImportError:
        raise SystemExit("the real tools are not available yet (lane tools); use --tools synthetic") from None
    return build_tools()


def resolve_config(spec: str, store: Any) -> HarnessConfig:
    path = Path(spec)
    if path.suffix == ".json" or path.exists():
        return load_config(path)
    doc = store.pinned_version() if spec == "pinned" else store.get("harness_versions", spec)
    if doc is None:
        raise SystemExit(f"no harness version {spec!r} in the store")
    return HarnessConfig.model_validate(doc["config"])


def dump_run(store: Any, run_id: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    doc = {"run": store.get("runs", run_id),
           "events": store.find("events", {"run_id": run_id}, sort=[("seq", 1)])}
    path = directory / f"{run_id}.json"
    path.write_text(json.dumps(doc, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    if argv[:1] == ["--"]:  # `python -m adaptive_harness run -- --config ...` (see run's docstring)
        argv = argv[1:]
    p = argparse.ArgumentParser(prog="python -m adaptive_harness run", description="Run cases under a harness version.")
    p.add_argument("--config", required=True, help="file path, version id (v1, v2), or 'pinned'")
    p.add_argument("--snapshot", required=True, choices=["M0", "M1"])
    p.add_argument("--cases", required=True, help="comma-separated case ids, e.g. D1,H1,P1")
    p.add_argument("--arm", default="baseline",
                   choices=["baseline", "repeat", "memory_only", "candidate", "confirmation"])
    p.add_argument("--experiment", default=None)
    p.add_argument("--store", default="mongo", choices=["mongo", "memory"])
    p.add_argument("--tools", default="real", choices=["real", "synthetic"])
    p.add_argument("--dump", default=None, metavar="DIR",
                   help="also write each run and its events as JSON into DIR (useful with --store memory)")
    return p.parse_args(argv)


def run(argv: list[str]) -> int:
    from dotenv import load_dotenv

    from examples.neutralization_brief.task import load_task

    from .models import build_client

    load_dotenv()
    args = parse_args(argv)
    case_ids = [c.strip() for c in args.cases.split(",") if c.strip()]
    tasks = [load_task(c) for c in case_ids]

    store = open_store(args.store)
    config = resolve_config(args.config, store)
    resolved = resolve_model(config.model)
    print(f"harness {config.version_id} ({config.config_hash()[:12]}), provider {resolved.provider}")
    for role, model in resolved.assignment.items():
        print(f"  {role}: {model}")
    sys.stdout.flush()

    coordinator = Coordinator(
        config, store, build_client(resolved), open_tools(args.tools), resolved, fastf1_version()
    )

    def one(task):
        run_id = coordinator.run(task, args.snapshot, args.arm, args.experiment)
        status = (store.get("runs", run_id) or {}).get("status")
        if args.dump:
            dump_run(store, run_id, Path(args.dump))
        print(f"{run_id}\t{task.id}\t{status}", flush=True)
        return status

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CASES) as pool:
        statuses = list(pool.map(one, tasks))
    return 1 if any(s == "harness_error" for s in statuses) else 0

"""CLI handlers of the dmaic lane (dispatched by `python -m adaptive_harness`).

    define  --experiment E
    measure --experiment E [--repeat]
    analyze --experiment E --snapshot M1
    improve --experiment E --from v1 --snapshot M1
    improve --verify --experiment E
    control --experiment E [--confirm]
    cycle   --experiment E [--repeat] [--confirm] [--from VERSION]

Each prints the phase result as JSON. A tollgate that does not pass is a recorded
outcome (exit 0); a missing precondition or a failed external step exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from ..contracts.errors import HarnessError
from .common import PhaseError
from .ports import Deps, PortError, deps_from_env

# Replaced in tests to inject fakes.
build_deps: Callable[[], Deps] = lambda: deps_from_env(log=lambda msg: print(msg, file=sys.stderr))


def _parser(command: str, description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"python -m adaptive_harness {command}", description=description)
    p.add_argument("--experiment", required=True, metavar="EXPERIMENT_ID")
    return p


def _run(fn: Callable[[Deps], dict[str, Any]]) -> int:
    try:
        deps = build_deps()
        result = fn(deps)
    except (PhaseError, PortError, HarnessError) as e:
        print(f"dmaic: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def define(argv: list[str]) -> int:
    from .define import define as run

    args = _parser("define", "DMAIC define: charter, problem statement, tollgate D.").parse_args(argv)
    return _run(lambda d: run(d, args.experiment))


def measure(argv: list[str]) -> int:
    from .measure import measure as run

    p = _parser("measure", "DMAIC measure: re-score, hashes, DPO, Pareto, cost; tollgate M.")
    p.add_argument("--repeat", action="store_true", help="also run the development cases once more (arm repeat)")
    args = p.parse_args(argv)
    return _run(lambda d: run(d, args.experiment, repeat=args.repeat))


def analyze(argv: list[str]) -> int:
    from .analyze import analyze as run

    p = _parser("analyze", "DMAIC analyze: root causes, verified lessons, tollgate A.")
    p.add_argument("--snapshot", required=True, choices=["M0", "M1"])
    args = p.parse_args(argv)
    return _run(lambda d: run(d, args.experiment, args.snapshot))


def improve(argv: list[str]) -> int:
    from .improve import propose, verify

    p = _parser("improve", "DMAIC improve: proposal and candidate; with --verify, tollgate I.")
    p.add_argument("--from", dest="from_version", metavar="VERSION")
    p.add_argument("--snapshot", choices=["M0", "M1"])
    p.add_argument("--verify", action="store_true", help="apply the acceptance rules after the pilot arms ran")
    args = p.parse_args(argv)
    if args.verify:
        return _run(lambda d: verify(d, args.experiment))
    if not args.from_version or not args.snapshot:
        p.error("improve needs --from and --snapshot (or --verify)")
    return _run(lambda d: propose(d, args.experiment, args.from_version, args.snapshot))


def control(argv: list[str]) -> int:
    from .control import control as run

    p = _parser("control", "DMAIC control: pin, control plan; with --confirm, confirmation arm; tollgate C.")
    p.add_argument("--confirm", action="store_true", help="run and check the confirmation arm")
    args = p.parse_args(argv)
    return _run(lambda d: run(d, args.experiment, confirm=args.confirm))


def cycle(argv: list[str]) -> int:
    from .cycle import cycle as run

    p = _parser("cycle", "The whole DMAIC cycle, stopping at the first tollgate that does not pass.")
    p.add_argument("--repeat", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--from", dest="from_version", metavar="VERSION")
    args = p.parse_args(argv)
    return _run(lambda d: run(d, args.experiment, repeat=args.repeat, confirm=args.confirm,
                              from_version=args.from_version))

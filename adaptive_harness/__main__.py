"""CLI: python -m adaptive_harness <subcommand> [args...]

Each subcommand is owned by a lane. The lane implements it by providing a function
`<name>(argv: list[str]) -> int` (hyphens become underscores) in
adaptive_harness/<lane>/cli.py and parses its own arguments there. This file never
needs editing.
"""

from __future__ import annotations

import argparse
import importlib
import sys

# subcommand -> (owning lane, help)
SUBCOMMANDS: dict[str, tuple[str, str]] = {
    "init-db": ("store", "Create MongoDB collections and indexes, and register v1 as the pinned baseline."),
    "run": ("runner", "Run one or more cases under a harness version."),
    "define": ("dmaic", "DMAIC define phase: charter and tollgate D."),
    "measure": ("dmaic", "DMAIC measure phase: measurement validity and tollgate M."),
    "analyze": ("dmaic", "DMAIC analyze phase: root causes, lessons, and tollgate A."),
    "improve": ("dmaic", "DMAIC improve phase: proposal, pilot, and tollgate I."),
    "control": ("dmaic", "DMAIC control phase: pin, control plan, and tollgate C."),
    "cycle": ("dmaic", "Run the whole DMAIC cycle, stopping at the first failed tollgate."),
    "report": ("report", "Write the markdown report for an experiment."),
    "maps": ("report", "Write Mermaid maps of a run or an experiment."),
    "show": ("report", "Show a stored run, version, experiment, or lesson."),
}


class NotImplementedYet(Exception):
    pass


def _handler(name: str):
    lane, _ = SUBCOMMANDS[name]
    func = name.replace("-", "_")
    try:
        module = importlib.import_module(f"adaptive_harness.{lane}.cli")
    except ModuleNotFoundError as e:
        if e.name in (f"adaptive_harness.{lane}.cli", f"adaptive_harness.{lane}"):
            raise NotImplementedYet(name) from None
        raise
    handler = getattr(module, func, None)
    if handler is None:
        raise NotImplementedYet(name)
    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m adaptive_harness",
        description="Race Engineer Adaptive Harness. The evaluator runs as its own module: "
        "python -m adaptive_harness.evaluate",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, (lane, help_text) in SUBCOMMANDS.items():
        p = sub.add_parser(name, help=f"{help_text} [lane {lane}]", add_help=False)
        p.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not ns.command:
        parser.print_help()
        return 0
    try:
        handler = _handler(ns.command)
    except NotImplementedYet:
        lane = SUBCOMMANDS[ns.command][0]
        print(
            f"'{ns.command}' is not implemented yet: owned by lane {lane} "
            f"(adaptive_harness/{lane}/cli.py).",
            file=sys.stderr,
        )
        return 2
    return int(handler(list(ns.args)) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

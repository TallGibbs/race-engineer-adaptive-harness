"""python -m adaptive_harness.evaluate: the fixed evaluator, run as its own process.

  --run <run_id>                  score one run and store its evaluation
  --experiment <id>               score every run of an experiment
  --rescore --run <run_id>        re-score in this process; exit 4 if any verdict differs
  --accept --experiment <id>      apply the acceptance rules R0..R4 (improve tollgate)
  --control --run <run_id>        compare a pinned version's run with its control plan
  --export (--experiment <id> | --run <run_id>)
                                  failure categories and defect counts per run

Exit codes: 0 done, 2 usage or scoring error, 3 key hash mismatch (nothing scored),
4 re-score differs from the stored evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .acceptance import decide
from .control import check_run
from .core import EvaluationError, Evaluator, defect_summary, export
from .keyfile import KeyHashMismatch, load_key

EXIT_OK, EXIT_ERROR, EXIT_KEY_HASH, EXIT_RESCORE_DIFFERS = 0, 2, 3, 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m adaptive_harness.evaluate", description=__doc__.split("\n\n")[0])
    p.add_argument("--run", action="append", default=[], metavar="RUN_ID")
    p.add_argument("--experiment", metavar="EXPERIMENT_ID")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--rescore", action="store_true", help="re-score and compare with the stored evaluation")
    mode.add_argument("--accept", action="store_true", help="apply the acceptance rules to an experiment")
    mode.add_argument("--control", action="store_true", help="control check of a pinned version's run")
    mode.add_argument("--export", action="store_true", help="failure categories and defect counts per run")
    p.add_argument("--force", action="store_true", help="replace an existing evaluation")
    return p


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def main(
    argv: list[str] | None = None,
    *,
    store: Any = None,
    key_path: str | Path | None = None,
    sha_path: str | Path | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    need_run = args.rescore or args.control
    if need_run and len(args.run) != 1:
        parser.error("--rescore and --control take exactly one --run")
    if args.accept and not args.experiment:
        parser.error("--accept needs --experiment")
    if not args.run and not args.experiment:
        parser.error("give --run or --experiment")

    try:
        key = load_key(key_path, sha_path)
    except KeyHashMismatch as e:
        print(f"evaluate: refusing to score: {e}", file=sys.stderr)
        return EXIT_KEY_HASH

    if store is None:
        from dotenv import load_dotenv

        from .store import open_store

        load_dotenv()
        store = open_store()
    ev = Evaluator(store, key)

    try:
        if args.rescore:
            result = ev.rescore(args.run[0])
            _print(result)
            if not result["identical"]:
                print(f"evaluate: re-score differs on {', '.join(result['differing_checks'])}", file=sys.stderr)
                return EXIT_RESCORE_DIFFERS
            return EXIT_OK
        if args.accept:
            _print(decide(ev, args.experiment))
            return EXIT_OK
        if args.control:
            _print(check_run(ev, args.run[0]))
            return EXIT_OK
        if args.export:
            _print(export(store, experiment_id=args.experiment, run_ids=args.run or None))
            return EXIT_OK
        run_ids = list(args.run)
        if args.experiment:
            run_ids += [r["_id"] for r in ev.experiment_runs(args.experiment) if r.get("status") is not None]
        results = []
        for run_id in dict.fromkeys(run_ids):
            doc, written = ev.evaluate(run_id, force=args.force)
            results.append({**defect_summary(doc), "written": written})
        _print(results)
        return EXIT_OK
    except EvaluationError as e:
        print(f"evaluate: {e}", file=sys.stderr)
        return EXIT_ERROR

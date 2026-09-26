"""Report lane CLI: `report` and `maps` (dispatched by `python -m adaptive_harness`).

    report --experiment E [--out reports/E.md] [--no-render-check]
    maps   --versions v1,v2 --experiment E [--out reports/maps.md] [--no-export]

`report` writes the markdown report and records its repo-relative path on the
experiment. Operator notes, when present, are read from reports/<E>.notes.json
({"define": "..."}) and placed in the matching section.

`maps` writes Mermaid diagrams generated from the stored configurations and records.
When Mermaid CLI is reachable through `npx` (npx -y @mermaid-js/mermaid-cli), each
diagram is also exported as SVG and PNG into a folder named after the output file
(reports/maps.md -> reports/maps/).

`show` is not defined here yet; the dispatcher falls back to the store lane's
`show run <id>`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..contracts.interfaces import Store
from ..contracts.paths import REPO_ROOT
from . import maps as mapsmod
from .data import ExperimentData, executed_stages, load
from .markdown import render, run_chart_mermaid

REPORTS_DIR = REPO_ROOT / "reports"
MERMAID_CLI = ["-y", "@mermaid-js/mermaid-cli"]

# Replaced in tests.
open_store: Callable[[], Store]


def _open_store() -> Store:
    from dotenv import load_dotenv

    from ..store.mongo import MongoStore

    load_dotenv()
    return MongoStore.from_env()


open_store = _open_store


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------- mermaid CLI


def mermaid_cli() -> list[str] | None:
    """Command prefix for Mermaid CLI via npx, or None when npx is not on PATH."""
    npx = shutil.which("npx")
    return [npx, *MERMAID_CLI] if npx else None


def render_mermaid(source: str, outputs: list[Path], cli: list[str] | None = None, timeout: float = 180) -> tuple[bool, str]:
    """Render one diagram to each output path (format from the suffix). (ok, message)."""
    cli = cli or mermaid_cli()
    if cli is None:
        return False, "Mermaid CLI not available (npx not on PATH)"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "diagram.mmd"
        src.write_text(source, encoding="utf-8")
        for out in outputs:
            out.parent.mkdir(parents=True, exist_ok=True)
            args = [*cli, "-i", str(src), "-o", str(out), "-b", "white"]
            if out.suffix == ".png":
                args += ["-s", "2"]
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as e:
                return False, f"{type(e).__name__}: {e}"
            if proc.returncode != 0 or not out.exists():
                return False, (proc.stderr or proc.stdout).strip()[-800:]
    return True, "ok"


# ---------------------------------------------------------------- report


def load_notes(experiment_id: str, directory: Path = REPORTS_DIR) -> dict[str, str]:
    path = directory / f"{experiment_id}.notes.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, str)}


def report(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m adaptive_harness report",
                                description="Write the markdown report for an experiment.")
    p.add_argument("--experiment", required=True)
    p.add_argument("--out", default=None, help="output path (default reports/<experiment>.md)")
    p.add_argument("--no-render-check", action="store_true",
                   help="do not test-render the run chart with Mermaid CLI")
    args = p.parse_args(argv)
    out = Path(args.out) if args.out else REPORTS_DIR / f"{args.experiment}.md"

    store = open_store()
    try:
        try:
            d = load(store, args.experiment)
        except KeyError as e:
            print(f"report: {e.args[0]}", file=sys.stderr)
            return 1
        xychart_ok: bool | None = None
        if not args.no_render_check and mermaid_cli() is not None:
            with tempfile.TemporaryDirectory() as tmp:
                xychart_ok, msg = render_mermaid(run_chart_mermaid(d), [Path(tmp) / "run_chart.svg"])
            if not xychart_ok:
                print(f"report: run chart did not render, omitted: {msg}", file=sys.stderr)
        text = render(d, load_notes(args.experiment, out.parent), xychart_ok)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        rel = _rel(out)
        if Path(rel).is_absolute():
            print(f"report: {rel} is not inside the repository; report_path not recorded", file=sys.stderr)
        else:
            from ..store.records import update_experiment

            update_experiment(store, args.experiment, {"report_path": rel})
    finally:
        close = getattr(store, "close", None)
        if close:
            close()
    print(f"report: wrote {_rel(out)}")
    return 0


# ---------------------------------------------------------------- maps


def build_maps(store: Store, d: ExperimentData, version_ids: list[str]) -> list[tuple[str, str, str]]:
    """[(name, heading, mermaid source)] for the given versions and experiment."""
    versions = {}
    for vid in version_ids:
        v = d.versions.get(vid) or store.get("harness_versions", vid)
        if v is None:
            raise KeyError(f"no harness version {vid!r}")
        versions[vid] = v
    runs_by_version: dict[str, list[str]] = {vid: [] for vid in version_ids}
    for a in d.arms.values():
        for r in a.runs.values():
            if r["harness_version"] in runs_by_version:
                runs_by_version[r["harness_version"]].append(r["_id"])
    executed = {vid: executed_stages(store, rids) for vid, rids in runs_by_version.items()}

    first = version_ids[0]
    out = [(f"{first}_process", f"The {first} process",
            mapsmod.process_map(versions[first], runs_by_version[first], executed[first])),
           ("dmaic_cycle", f"The DMAIC cycle of {d.experiment['_id']} as it ran", mapsmod.dmaic_map(d))]
    for a, b in zip(version_ids, version_ids[1:]):
        out.append((f"{a}_vs_{b}", f"{a} against {b}",
                    mapsmod.compare_map(versions[a], versions[b], runs_by_version, executed)))
    return out


def maps_markdown(d: ExperimentData, diagrams: list[tuple[str, str, str]], versions: dict[str, Any],
                  exported: dict[str, list[str]]) -> str:
    lines = [f"# Maps: experiment {d.experiment['_id']}", "",
             "Generated by `python -m adaptive_harness maps` from the stored harness configurations and the "
             "experiment's records; not drawn by hand. Stage node ids are stable (`<version>_<stage id>`).", "",
             mapsmod.legend_text(), ""]
    for vid, v in versions.items():
        lines.append(f"- {vid}: config hash `{v['config_hash']}`, status {v.get('status')}, pinned {v.get('pinned')}")
    for name, heading, src in diagrams:
        lines += ["", f"## {heading}", ""]
        if name.endswith("_process"):
            lines.append("Each node: stage id and name; for model stages each role with its context items, and "
                         "the tools; for S7 the enabled in-harness checks.")
        elif name == "dmaic_cycle":
            lines.append("Each edge carries the tollgate verdict of the phase it leaves; phases not reached are "
                         "dashed.")
        else:
            vids = name.split("_vs_")
            changes = mapsmod.change_summary(versions[vids[0]]["config"], versions[vids[1]]["config"])
            lines.append("Changes: " + ("; ".join(changes) if changes else "none") + ".")
        lines += ["", "```mermaid", src, "```"]
        if exported.get(name):
            lines += ["", "Exported: " + ", ".join(f"[{Path(p).name}]({p})" for p in exported[name])]
    return "\n".join(lines).rstrip() + "\n"


def maps(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m adaptive_harness maps",
                                description="Write Mermaid maps of versions and an experiment.")
    p.add_argument("--versions", required=True, help="comma-separated, e.g. v1,v2")
    p.add_argument("--experiment", required=True)
    p.add_argument("--out", default=None, help="output path (default reports/maps.md)")
    p.add_argument("--no-export", action="store_true", help="skip SVG and PNG export")
    args = p.parse_args(argv)
    version_ids = [v.strip() for v in args.versions.split(",") if v.strip()]
    out = Path(args.out) if args.out else REPORTS_DIR / "maps.md"

    store = open_store()
    try:
        try:
            d = load(store, args.experiment)
            diagrams = build_maps(store, d, version_ids)
        except KeyError as e:
            print(f"maps: {e.args[0]}", file=sys.stderr)
            return 1
        versions = {vid: d.versions.get(vid) or store.get("harness_versions", vid) for vid in version_ids}
    finally:
        close = getattr(store, "close", None)
        if close:
            close()

    exported: dict[str, list[str]] = {}
    if not args.no_export:
        cli = mermaid_cli()
        if cli is None:
            print("maps: Mermaid CLI not available (npx not on PATH); SVG and PNG not exported", file=sys.stderr)
        else:
            export_dir = out.parent / out.stem  # reports/maps.md -> reports/maps/
            for name, _, src in diagrams:
                files = [export_dir / f"{name}.svg", export_dir / f"{name}.png"]
                ok, msg = render_mermaid(src, files, cli)
                if ok:
                    exported[name] = [f"{out.stem}/{f.name}" for f in files]
                    print(f"maps: exported {', '.join(_rel(f) for f in files)}")
                else:
                    print(f"maps: export of {name} failed: {msg}", file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(maps_markdown(d, diagrams, versions, exported), encoding="utf-8", newline="\n")
    print(f"maps: wrote {_rel(out)}")
    return 0

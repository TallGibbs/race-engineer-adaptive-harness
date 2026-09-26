"""Mermaid maps generated from configurations and records (never drawn by hand).

- process_map: one version's process, stage by stage (stable stage ids).
- dmaic_map: the DMAIC cycle as it actually ran, each tollgate verdict on its edge,
  phases not reached drawn dashed.
- compare_map: two versions side by side, changes marked in text and style.

Pure functions over config dicts and the gathered ExperimentData.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .data import ExperimentData

PHASES = ("define", "measure", "analyze", "improve", "control")
MARKS = ("added", "moved", "context changed", "checks changed", "removed", "not executed")

CLASS_DEFS = [
    "classDef added fill:#d9f2d9,stroke:#2e7d32,stroke-width:2px",
    "classDef moved fill:#fff4cc,stroke:#b8860b,stroke-width:2px",
    "classDef ctx fill:#dbe9ff,stroke:#1f5fbf,stroke-width:2px",
    "classDef chk fill:#f1dcff,stroke:#7b1fa2,stroke-width:2px",
    "classDef removed fill:#f5f5f5,stroke:#c62828,stroke-width:2px,stroke-dasharray:6 4,color:#888",
    "classDef notexec fill:#ffffff,stroke:#777,stroke-dasharray:4 4,color:#777",
    "classDef stamp fill:#f7f7f7,stroke:#bbb,color:#555,font-size:11px",
]
MARK_CLASS = {"added": "added", "moved": "moved", "context changed": "ctx", "checks changed": "chk",
              "removed": "removed", "not executed": "notexec"}


def esc(text: str) -> str:
    """Text safe inside a quoted Mermaid label."""
    return str(text).replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def frontmatter(title: str) -> list[str]:
    return ["---", f"title: {title}", "---"]


# ---------------------------------------------------------------- one version's process


def stage_def(config: Mapping[str, Any], sid: str) -> Mapping[str, Any]:
    return config["stage_catalog"].get(sid) or config["optional_stage_catalog"][sid]


def context_items(config: Mapping[str, Any], role: str, sid: str, has_tools: bool) -> list[str]:
    items = ((config.get("context_policy") or {}).get("roles") or {}).get(role, {}).get(sid)
    if items is None:
        return ["task", "output_schema", *(["tool_docs"] if has_tools else [])]
    return list(items)


def enabled_checks(config: Mapping[str, Any]) -> list[str]:
    out = []
    for name, c in (config.get("checks") or {}).items():
        if c.get("enabled"):
            extra = {k: v for k, v in c.items() if k not in ("enabled", "editable")}
            out.append(name + (f" ({', '.join(f'{k} {v}' for k, v in extra.items())})" if extra else ""))
    return out


def stage_label(config: Mapping[str, Any], sid: str, marks: Sequence[str] = ()) -> str:
    sd = stage_def(config, sid)
    lines = [f"<b>{sid}</b> {sd['name']}"]
    if sd.get("kind") == "code":
        lines.append("code")
        lines.append("checks: " + ", ".join(enabled_checks(config)))
    else:
        tools = sd.get("tools") or []
        for role in sd.get("roles") or []:
            ctx = context_items(config, role, sid, bool(tools))
            lines.append(f"{role}: " + ", ".join(ctx))
        if tools:
            lines.append("tools: " + ", ".join(tools))
    lines += [f"[{m}]" for m in marks]
    return "<br/>".join(esc(l) if not l.startswith("<b>") else l for l in lines)


def groups(config: Mapping[str, Any], stages: Sequence[str]) -> list[list[str]]:
    """Consecutive stages sharing a parallel_group form one group."""
    out: list[list[str]] = []
    for sid in stages:
        g = stage_def(config, sid).get("parallel_group")
        if out and g and stage_def(config, out[-1][0]).get("parallel_group") == g:
            out[-1].append(sid)
        else:
            out.append([sid])
    return out


def stage_nodes(config: Mapping[str, Any], prefix: str, stages: Sequence[str],
                marks: Mapping[str, list[str]] | None = None, indent: str = "  ") -> list[str]:
    marks = marks or {}
    lines: list[str] = []
    gs = groups(config, stages)
    for g in gs:
        if len(g) > 1:
            gid = stage_def(config, g[0]).get("parallel_group")
            lines.append(f'{indent}subgraph {prefix}_{gid} ["{gid}: parallel, blind to each other"]')
            for sid in g:
                lines.append(f'{indent}  {prefix}_{sid}["{stage_label(config, sid, marks.get(sid, []))}"]')
            lines.append(f"{indent}end")
        else:
            sid = g[0]
            lines.append(f'{indent}{prefix}_{sid}["{stage_label(config, sid, marks.get(sid, []))}"]')
    for a, b in zip(gs, gs[1:]):
        for x in a:
            for y in b:
                lines.append(f"{indent}{prefix}_{x} --> {prefix}_{y}")
    for sid, ms in marks.items():
        for m in ms[:1]:
            lines.append(f"{indent}class {prefix}_{sid} {MARK_CLASS[m]}")
    return lines


def stamp_node(node_id: str, lines: Iterable[str], indent: str = "  ") -> list[str]:
    text = "<br/>".join(esc(l) for l in lines)
    return [f'{indent}{node_id}["{text}"]', f"{indent}class {node_id} stamp"]


def process_map(version: Mapping[str, Any], run_ids: Sequence[str], executed: set[str] | None = None) -> str:
    config = version["config"]
    vid = version["_id"]
    marks = {}
    if executed is not None and run_ids:
        marks = {sid: ["not executed"] for sid in config["stages"] if sid not in executed}
    lines = frontmatter(f"{vid} process, config {version['config_hash'][:12]}")
    lines.append("flowchart LR")
    lines += stage_nodes(config, vid, config["stages"], marks)
    lines += stamp_node(f"{vid}_stamp", [f"version {vid}", f"config {version['config_hash']}",
                                         f"runs ({len(run_ids)}):", *run_ids])
    lines += ["  " + c for c in CLASS_DEFS]
    return "\n".join(lines)


# ---------------------------------------------------------------- the DMAIC cycle as it ran


def dmaic_map(d: ExperimentData) -> str:
    exp = d.experiment
    dm = exp.get("dmaic") or {}
    lines = frontmatter(f"DMAIC cycle as it ran, experiment {exp['_id']}")
    lines.append("flowchart LR")
    base = d.arms.get("baseline")
    lines.append(f'  B["baseline arm<br/>{len(base.runs) if base else 0} runs, snapshot {base.snapshot if base else "-"}"]')
    for p in PHASES:
        ph = dm.get(p) or {}
        status = ph.get("status", "not_reached")
        lines.append(f'  {p}["{p.capitalize()}<br/>{status.replace("_", " ")}"]')
    prev = "B"
    for p in PHASES:
        status = (dm.get(p) or {}).get("status", "not_reached")
        if status == "not_reached":
            lines.append(f'  {prev} -.->|"not reached"| {p}')
        elif prev == "B":
            lines.append(f"  {prev} --> {p}")
        else:
            lines.append(f'  {prev} -->|"{edge_label(dm.get(prev) or {})}"| {p}')
        prev = p
    last = dm.get("control") or {}
    outcome = exp.get("decision") or "open"
    lines.append(f'  OUT(["decision: {esc(outcome)}"])')
    final_arrow = "-.->" if last.get("status", "not_reached") == "not_reached" else "-->"
    stop_phase = next((p for p in PHASES if (dm.get(p) or {}).get("status") == "stopped"), None)
    if stop_phase:
        lines.append(f'  {stop_phase} -->|"{edge_label(dm[stop_phase])}"| OUT')
    else:
        lines.append(f'  control {final_arrow}|"{edge_label(last)}"| OUT')
    lines += stamp_node("stamp", [f"experiment {exp['_id']}",
                                  *[f"{vid} config {v['config_hash']} ({v.get('status')})"
                                    for vid, v in sorted(d.versions.items())],
                                  *[f"{a.arm}: {', '.join(r['_id'] for r in a.runs.values())}"
                                    for a in d.arms.values()]])
    for p in PHASES:
        status = (dm.get(p) or {}).get("status", "not_reached")
        cls = {"passed": "passed", "stopped": "stopped"}.get(status, "notreached")
        lines.append(f"  class {p} {cls}")
    lines += ["  classDef passed fill:#d9f2d9,stroke:#2e7d32,stroke-width:2px",
              "  classDef stopped fill:#ffd9d9,stroke:#c62828,stroke-width:3px",
              "  classDef notreached fill:#ffffff,stroke:#777,stroke-dasharray:5 5,color:#777",
              "  " + CLASS_DEFS[-1]]
    return "\n".join(lines)


def edge_label(phase: Mapping[str, Any]) -> str:
    status = phase.get("status", "not_reached")
    if status == "not_reached":
        return "not reached"
    tg = phase.get("tollgate") or {}
    word = "passed" if tg.get("passed") else "stopped"
    reason = (tg.get("reasons") or [""])[-1]
    return "<br/>".join(esc(line) for line in wrap(f"{word}: {reason}" if reason else word, 40))


def wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines or [""]


# ---------------------------------------------------------------- two versions side by side


def diff_marks(old: Mapping[str, Any], new: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    """Marks on the new version's stages, and the stage ids removed from the old one."""
    old_stages, new_stages = list(old["stages"]), list(new["stages"])
    common = [s for s in new_stages if s in old_stages]
    old_order = [s for s in old_stages if s in common]
    marks: dict[str, list[str]] = {s: [] for s in new_stages}
    for s in new_stages:
        if s not in old_stages:
            marks[s].append("added")
    for i, s in enumerate(common):
        if old_order.index(s) != i:
            marks[s].append("moved")
    for s in common:
        sd = stage_def(new, s)
        tools = bool(sd.get("tools"))
        if any(context_items(old, r, s, tools) != context_items(new, r, s, tools) for r in sd.get("roles") or []):
            marks[s].append("context changed")
        if sd.get("kind") == "code" and old.get("checks") != new.get("checks"):
            marks[s].append("checks changed")
    removed = [s for s in old_stages if s not in new_stages]
    return {s: m for s, m in marks.items() if m}, removed


def compare_map(old_v: Mapping[str, Any], new_v: Mapping[str, Any], runs: Mapping[str, Sequence[str]],
                executed: Mapping[str, set[str]]) -> str:
    old, new = old_v["config"], new_v["config"]
    o, nw = old_v["_id"], new_v["_id"]
    marks, removed = diff_marks(old, new)
    old_marks = {s: ["removed"] for s in removed}
    for vid, cfg, mk in ((o, old, old_marks), (nw, new, marks)):
        ex = executed.get(vid)
        if ex is not None and runs.get(vid):
            for sid in cfg["stages"]:
                if sid not in ex:
                    mk.setdefault(sid, []).append("not executed")
    lines = frontmatter(f"{o} against {nw}")
    lines.append("flowchart LR")
    for vid, cfg, mk, v in ((o, old, old_marks, old_v), (nw, new, marks, new_v)):
        lines.append(f'  subgraph {vid}_box ["{vid}, config {v["config_hash"][:12]}, {v.get("status")}"]')
        lines.append("    direction TB")
        lines += stage_nodes(cfg, vid, cfg["stages"], mk, indent="    ")
        lines.append("  end")
    lines.append(f"  {o}_box ~~~ {nw}_box")
    lines += legend()
    lines += stamp_node("stamp", [*[f"{vid} config {v['config_hash']}" for vid, v in ((o, old_v), (nw, new_v))],
                                  *[f"{vid} runs: {', '.join(runs.get(vid, [])) or 'none'}" for vid in (o, nw)]])
    lines.append(f"  {nw}_box ~~~ legend")
    lines += ["  " + c for c in CLASS_DEFS]
    return "\n".join(lines)


def legend() -> list[str]:
    lines = ['  subgraph legend ["legend"]', "    direction LR"]
    for m in MARKS:
        nid = "lg_" + MARK_CLASS[m]
        lines.append(f'    {nid}["[{m}]"]')
        lines.append(f"    class {nid} {MARK_CLASS[m]}")
    lines.append("  end")
    return lines


def change_summary(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """Plain-text list of what differs between two configs (stages, context, checks)."""
    marks, removed = diff_marks(old, new)
    out = [f"{s}: [{'], ['.join(m)}]" for s, m in marks.items()]
    out += [f"{s}: [removed]" for s in removed]
    for name in sorted(set(old.get("checks", {})) | set(new.get("checks", {}))):
        a, b = old.get("checks", {}).get(name), new.get("checks", {}).get(name)
        if a != b:
            out.append(f"check {name}: {a} -> {b}")
    if (old.get("context_policy") or {}).get("lessons") != (new.get("context_policy") or {}).get("lessons"):
        out.append(f"lessons: {old['context_policy']['lessons']} -> {new['context_policy']['lessons']}")
    return out


def legend_text() -> str:
    return ("Legend: [added] stage not in the older version; [moved] stage in a different position; "
            "[context changed] a role at that stage receives different context items; [checks changed] the "
            "in-harness checks run at S7 differ; [removed] stage dropped from the newer version (dashed red); "
            "[not executed] stage with no recorded event in that version's runs (dashed grey). Node ids are the "
            "stable stage ids (e.g. v1_S4).")

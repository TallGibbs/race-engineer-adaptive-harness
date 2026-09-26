"""`show` views of stored records, read straight from the store (MongoDB Atlas in the demo).

    show experiment <id>      phases and tollgates, decision, acceptance, versions, runs per arm
    show version <id>         config hash, status, pinned, parent, each change
    show lesson <id>          categories, why-chain, lesson text (never the vector)
    show lessons --query T    $vectorSearch on lessons_vector, optionally pre-filtered by snapshot
    show cost --experiment E  model calls and tokens by arm, role, and model (no prices)

Counts come from aggregation pipelines on stores that have `aggregate` (MongoStore); a
store without it (FakeStore) gets the same numbers from a Python fallback over `find`.
Every function returns lines already wrapped to `width`.
"""

from __future__ import annotations

import json
import textwrap
from typing import Any, Callable, Mapping, Sequence

from ..contracts.interfaces import Store
from ..store.mongo import vector_search_pipeline
from .data import ARM_ORDER

PHASE_ORDER = ("define", "measure", "analyze", "improve", "control")
RULES = ("R0", "R1", "R2", "R3", "R4")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens")
SEARCH_TEXT_LINES = 3  # lines of each hit's first text line shown by `show lessons`


# ---------------------------------------------------------------- formatting


def wrap(text: Any, width: int, indent: str = "", hang: str | None = None) -> list[str]:
    """Wrap one value to `width`: first line after `indent`, continuation lines after `hang`
    (default: indent plus two spaces). Long unbroken tokens (hashes, ids) are split."""
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, sort_keys=True, default=str)
    hang = indent + "  " if hang is None else hang
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        lines += textwrap.wrap(para.strip(), width=max(width, len(hang) + 10), initial_indent=indent,
                               subsequent_indent=hang, break_long_words=True, break_on_hyphens=False) or [indent]
        indent = hang
    return lines


def field(label: str, value: Any, width: int, indent: str = "  ") -> list[str]:
    head = f"{indent}{label}: "
    return wrap(value, width, indent=head, hang=indent + "    ")


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], width: int, right: Sequence[int] = ()) -> list[str]:
    """Fixed-column table; text columns are cut to fit `width`, never numbers."""
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    over = sum(widths) + 2 * (len(widths) - 1) - width
    for i in sorted((i for i in range(len(widths)) if i not in right), key=lambda i: -widths[i]):
        if over <= 0:
            break
        cut = min(over, max(0, widths[i] - 8))
        widths[i] -= cut
        over -= cut

    def fmt(r: list[str]) -> str:
        out = []
        for i, c in enumerate(r):
            c = c if len(c) <= widths[i] else c[: widths[i] - 1] + "~"
            out.append(c.rjust(widths[i]) if i in right else c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    return [fmt(cells[0]), "  ".join("-" * w for w in widths)] + [fmt(r) for r in cells[1:]]


def _arm_key(arm: Any) -> tuple[int, str]:
    return (ARM_ORDER.index(arm) if arm in ARM_ORDER else len(ARM_ORDER), str(arm))


def _aggregate(store: Store, collection: str, pipeline: list[dict[str, Any]],
               fallback: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    agg = getattr(store, "aggregate", None)
    return list(agg(collection, pipeline)) if callable(agg) else fallback()


# ---------------------------------------------------------------- experiment


def runs_per_arm_pipeline(experiment_id: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"experiment_id": experiment_id}},
        {"$group": {
            "_id": "$arm",
            "runs": {"$sum": 1},
            "harness": {"$sum": {"$cond": [{"$eq": ["$status", "harness_error"]}, 1, 0]}},
            "wall_seconds": {"$sum": "$totals.wall_seconds"},
            "versions": {"$addToSet": "$harness_version"},
        }},
        {"$sort": {"_id": 1}},
    ]


def runs_per_arm(store: Store, experiment_id: str) -> list[dict[str, Any]]:
    def fallback() -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for r in store.find("runs", {"experiment_id": experiment_id}):
            g = groups.setdefault(r.get("arm"), {"_id": r.get("arm"), "runs": 0, "harness": 0,
                                                "wall_seconds": 0.0, "versions": []})
            g["runs"] += 1
            g["harness"] += r.get("status") == "harness_error"
            g["wall_seconds"] += (r.get("totals") or {}).get("wall_seconds", 0) or 0
            if r.get("harness_version") not in g["versions"]:
                g["versions"].append(r.get("harness_version"))
        return list(groups.values())

    rows = _aggregate(store, "runs", runs_per_arm_pipeline(experiment_id), fallback)
    return sorted(rows, key=lambda g: _arm_key(g["_id"]))


def show_experiment(store: Store, experiment_id: str, width: int = 100) -> list[str]:
    exp = store.get("experiments", experiment_id)
    if exp is None:
        raise KeyError(f"no experiment {experiment_id!r}")
    pinned = store.pinned_version()
    lines = wrap(f"experiment {exp['_id']}  decision {exp.get('decision') or '-'}", width)
    lines += field("candidate version", exp.get("candidate_version") or "-", width)
    lines += field("pinned version now", pinned["_id"] if pinned else "-", width)
    cases = exp.get("cases") or []
    if cases:
        lines += field("cases", ", ".join(f"{c['id']} ({c['set']})" for c in cases), width)
    if exp.get("report_path"):
        lines += field("report", exp["report_path"], width)

    lines += ["", "phases"]
    dmaic = exp.get("dmaic") or {}
    for name in PHASE_ORDER:
        ph = dmaic.get(name) or {}
        gate = ph.get("tollgate")
        verdict = "-" if gate is None else ("passed" if gate.get("passed") else "not passed")
        lines += wrap(f"  {name:<8} {ph.get('status', 'not_reached'):<12} tollgate {verdict}", width)
        for reason in (gate or {}).get("reasons") or []:
            lines += wrap(reason, width, indent="    - ", hang="      ")

    lines += ["", "acceptance"]
    acc = exp.get("acceptance")
    if not acc:
        lines.append("  not decided")
    else:
        for rule in RULES:
            r = acc.get(rule) or {}
            lines += wrap(r.get("detail", ""), width, indent=f"  {rule} {'holds' if r.get('holds') else 'fails'}  ",
                          hang="            ")

    lines += [""] + wrap("runs per arm (aggregation: $match experiment_id, $group arm)", width)
    rows = [[g["_id"], ",".join(sorted(v for v in g["versions"] if v)), g["runs"], g["harness"],
             f"{g['wall_seconds']:.0f}"] for g in runs_per_arm(store, experiment_id)]
    if rows:
        lines += ["  " + ln for ln in table(["arm", "version", "runs", "harness", "wall s"], rows, width - 2,
                                           right=(2, 3, 4))]
    else:
        lines.append("  no runs")
    return lines


# ---------------------------------------------------------------- version


def _op_text(op: Mapping[str, Any]) -> str:
    text = f"{op.get('op')} {op.get('path')}"
    if op.get("from") is not None:
        text += f" from {op['from']}"
    if "value" in op and op.get("op") not in ("remove", "move", "copy"):
        text += " = " + json.dumps(op.get("value"), ensure_ascii=False, sort_keys=True, default=str)
    return text


def show_version(store: Store, version_id: str, width: int = 100) -> list[str]:
    v = store.get("harness_versions", version_id)
    if v is None:
        raise KeyError(f"no harness version {version_id!r}")
    lines = wrap(f"version {v['_id']}  status {v.get('status')}  pinned {bool(v.get('pinned'))}  "
                 f"parent {v.get('parent_id') or '-'}", width)
    lines += field("config_hash", v.get("config_hash"), width)
    validation = v.get("validation")
    if validation:
        lines += field("validation", "valid" if validation.get("valid") else "invalid", width)
        for reason in validation.get("reasons") or []:
            lines += wrap(reason, width, indent="    - ", hang="      ")
    changes = v.get("changes") or []
    lines += ["", f"changes ({len(changes)})"]
    for i, ch in enumerate(changes, 1):
        lines += wrap(_op_text(ch.get("op") or {}), width, indent=f"  {i}. op ", hang="       ")
        lines += field("lesson", ch.get("lesson_id"), width, indent="     ")
        lines += field("why", ch.get("why"), width, indent="     ")
    if not changes:
        lines.append("  none")
    for label in ("rationale", "expected_effect", "risks"):
        if v.get(label):
            lines += ["", label.replace("_", " ")] + wrap(v[label], width, indent="  ", hang="  ")
    reasons = v.get("decision_reasons") or []
    if reasons:
        lines += ["", "decision reasons"]
        for reason in reasons:
            lines += wrap(reason, width, indent="  - ", hang="    ")
    return lines


# ---------------------------------------------------------------- lessons


def show_lesson(store: Store, lesson_id: str, width: int = 100) -> list[str]:
    les = store.get("lessons", lesson_id)
    if les is None:
        raise KeyError(f"no lesson {lesson_id!r}")
    d = les.get("defect") or {}
    lines = wrap(f"lesson {les['_id']}", width, indent="", hang="  ")
    lines += field("status", f"{les.get('status')}  scope {les.get('scope')}  snapshot {les.get('snapshot')}", width)
    lines += field("cause category", f"{les.get('cause_category')}  (controllable {les.get('controllable')})", width)
    lines += field("failure category", les.get("failure_category"), width)
    lines += field("defect", f"run {d.get('run_id')}  case {d.get('case_id')}  check {d.get('check_id')}", width)
    lines += field("origin event", les.get("origin_event_id"), width)
    lines += field("source events", ", ".join(les.get("source_event_ids") or []), width)
    emb = les.get("embedding") or []
    lines += field("embedding", f"{les.get('embedding_model') or '-'}, {len(emb)} dimensions (vector not shown)",
                   width)
    lines += ["", "why-chain"]
    for i, why in enumerate(les.get("why_chain") or [], 1):
        lines += wrap(why, width, indent=f"  {i}. ", hang="     ")
    lines += ["", "lesson"] + wrap(les.get("text"), width, indent="  ", hang="  ")
    return lines


def lesson_search_pipeline(vector: Sequence[float], k: int, filters: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The store's $vectorSearch pipeline, projected so the vector never leaves the server."""
    return [*vector_search_pipeline(vector, k, filters), {"$project": {"embedding": 0}}]


def show_lessons(store: Store, query: str, embed_query: Callable[[str], list[float]], k: int = 5,
                 snapshot: str | None = None, status: str | None = None, width: int = 100) -> list[str]:
    filters: dict[str, Any] = {}
    if snapshot:
        filters["snapshot"] = snapshot
    if status:
        filters["status"] = status
    vector = embed_query(query)
    hits = _aggregate(store, "lessons", lesson_search_pipeline(vector, k, filters),
                      lambda: store.vector_search_lessons(vector, k, filters))
    where = ", ".join(f"{f} = {v}" for f, v in filters.items()) or "none"
    lines = wrap(f'$vectorSearch lessons_vector  k {k}  pre-filter: {where}  query: "{query}"', width, hang="  ")
    if not hits:
        return lines + ["  no lessons found"]
    for i, h in enumerate(hits, 1):
        first = (h.get("text") or "").strip().splitlines()[0] if (h.get("text") or "").strip() else ""
        lines.append("")
        lines += wrap(f"{i}. {h['_id']}", width, indent="", hang="   ")
        lines += field("score", f"{h.get('score', 0):.4f}  cause {h.get('cause_category')}  "
                                f"status {h.get('status')}  snapshot {h.get('snapshot')}", width, indent="   ")
        text = wrap(first, width, indent="   ", hang="   ")
        if len(text) > SEARCH_TEXT_LINES:
            text = text[:SEARCH_TEXT_LINES]
            text[-1] = text[-1][: width - 3].rsplit(" ", 1)[0].rstrip() + "..."
        lines += text
    return lines


# ---------------------------------------------------------------- cost


def cost_pipeline(run_ids: Sequence[str]) -> list[dict[str, Any]]:
    """model_call events of the given runs, joined to their run for the arm, summed by
    (arm, role, model). The $match is served by the events run_type index."""
    return [
        {"$match": {"run_id": {"$in": list(run_ids)}, "type": "model_call"}},
        {"$lookup": {"from": "runs", "localField": "run_id", "foreignField": "_id", "as": "run"}},
        {"$unwind": "$run"},
        {"$group": {
            "_id": {"arm": "$run.arm", "role": "$role", "model": "$model"},
            "calls": {"$sum": 1},
            **{f: {"$sum": {"$ifNull": [f"$usage.{f}", 0]}} for f in TOKEN_FIELDS},
        }},
        {"$sort": {"_id.arm": 1, "_id.role": 1, "_id.model": 1}},
    ]


def cost_rows(store: Store, experiment_id: str) -> list[dict[str, Any]]:
    """[{arm, role, model, calls, input_tokens, output_tokens, cache_read_tokens}], arm order."""
    runs = {r["_id"]: r for r in store.find("runs", {"experiment_id": experiment_id})}

    def fallback() -> list[dict[str, Any]]:
        groups: dict[tuple, dict[str, Any]] = {}
        for ev in store.find("events", {"run_id": {"$in": list(runs)}, "type": "model_call"}):
            key = (runs[ev["run_id"]].get("arm"), ev.get("role"), ev.get("model"))
            g = groups.setdefault(key, {"_id": dict(zip(("arm", "role", "model"), key)), "calls": 0,
                                        **{f: 0 for f in TOKEN_FIELDS}})
            g["calls"] += 1
            for f in TOKEN_FIELDS:
                g[f] += (ev.get("usage") or {}).get(f) or 0
        return list(groups.values())

    if not runs:
        return []
    out = [{**g["_id"], **{k: v for k, v in g.items() if k != "_id"}}
           for g in _aggregate(store, "events", cost_pipeline(list(runs)), fallback)]
    return sorted(out, key=lambda r: (_arm_key(r.get("arm")), str(r.get("role")), str(r.get("model"))))


def show_cost(store: Store, experiment_id: str, width: int = 100) -> list[str]:
    if store.get("experiments", experiment_id) is None:
        raise KeyError(f"no experiment {experiment_id!r}")
    rows = cost_rows(store, experiment_id)
    lines = wrap(f"cost of experiment {experiment_id}: model calls and tokens by arm, role, and model "
                 "(aggregation over events: $match model_call, $lookup runs, $group)", width, hang="  ")
    if not rows:
        return lines + ["  no model calls"]
    total = {"calls": 0, **{f: 0 for f in TOKEN_FIELDS}}
    body = []
    for r in rows:
        for k in total:
            total[k] += r[k]
        body.append([r["arm"], r["role"] or "-", r["model"] or "-", r["calls"],
                     *(f"{r[f]:,}" for f in TOKEN_FIELDS)])
    body.append(["total", "", "", total["calls"], *(f"{total[f]:,}" for f in TOKEN_FIELDS)])
    return lines + [""] + table(["arm", "role", "model", "calls", "input", "output", "cache read"], body, width,
                                right=(3, 4, 5, 6))

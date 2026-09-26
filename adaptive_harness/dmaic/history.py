"""Proposal history: what earlier cycles proposed and how it went.

The improve phase shows the improvement agent every earlier non-baseline version of the
same task family, so a new cycle does not repeat a change set that already failed. Each
entry carries only development-case evidence (evidence rule, SPECIFICATION.md): the R2
detail when R2 decided, and otherwise only the ids of the rules that use
non-development cases, with no numbers and no case ids.

A proposal the validator refused outright never becomes a version; it is recorded only in
its experiment's improve artifact. Those are loaded too, from earlier experiments of the
same task family: the proposed ops and cited lesson ids, and the validator's reasons (any
reason that names a held-out or control case is withheld). Rationale, risks, and the
agent's free text are not carried forward.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .ports import Deps

HISTORY_STATUSES = ("accepted", "rejected", "rolled_back", "candidate")
REFUSED_STATUSES = ("rejected", "rolled_back")
VALIDATOR_REFUSED = "refused by the validator"
WITHHELD_REASON = "a reason that names non-development cases (withheld)"
DEVELOPMENT_RULE = "R2"
NON_DEVELOPMENT = "rejected on a rule that uses non-development cases"


def normalize_op(op: Mapping[str, Any]) -> str:
    """One JSON Patch op as canonical text: only the members its kind uses."""
    kind = op.get("op")
    out: dict[str, Any] = {"op": kind, "path": op.get("path")}
    if kind in ("add", "replace", "test"):
        out["value"] = op.get("value")
    if kind in ("move", "copy"):
        out["from"] = op.get("from", op.get("from_"))
    return json.dumps(out, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def change_set(ops: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The order-insensitive identity of a list of JSON Patch ops."""
    return tuple(sorted(normalize_op(o) for o in ops))


def _rule_verdicts(deps: Deps, version: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """{rule id: {holds, detail}} for the version's pilot: the experiment's acceptance
    record, else the rule ids named in the version's decision reasons (failed rules)."""
    for exp in deps.store.find("experiments", {"candidate_version": version["_id"]}):
        if exp.get("acceptance"):
            return dict(exp["acceptance"])
    out: dict[str, dict[str, Any]] = {}
    for reason in version.get("decision_reasons") or []:
        m = re.match(r"(R\d+)\b[^:]*:\s*(.*)", str(reason))
        if m:
            out[m.group(1)] = {"holds": False, "detail": m.group(2)}
    return out


def development_outcome(deps: Deps, version: Mapping[str, Any]) -> str:
    status = version.get("status")
    if status == "candidate":
        return "no acceptance decision recorded"
    verdicts = _rule_verdicts(deps, version)
    r2 = verdicts.get(DEVELOPMENT_RULE)
    r2_text = f"{DEVELOPMENT_RULE} (development cases): {r2['detail']}" if r2 else None
    if status == "accepted":
        return "accepted" + (f"; {r2_text}" if r2_text else "")
    if status == "rolled_back":
        return "accepted, then rolled back by the control rule, which uses non-development cases"
    failed = sorted(rid for rid, v in verdicts.items() if not v.get("holds"))
    parts = []
    if DEVELOPMENT_RULE in failed and r2_text:
        parts.append(f"rejected on {r2_text}")
    others = [rid for rid in failed if rid != DEVELOPMENT_RULE]
    if others:
        parts.append(f"{NON_DEVELOPMENT} ({', '.join(others)})")
    return "; ".join(parts) or "rejected"


def proposal_history(
    deps: Deps, task_family: str | None, exclude_experiment: str | None = None
) -> list[dict[str, Any]]:
    """Every earlier non-baseline version of the task family and every proposal the
    validator refused in an earlier experiment, oldest first, as the improvement agent
    may see them."""
    docs = [d for d in deps.store.find("harness_versions")
            if d.get("status") in HISTORY_STATUSES and (d.get("config") or {}).get("task_family") == task_family]
    docs.sort(key=lambda d: (d.get("created_at") is None, d.get("created_at"), _num(d["_id"])))
    timed = [(d.get("created_at"), {
        "version_id": d["_id"],
        "status": d["status"],
        "changes": [{"op": json.loads(normalize_op(c["op"])), "lesson_id": c.get("lesson_id")}
                    for c in d.get("changes") or []],
        "outcome": development_outcome(deps, d),
    }) for d in docs]
    timed += _validator_refused(deps, task_family, exclude_experiment)
    timed.sort(key=lambda t: (t[0] is None, _when(t[0])))  # stable: versions keep their order
    return [entry for _, entry in timed]


def _validator_refused(
    deps: Deps, task_family: str | None, exclude_experiment: str | None
) -> list[tuple[Any, dict[str, Any]]]:
    out = []
    for exp in deps.store.find("experiments"):
        if exp["_id"] == exclude_experiment:
            continue
        improve = (exp.get("dmaic") or {}).get("improve") or {}
        art = improve.get("artifact") or {}
        validation = art.get("validation") or {}
        if "proposal" not in art or validation.get("valid") is not False:
            continue
        parent = deps.store.get("harness_versions", art["from"]) if art.get("from") else None
        if parent is None or (parent.get("config") or {}).get("task_family") != task_family:
            continue
        withheld = [c["id"] for c in exp.get("cases") or [] if c.get("set") != "development"]
        reasons = [WITHHELD_REASON if any(cid in str(r) for cid in withheld) else str(r)
                   for r in validation.get("reasons") or []]
        out.append((improve.get("completed_at") or exp.get("created_at"), {
            "experiment_id": exp["_id"],
            "status": VALIDATOR_REFUSED,
            "changes": _proposed_changes(art.get("proposal")),
            "validator_reasons": list(dict.fromkeys(reasons)),
        }))
    return out


def _proposed_changes(proposal: Any) -> list[dict[str, Any]]:
    raw = proposal.get("changes") if isinstance(proposal, Mapping) else None
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, Mapping) and isinstance(c.get("op"), Mapping):
            out.append({"op": json.loads(normalize_op(c["op"])), "lesson_id": c.get("lesson_id")})
    return out


def history_label(entry: Mapping[str, Any]) -> str:
    """How an entry is named in the improve artifact: the version id, or the experiment
    id of a validator-refused proposal."""
    if "version_id" in entry:
        return entry["version_id"]
    return f"{entry['experiment_id']} (refused by the validator)"


def refused_change_sets(history: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """{version id: ops} of the versions whose change set may not be proposed again."""
    return {h["version_id"]: [c["op"] for c in h["changes"]]
            for h in history if h["status"] in REFUSED_STATUSES and h["changes"]}


def validator_refused_change_sets(history: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """{experiment id: ops} of the proposals the validator refused in earlier experiments."""
    return {h["experiment_id"]: [c["op"] for c in h["changes"]]
            for h in history if h["status"] == VALIDATOR_REFUSED and h["changes"]}


def _num(version_id: str) -> int:
    m = re.fullmatch(r"v(\d+)", str(version_id))
    return int(m.group(1)) if m else 0


def _when(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)

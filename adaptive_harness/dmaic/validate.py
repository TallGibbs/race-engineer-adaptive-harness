"""Validation of an improvement proposal (CONTRACT.md sections B and J).

Code checks, in order: the proposal's shape; the change cap; every path (and `from`
path) on the editable whitelist; every change citing a verified controllable root cause
whose cause category is the surface of the path; the change set is not that of an
earlier rejected or rolled-back version, nor of a proposal the validator refused in an
earlier experiment; the patch applies; the fixed parts of the
configuration are unchanged; and the result validates as a HarnessConfig (bounds and
stage order). Any problem rejects the whole proposal.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from ..contracts.config import HarnessConfig, surface_of
from ..contracts.records import Change
from .history import change_set

# Parts of the configuration no proposal may change, whatever the patch looks like.
FIXED_KEYS = ("task_family", "model", "budgets", "stage_catalog", "optional_stage_catalog", "change_cap")
REQUIRED_TEXT = ("rationale", "expected_effect", "risks")


class PatchError(ValueError):
    pass


# ---------------------------------------------------------------- RFC 6902


def _tokens(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError(f"path {path!r} must start with '/'")
    return [t.replace("~1", "/").replace("~0", "~") for t in path[1:].split("/")]


def _parent(doc: Any, path: str) -> tuple[Any, str]:
    tokens = _tokens(path)
    if not tokens:
        raise PatchError("the whole document cannot be replaced")
    cur = doc
    for t in tokens[:-1]:
        cur = _child(cur, t, path)
    return cur, tokens[-1]


def _child(cur: Any, t: str, path: str) -> Any:
    if isinstance(cur, dict):
        if t not in cur:
            raise PatchError(f"{path}: no member {t!r}")
        return cur[t]
    if isinstance(cur, list):
        i = _index(cur, t, path, allow_end=False)
        return cur[i]
    raise PatchError(f"{path}: cannot descend into a scalar")


def _index(lst: list, t: str, path: str, allow_end: bool) -> int:
    if t == "-" and allow_end:
        return len(lst)
    if not t.isdigit() or (len(t) > 1 and t.startswith("0")):
        raise PatchError(f"{path}: {t!r} is not an array index")
    i = int(t)
    if i > len(lst) or (i == len(lst) and not allow_end):
        raise PatchError(f"{path}: index {i} out of range")
    return i


def _get(doc: Any, path: str) -> Any:
    cur = doc
    for t in _tokens(path):
        cur = _child(cur, t, path)
    return cur


def _add(doc: Any, path: str, value: Any) -> None:
    parent, t = _parent(doc, path)
    if isinstance(parent, dict):
        parent[t] = value
    elif isinstance(parent, list):
        parent.insert(_index(parent, t, path, allow_end=True), value)
    else:
        raise PatchError(f"{path}: parent is a scalar")


def _remove(doc: Any, path: str) -> Any:
    parent, t = _parent(doc, path)
    if isinstance(parent, dict):
        if t not in parent:
            raise PatchError(f"{path}: no member {t!r}")
        return parent.pop(t)
    if isinstance(parent, list):
        return parent.pop(_index(parent, t, path, allow_end=False))
    raise PatchError(f"{path}: parent is a scalar")


def apply_patch(doc: Mapping[str, Any], ops: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out = copy.deepcopy(dict(doc))
    for op in ops:
        kind, path = op.get("op"), op.get("path")
        if not isinstance(path, str):
            raise PatchError("every operation needs a path")
        if kind == "add":
            _add(out, path, copy.deepcopy(op.get("value")))
        elif kind == "remove":
            _remove(out, path)
        elif kind == "replace":
            _get(out, path)
            parent, t = _parent(out, path)
            if isinstance(parent, list):
                parent[_index(parent, t, path, allow_end=False)] = copy.deepcopy(op.get("value"))
            else:
                parent[t] = copy.deepcopy(op.get("value"))
        elif kind == "move":
            src = op.get("from")
            if path.startswith(str(src) + "/"):
                raise PatchError(f"cannot move {src} into its own child")
            _add(out, path, _remove(out, str(src)))
        elif kind == "copy":
            _add(out, path, copy.deepcopy(_get(out, str(op.get("from")))))
        elif kind == "test":
            if _get(out, path) != op.get("value"):
                raise PatchError(f"test failed at {path}")
        else:
            raise PatchError(f"unknown operation {kind!r}")
    return out


# ---------------------------------------------------------------- proposal validation


def validate_proposal(
    proposal: Mapping[str, Any] | None,
    parent_config: Mapping[str, Any],
    lessons: Mapping[str, Mapping[str, Any]],
    *,
    new_version_id: str,
    lesson_status: str = "verified",
    refused: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    refused_proposals: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> tuple[dict[str, Any] | None, list[Change], list[str]]:
    """Validate a proposal against its parent configuration.

    `lessons` maps lesson id to the root causes the agent was given (only those may be
    cited). `refused` maps the id of each earlier rejected or rolled-back version to its
    JSON Patch ops; a proposal with the same change set (order-insensitive) is refused.
    `refused_proposals` maps an earlier experiment id to the ops of the proposal the
    validator refused there; an exact repeat of one is refused too.
    Returns (candidate config dict or None, changes, reasons); no reasons means valid.
    """
    if not isinstance(proposal, Mapping):
        return None, [], ["the proposal is not a JSON object"]
    reasons: list[str] = []
    raw = proposal.get("changes")
    if not isinstance(raw, list) or not raw:
        return None, [], ["the proposal has no changes"]
    for key in REQUIRED_TEXT:
        if not isinstance(proposal.get(key), str) or not proposal[key].strip():
            reasons.append(f"the proposal has no {key}")
    cap = int(parent_config.get("change_cap", 0))
    if len(raw) > cap:
        reasons.append(f"{len(raw)} changes exceed the change cap of {cap}")

    changes: list[Change] = []
    for i, item in enumerate(raw, 1):
        try:
            change = Change.model_validate(item)
        except ValidationError as e:
            reasons.append(f"change {i}: malformed ({e.errors()[0].get('msg', 'invalid')})")
            continue
        changes.append(change)
        op = change.op
        surface = surface_of(op.path)
        if surface is None:
            reasons.append(f"change {i}: {op.path} is not an editable path")
        if op.op in ("move", "copy"):
            src_surface = surface_of(op.from_ or "")
            if src_surface is None:
                reasons.append(f"change {i}: from {op.from_!r} is not an editable path")
            elif surface is not None and src_surface != surface:
                reasons.append(f"change {i}: moves between surfaces {src_surface} and {surface}")
        lesson = lessons.get(change.lesson_id)
        if lesson is None:
            reasons.append(f"change {i}: cites {change.lesson_id!r}, which is not a verified controllable root cause given to the agent")
        else:
            if lesson.get("status") != lesson_status:
                reasons.append(f"change {i}: root cause {change.lesson_id} is {lesson.get('status')}, not {lesson_status}")
            if not lesson.get("controllable"):
                reasons.append(f"change {i}: root cause {change.lesson_id} is not controllable")
            if surface is not None and lesson.get("cause_category") != surface:
                reasons.append(
                    f"change {i}: {op.path} is a {surface} surface but root cause {change.lesson_id} "
                    f"is a {lesson.get('cause_category')} cause"
                )
    if (refused or refused_proposals) and len(changes) == len(raw):
        mine = change_set([c.op.model_dump(by_alias=True) for c in changes])
        reasons += [f"repeats rejected proposal {vid}" for vid, ops in (refused or {}).items()
                    if change_set(ops) == mine]
        reasons += [f"repeats refused proposal from {eid}" for eid, ops in (refused_proposals or {}).items()
                    if change_set(ops) == mine]
    if reasons:
        return None, changes, reasons

    ops = [c.op.model_dump(by_alias=True, exclude_none=True) for c in changes]
    for o, c in zip(ops, changes):
        if c.op.op in ("add", "replace", "test"):
            o["value"] = c.op.value
    try:
        patched = apply_patch(parent_config, ops)
    except PatchError as e:
        return None, changes, [f"the patch does not apply: {e}"]

    for key in FIXED_KEYS:
        if patched.get(key) != parent_config.get(key):
            reasons.append(f"the fixed field {key} changed")
    if (patched.get("checks") or {}).get("schema") != (parent_config.get("checks") or {}).get("schema"):
        reasons.append("the fixed check /checks/schema changed")
    lesson_filters = ((patched.get("context_policy") or {}).get("lessons") or {}).get("filters") or {}
    if lesson_filters.get("status") != lesson_status:
        reasons.append(f"lesson retrieval must stay limited to {lesson_status} lessons")
    patched["version_id"] = new_version_id
    patched["parent_id"] = parent_config.get("version_id")
    try:
        config = HarnessConfig.model_validate(patched)
    except ValidationError as e:
        reasons += [f"schema: {err.get('msg')} at {'/'.join(map(str, err.get('loc', ())))}" for err in e.errors()]
        return None, changes, reasons
    unchanged = {**dict(parent_config), "version_id": new_version_id, "parent_id": parent_config.get("version_id")}
    if config.to_dict() == HarnessConfig.model_validate(unchanged).to_dict():
        reasons.append("the changes leave the configuration unchanged")
    if reasons:
        return None, changes, reasons
    return config.to_dict(), changes, []

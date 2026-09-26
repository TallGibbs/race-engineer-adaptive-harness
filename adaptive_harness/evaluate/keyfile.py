"""The answer key and the evaluator's own hash.

This is the only module in the project that opens data/key.json. The key is verified
against data/key.sha256 before any value in it is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..contracts.common import canonical_json, canonical_sha256, sha256_hex
from ..contracts.paths import DATA_DIR

KEY_PATH = DATA_DIR / "key.json"
KEY_SHA_PATH = DATA_DIR / "key.sha256"
EVALUATOR_DIR = Path(__file__).resolve().parent


class KeyHashMismatch(Exception):
    """data/key.json does not hash to the value frozen in data/key.sha256."""


class Key:
    """The verified answer key. `sha256` is the frozen value from key.sha256."""

    def __init__(self, obj: dict[str, Any], sha256: str) -> None:
        self._obj = obj
        self.sha256 = sha256

    def case(self, case_id: str) -> dict[str, Any]:
        cases = self._obj.get("cases", {})
        if case_id not in cases:
            raise KeyError(f"the key has no case {case_id!r}")
        return cases[case_id]


def load_key(key_path: str | Path | None = None, sha_path: str | Path | None = None) -> Key:
    """Read the key and verify it against the frozen hash; raise KeyHashMismatch on any difference."""
    key_path = Path(key_path or KEY_PATH)
    sha_path = Path(sha_path or KEY_SHA_PATH)
    try:
        expected = sha_path.read_text(encoding="utf-8").split()[0].strip().lower()
    except (OSError, IndexError) as e:
        raise KeyHashMismatch(f"cannot read the frozen key hash: {e}") from None
    try:
        obj = json.loads(key_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise KeyHashMismatch(f"cannot read the key: {e}") from None
    actual = canonical_sha256(obj)
    if actual != expected:
        raise KeyHashMismatch("the key does not match its frozen hash")
    return Key(obj, expected)


def evaluator_sha256(directory: str | Path | None = None) -> str:
    """Hash of this lane's source files: canonical SHA-256 of {relative path: sha256 of the
    file with line endings normalized to LF}, so the value is the same on every platform."""
    root = Path(directory or EVALUATOR_DIR)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        data = path.read_bytes().replace(b"\r\n", b"\n")
        files[path.relative_to(root).as_posix()] = sha256_hex(data)
    return sha256_hex(canonical_json(files))

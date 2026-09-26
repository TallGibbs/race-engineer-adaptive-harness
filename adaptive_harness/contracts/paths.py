"""Repository paths and loaders for the fixed configuration files."""

from __future__ import annotations

import json
from pathlib import Path

from .config import HarnessConfig
from .rules import AcceptanceRules, CycleConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
DATA_DIR = REPO_ROOT / "data"
SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: str | Path | None = None) -> HarnessConfig:
    return HarnessConfig.model_validate(_read(Path(path) if path else CONFIGS_DIR / "v1.json"))


def load_acceptance(path: str | Path | None = None) -> AcceptanceRules:
    return AcceptanceRules.model_validate(_read(Path(path) if path else CONFIGS_DIR / "acceptance.json"))


def load_cycle(path: str | Path | None = None) -> CycleConfig:
    return CycleConfig.model_validate(_read(Path(path) if path else CONFIGS_DIR / "cycle.json"))

"""Read-only compatibility helpers for artifacts produced before the TRAIL rename."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Historical artifacts remain immutable and are normalized only after read.
LEGACY_PROTOCOL = "topo" + "stake"
CURRENT_PROTOCOL = "trail"


def canonical_token(value: str) -> str:
    """Map a legacy schema token to its current TRAIL spelling."""

    if value == LEGACY_PROTOCOL:
        return CURRENT_PROTOCOL
    if value.startswith(f"{LEGACY_PROTOCOL}_"):
        return f"{CURRENT_PROTOCOL}_{value[len(LEGACY_PROTOCOL) + 1:]}"
    if value == "Topo" + "Stake":
        return "TRAIL"
    return value


def canonicalize_artifact(value: Any) -> Any:
    """Recursively normalize protocol-labelled keys and scalar values."""

    if isinstance(value, dict):
        return {
            canonical_token(str(key)): canonicalize_artifact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [canonicalize_artifact(item) for item in value]
    if isinstance(value, str):
        return canonical_token(value)
    return value


def resolve_legacy_run_dir(path: Path) -> Path:
    """Return an existing immutable run directory across the rename."""

    if path.exists() or not path.name.startswith(CURRENT_PROTOCOL):
        return path
    legacy_name = LEGACY_PROTOCOL + path.name[len(CURRENT_PROTOCOL) :]
    legacy_path = path.with_name(legacy_name)
    return legacy_path if legacy_path.exists() else path

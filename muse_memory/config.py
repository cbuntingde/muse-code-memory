"""Configuration with layered defaults: built-in < user < project."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    # Retrieval
    "recall_top_k": 5,
    "recall_max_chars": 4000,
    "session_start_max_chars": 2500,
    "min_score": 0.05,
    # Capture
    "capture_enabled": True,
    "capture_tool_result_max_chars": 6000,
    "capture_min_importance": 2,
    "capture_auto_file_importance": 4,
    "proposal_ttl_days": 7,
    # Storage caps (Muse injects max 48 memory files into context)
    "max_files_per_scope": 40,
    "max_chars_per_file": 12000,
    "max_pending": 200,
    # Consolidation
    "dedupe_threshold": 0.85,
    "merge_threshold": 0.72,
    "stale_days": 90,
    "session_consolidation": True,
    "auto_cleanup": True,
    # Scopes
    "scopes": ["global", "project-personal", "project-shared"],
    "default_scope": "project-personal",
}


_SCOPES = ("global", "project-personal", "project-shared")

# key -> (kind, low, high); invalid values fall back to the default.
_CONSTRAINTS: dict[str, tuple[str, float | None, float | None]] = {
    "recall_top_k": ("int", 1, 20),
    "recall_max_chars": ("int", 200, 100000),
    "session_start_max_chars": ("int", 200, 50000),
    "min_score": ("float", 0.0, 100.0),
    "capture_enabled": ("bool", None, None),
    "capture_tool_result_max_chars": ("int", 200, 200000),
    "capture_min_importance": ("int", 1, 5),
    "capture_auto_file_importance": ("int", 1, 5),
    "proposal_ttl_days": ("int", 1, 365),
    "max_files_per_scope": ("int", 1, 200),
    "max_chars_per_file": ("int", 500, 200000),
    "max_pending": ("int", 1, 2000),
    "dedupe_threshold": ("float", 0.0, 1.0),
    "merge_threshold": ("float", 0.0, 1.0),
    "stale_days": ("int", 1, 3650),
    "session_consolidation": ("bool", None, None),
    "auto_cleanup": ("bool", None, None),
}


def _valid(key: str, val: Any) -> bool:
    if key == "scopes":
        return (isinstance(val, list) and bool(val)
                and all(v in _SCOPES for v in val))
    if key == "default_scope":
        return val in _SCOPES
    spec = _CONSTRAINTS.get(key)
    if spec is None:
        return False
    kind, low, high = spec
    if kind == "bool":
        return type(val) is bool
    if kind == "int":
        return type(val) is int and low <= val <= high  # type: ignore[operator]
    if kind == "float":
        return (type(val) in (int, float)
                and low <= float(val) <= high)  # type: ignore[operator]
    return False


_warned: set[tuple[str, str]] = set()


def _warn_once(path: Path, key: str, detail: str) -> None:
    """Stderr warning for a bad config entry, once per file+key per process.

    Invalid entries still fall back to defaults (never break a hook), but
    silence made typos undiscoverable; stderr keeps MCP stdout clean.
    """
    marker = (str(path), str(key))
    if marker in _warned:
        return
    _warned.add(marker)
    try:
        print(f"muse-memory config: ignoring {key} in {path} ({detail})",
              file=sys.stderr)
    except OSError:
        pass


def load(cwd: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = dict(DEFAULTS)
    for path in (paths.user_config_file(), paths.project_config_file(cwd)):
        if path is None:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            _warn_once(path, "<file>", "invalid JSON; using defaults")
            continue
        if not isinstance(data, dict):
            _warn_once(path, "<file>", "top-level JSON must be an object")
            continue
        for key, val in data.items():
            if key not in DEFAULTS:
                _warn_once(path, key, "unknown key")
            elif not _valid(key, val):
                _warn_once(path, key, f"invalid value {val!r}; using default")
            else:
                cfg[key] = val
    return cfg


def save_user_config(values: dict[str, Any]) -> Path:
    path = paths.user_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    for k, v in values.items():
        if k not in DEFAULTS:
            _warn_once(path, k, "unknown key; not saved")
        elif not _valid(k, v):
            _warn_once(path, k, f"invalid value {v!r}; not saved")
        else:
            current[k] = v
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return path

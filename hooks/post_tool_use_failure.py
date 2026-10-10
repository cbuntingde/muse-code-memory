#!/usr/bin/env python
"""PostToolUseFailure: capture error patterns as gotcha candidates (silent)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import event_cwd, run, tool_text  # noqa: E402


def main(event: dict) -> None:
    from muse_memory import config as config_mod
    from muse_memory import extractor, lifecycle
    from muse_memory.store import MemoryStore

    name, text = tool_text(event)
    error = event.get("error") or event.get("tool_error") or ""
    if isinstance(error, str) and error.strip():
        text = f"{error}\n{text}"
    if len(text.strip()) < 20:
        return
    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    if not cfg.get("capture_enabled", True):
        return
    text = f"[{name or 'tool'} failed] " + text[:int(
        cfg.get("capture_tool_result_max_chars", 6000))]
    candidates = extractor.extract_candidates(
        text, source="tool-failure",
        session_id=str(event.get("session_id", "")),
        max_candidates=3)
    if not candidates:
        # Fall back to a compact raw error note so failures are not lost.
        first = " ".join(text.split())[:300]
        first, _ = extractor.redact_secrets(first)
        candidates = [{"text": first, "kind": "gotcha",
                       "topic": extractor.suggest_topic(first, "gotcha"),
                       "importance": 3, "tags": ["gotcha"],
                       "scope": "project-personal", "source": "tool-failure",
                       "session_id": str(event.get("session_id", ""))}]
    lifecycle.queue_candidates(candidates, MemoryStore(cwd),
                               int(cfg.get("max_pending", 200)))


if __name__ == "__main__":
    run(main)

#!/usr/bin/env python3
"""PostToolUse: observe tool results, queue memory candidates (silent)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import event_cwd, run, tool_text  # noqa: E402

# Tools whose output is worth mining. Everything else is skipped for speed.
MINED_TOOLS = {"edit_file", "write_file", "powershell", "monitor",
               "subagent_read_result", "web_fetch", "web_search",
               "read_file", "search"}


def main(event: dict) -> None:
    from muse_memory import config as config_mod
    from muse_memory import extractor, lifecycle
    from muse_memory.store import MemoryStore

    name, text = tool_text(event)
    if name and name not in MINED_TOOLS and not name.endswith(
            ("edit_file", "write_file", "powershell")):
        return
    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    if not cfg.get("capture_enabled", True):
        return
    text = text[:int(cfg.get("capture_tool_result_max_chars", 6000))]
    session_id = str(event.get("session_id", ""))
    candidates = extractor.extract_candidates(
        text, source="tool", session_id=session_id, max_candidates=3)
    if not candidates:
        return
    store = MemoryStore(cwd)
    lifecycle.queue_candidates(candidates, store,
                               int(cfg.get("max_pending", 200)))


if __name__ == "__main__":
    run(main)

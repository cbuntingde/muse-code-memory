"""PostToolUse: observe tool results, queue memory candidates (silent)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import _shell_command, event_cwd, run, tool_text  # noqa: E402

# Tools whose output is worth mining. Everything else is skipped for speed.
MINED_TOOLS = {"edit_file", "write_file", "powershell", "monitor",
               "subagent_read_result", "web_fetch", "web_search",
               "read_file", "search"}


def _tool_file_paths(event: dict, tool_name: str) -> list[str]:
    """File arguments of edit/write tools (bounded, best effort).

    Read tools are excluded: opening a file is not evidence it matters.
    Hook payload shapes vary, so anything unrecognized yields nothing.
    """
    lowered = (tool_name or "").lower()
    if not lowered.endswith(("edit_file", "write_file")):
        return []
    raw = event.get("tool_input", event.get("input"))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, dict):
        return []
    out = []
    for key in ("filePath", "file_path", "path", "file", "filename"):
        val = raw.get(key)
        if (isinstance(val, str) and val.strip() and len(val) < 500
                and ("/" in val or "\\" in val or "." in val)):
            out.append(" ".join(val.split()))
    return out[:3]


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
        text, source="tool", session_id=session_id,
        default_scope=cfg['default_scope'], max_candidates=3)
    store = None
    if candidates:
        store = MemoryStore(cwd)
        lifecycle.queue_candidates(candidates, store,
                                   int(cfg.get("max_pending", 200)))
    if cfg.get("entity_sync_enabled", True):
        scope = cfg["default_scope"]
        if scope == "global":
            scope = "project-personal"
        entity_cands = lifecycle.tool_entity_candidates(
            command=_shell_command(event),
            files=_tool_file_paths(event, name),
            text=text, cwd=cwd, scope=scope)
        if entity_cands:
            if store is None:
                store = MemoryStore(cwd)
            lifecycle.queue_candidates(entity_cands, store,
                                       int(cfg.get("max_pending", 200)))


if __name__ == "__main__":
    run(main)

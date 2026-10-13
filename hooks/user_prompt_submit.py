#!/usr/bin/env python3
"""UserPromptSubmit: RAG recall for the submitted prompt, injected as context.

Read-only hot path: uses the cached FTS5 index (or an in-memory build)
and records use signals to the append-only access log instead of writing
topic metadata; Stop/SessionEnd drain that log into access counts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_context, event_cwd, run


def main(event: dict) -> None:
    from muse_memory import config as config_mod
    from muse_memory.service import MemoryService

    prompt = event.get("prompt") or event.get("user_prompt") or event.get("text") or ""
    if not isinstance(prompt, str) or len(prompt.strip()) < 8:
        return
    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    svc = MemoryService(cwd)
    try:
        _cleaned, _since, _until, hits = svc.recall(
            prompt, top_k=int(cfg.get("recall_top_k", 5)),
            read_only=True, access="queue")
    except ValueError:
        return
    if not hits:
        return
    from muse_memory import rag
    emit_context(rag.format_context(hits, int(cfg.get("recall_max_chars", 4000))),
                 "UserPromptSubmit",
                 message=f"muse-memory: recalled {len(hits)} topics for this prompt")


if __name__ == "__main__":
    run(main)

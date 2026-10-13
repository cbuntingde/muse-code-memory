#!/usr/bin/env python
"""UserPromptSubmit: RAG recall for the submitted prompt, injected as context."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_context, event_cwd, run  # noqa: E402


def main(event: dict) -> None:
    from muse_memory import config as config_mod
    from muse_memory import dates, rag
    from muse_memory.store import MemoryStore

    prompt = event.get("prompt") or event.get("user_prompt") or event.get("text") or ""
    if not isinstance(prompt, str) or len(prompt.strip()) < 8:
        return
    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    cleaned, auto = dates.split_query(prompt)
    since, until = auto if auto else (None, None)
    hits = rag.search_all(
        list(store.scopes.values()), cleaned,
        top_k=int(cfg.get("recall_top_k", 5)),
        min_score=float(cfg.get("min_score", 0.05)),
        since=since, until=until,
    )
    if not hits:
        return
    for hit in hits[:3]:
        scope = store.get_scope(hit.scope)
        if scope is not None:
            try:
                scope.touch_access(hit.file)
            except OSError:
                pass
    emit_context(rag.format_context(hits, int(cfg.get("recall_max_chars", 4000))),
                 "UserPromptSubmit",
                 message=f"muse-memory: recalled {len(hits)} topics for this prompt")


if __name__ == "__main__":
    run(main)

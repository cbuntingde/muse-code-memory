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
    from muse_memory import lifecycle
    from muse_memory.service import MemoryService

    prompt = event.get("prompt") or event.get("user_prompt") or event.get("text") or ""
    if not isinstance(prompt, str) or len(prompt.strip()) < 8:
        return
    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    svc = MemoryService(cwd)
    if cfg.get("entity_sync_enabled", True):
        working = svc.working_set()
    else:
        working = {"text": "", "files": []}
    hits = []
    try:
        _cleaned, _since, _until, hits = svc.recall(
            prompt, top_k=int(cfg.get("recall_top_k", 5)),
            read_only=True, access="queue")
    except ValueError:
        hits = []
    for scope_name, filename in working["files"]:
        lifecycle.queue_access(svc.store, scope_name, [filename])
    parts = []
    if working["text"]:
        parts.append(working["text"])
    if hits:
        from muse_memory import rag
        parts.append(rag.format_context(hits, int(cfg.get("recall_max_chars", 4000))))
    if not parts:
        return
    notes = []
    if hits:
        notes.append(f"recalled {len(hits)} topics for this prompt")
    if working["text"]:
        notes.append("project working set included")
    emit_context("\n\n".join(parts), "UserPromptSubmit",
                 message="muse-memory: " + " + ".join(notes))


if __name__ == "__main__":
    run(main)

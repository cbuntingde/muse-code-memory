#!/usr/bin/env python3
"""SessionStart: ensure stores, refresh RAG, inject a short memory digest."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_context, event_cwd, run  # noqa: E402


def main(event: dict) -> None:
    from muse_memory import config as config_mod
    from muse_memory import rag
    from muse_memory.store import MemoryStore

    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    store.ensure_all()

    # Startup maintenance: retire machine artifacts and enforce the file
    # cap before building the digest. SessionEnd may never fire, so this
    # is the guaranteed once-per-session enforcement point.
    fragmented = capped = 0
    if cfg.get("auto_cleanup", True):
        from muse_memory import consolidate as cons
        for name in ("global", "project-personal", "project-shared"):
            scope = store.get_scope(name)
            if scope is None:
                continue
            try:
                fragmented += cons.retire_fragments(scope)["fragments_retired"]
                capped += cons.enforce_file_cap(
                    scope,
                    int(cfg.get("max_files_per_scope", 40)))["retired_for_cap"]
            except OSError:
                continue

    lines = ["Memory digest (muse-memory):"]
    counts: dict[str, int] = {}
    for name in ("global", "project-personal", "project-shared"):
        scope = store.get_scope(name)
        if scope is None:
            continue
        try:
            idx = rag.load_or_build(scope)
        except OSError:
            continue
        records = scope.read_all()
        counts[name] = len(records)
        if not records:
            continue
        top = sorted(records, key=lambda r: (-r.importance, r.title))[:3]
        lines.append(f"[{name}] {len(records)} topics, {len(idx.chunks)} chunks:")
        for rec in top:
            first = next((ln.strip() for ln in rec.content.splitlines()
                          if ln.strip().startswith(("- ", "* "))),
                         rec.title)
            lines.append(f"  - {rec.title}: {first[2:120]}")
    total_files = sum(counts.values())
    if total_files == 0:
        lines.append("(no memories stored yet — durable facts will be captured "
                     "automatically during this session)")
        status = "muse-memory: ready, no memories stored yet"
    else:
        parts = " + ".join(f"{n} {name.split('-')[-1]}"
                           for name, n in counts.items())
        status = f"muse-memory: {total_files} topics indexed ({parts})"
    if fragmented or capped:
        status += (f"; maintenance retired {fragmented} fragments, "
                   f"{capped} over cap")
    text = "\n".join(lines)
    emit_context(text[:int(cfg.get("session_start_max_chars", 2500))],
                 "SessionStart", message=status)


if __name__ == "__main__":
    run(main)

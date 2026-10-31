#!/usr/bin/env python3
"""SessionStart: ensure stores and inject a short memory digest.

Read-only with respect to topics, metadata, and the persisted index:
maintenance (fragment retirement, file caps, index rebuilds) runs at
Stop/SessionEnd, never on the startup path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_context, event_cwd, run


def main(event: dict) -> None:
    from muse_memory import config as config_mod
    from muse_memory import lifecycle as lifecycle_mod
    from muse_memory.store import MemoryStore

    cwd = event_cwd(event)
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    store.ensure_all()

    lines = ["Memory digest (muse-memory):"]
    counts: dict[str, int] = {}
    for name in ("global", "project-personal", "project-shared"):
        scope = store.get_scope(name)
        if scope is None:
            continue
        records = scope.read_all()
        counts[name] = len(records)
        if not records:
            continue
        top = sorted(records, key=lambda r: (-r.importance, r.title))[:3]
        lines.append(f"[{name}] {len(records)} topics:")
        for rec in top:
            first = next((ln.strip() for ln in rec.content.splitlines()
                          if ln.strip().startswith(("- ", "* "))),
                         rec.title)
            lines.append(f"  - {rec.title}: {first[2:120]}")
    # One build line when a build memory exists: agents that skip recall
    # still see how to build without re-scanning the environment.
    build_best: tuple | None = None
    for name in ("global", "project-personal", "project-shared"):
        scope = store.get_scope(name)
        if scope is None:
            continue
        for rec in scope.read_all():
            if "build" in (rec.tags or []):
                key = (-rec.importance, rec.title)
                if build_best is None or key < build_best[0]:
                    first = next((ln.strip() for ln in rec.content.splitlines()
                                  if ln.strip().startswith(("- ", "* "))),
                                 rec.title)
                    snippet = (first[2:120] if first[:2] in ("- ", "* ")
                               else first[:120])
                    build_best = (key, rec.title, snippet)
    if build_best is not None:
        lines.append(f"Build: {build_best[1]}: {build_best[2]}")
    sync = lifecycle_mod.session_start_entities(store, cfg)
    if sync["block"]:
        lines.append("")
        lines.append(sync["block"])
    total_files = sum(counts.values())
    if total_files == 0:
        lines.append("(no memories stored yet — durable facts will be captured "
                     "automatically during this session)")
        status = "muse-memory: ready, no memories stored yet"
    else:
        parts = " + ".join(f"{n} {name.split('-')[-1]}"
                           for name, n in counts.items())
        status = f"muse-memory: {total_files} topics indexed ({parts})"
    text = "\n".join(lines)
    emit_context(text[:int(cfg.get("session_start_max_chars", 2500))],
                 "SessionStart", message=status)


if __name__ == "__main__":
    run(main)

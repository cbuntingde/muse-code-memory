#!/usr/bin/env python3
"""PreCompact: snapshot current memory state so context survives compaction."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_message, event_cwd, run  # noqa: E402


def main(event: dict) -> None:
    from muse_memory import paths
    from muse_memory.store import MemoryStore

    cwd = event_cwd(event)
    store = MemoryStore(cwd)
    session_id = str(event.get("session_id", "nosession"))[:36]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    scope = store.get_scope("project-personal") or next(
        iter(store.scopes.values()), None)
    if scope is None:
        return
    try:
        scope.dir.mkdir(parents=True, exist_ok=True)
        lines = [f"# Memory snapshot {stamp} (session {session_id})", ""]
        for name, sc in store.scopes.items():
            for rec in sc.read_all():
                if rec.importance >= 3:
                    lines.append(f"- [{name}/{rec.file}] {rec.title}")
        snapshot = scope.dir / f"{paths.SNAPSHOT_PREFIX}{stamp}.md"
        snapshot.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Keep only the 3 newest snapshots.
        snaps = sorted(scope.dir.glob(paths.SNAPSHOT_PREFIX + "*.md"))
        for old in snaps[:-3]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        return
    emit_message(f"muse-memory: snapshot saved ({len(lines) - 2} key topics)")


if __name__ == "__main__":
    run(main)

#!/usr/bin/env python3
"""Stop: file pending candidates (light checkpoint, no full cleanup)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_message, event_cwd, run  # noqa: E402


def main(event: dict) -> None:
    from muse_memory import lifecycle

    if event.get("stop_hook_active"):
        return  # avoid chained Stop loops
    report = lifecycle.stop_checkpoint(
        event_cwd(event), str(event.get("session_id", "")))
    filed = report.get("filed", 0)
    if filed:
        actions = report.get("actions", {})
        detail = ", ".join(f"{k} {v}" for k, v in sorted(actions.items()))
        emit_message(f"muse-memory: filed {filed} memories ({detail})")


if __name__ == "__main__":
    run(main)

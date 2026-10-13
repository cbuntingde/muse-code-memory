#!/usr/bin/env python3
"""Stop: file pending candidates (light checkpoint, no full cleanup)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_message, event_cwd, run


def main(event: dict) -> None:
    from muse_memory import lifecycle

    if event.get("stop_hook_active"):
        return  # avoid chained Stop loops
    report = lifecycle.stop_checkpoint(
        event_cwd(event), str(event.get("session_id", "")))
    filed = report.get("filed", 0)
    retired = report.get("retired_for_cap", 0)
    held = report.get("proposals_held", 0)
    if filed or retired or held:
        parts = []
        if filed:
            actions = report.get("actions", {})
            detail = ", ".join(f"{k} {v}" for k, v in sorted(actions.items()))
            parts.append(f"filed {filed} memories ({detail})")
        if held:
            parts.append(f"{held} capture proposals held (/consolidate --approve-all to file)")
        if retired:
            parts.append(f"retired {retired} over cap")
        emit_message(f"muse-memory: {'; '.join(parts)}")


if __name__ == "__main__":
    run(main)

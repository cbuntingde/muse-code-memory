#!/usr/bin/env python3
"""SessionEnd: mine transcript, file pending, dedupe/merge/expire/trim.

Observational by design (Muse ignores SessionEnd output); all work is local.
Bounded and exception-safe so orderly termination is never delayed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import event_cwd, run  # noqa: E402


def main(event: dict) -> None:
    from muse_memory import lifecycle

    lifecycle.session_end(event_cwd(event), str(event.get("session_id", "")))


if __name__ == "__main__":
    run(main)

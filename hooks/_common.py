"""Shared helpers for muse-memory hook scripts (import-only, not a hook)."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

DIAGNOSTICS_MAX_BYTES = 131072
DIAGNOSTICS_KEEP_BYTES = 65536


def plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_imports() -> None:
    root = str(plugin_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def read_event() -> dict:
    """Read the hook event JSON from stdin (tolerates empty/tty stdin).

    Reads bytes and decodes UTF-8 explicitly: sys.stdin uses the Windows
    locale codec (cp1252), which would mojibake any non-ASCII content.
    """
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except (OSError, ValueError, AttributeError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def event_cwd(event: dict) -> str | None:
    for key in ("cwd", "workspace", "project_dir"):
        val = event.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload))


def emit_context(text: str, event_name: str = "",
               message: str = "") -> None:
    """Inject model-visible context (works for SessionStart/UserPromptSubmit/Stop).

    When message is given, the same single JSON payload also carries a
    user-visible systemMessage, so hooks show live status in chat. Never
    call emit() twice: stdout must hold exactly one JSON document.
    """
    if not text.strip() and not message.strip():
        return
    payload: dict = {}
    if message.strip():
        payload["systemMessage"] = message.strip()
    if text.strip():
        payload["hookSpecificOutput"] = {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    emit(payload)


def emit_message(text: str) -> None:
    if text.strip():
        emit({"systemMessage": text})


def _collect_strings(node: object, out: list[str], depth: int = 0) -> None:
    """Collect prose-like string leaves from a tool payload (bounded)."""
    if depth > 6 or len(out) >= 60:
        return
    if isinstance(node, str):
        s = node.strip()
        if 30 < len(s) < 4000 and " " in s:
            out.append(s)
    elif isinstance(node, dict):
        for value in node.values():
            _collect_strings(value, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:20]:
            _collect_strings(item, out, depth + 1)


def tool_text(event: dict) -> tuple[str, str]:
    """Return (tool_name, combined tool output text) for PostToolUse events.

    Only outputs are mined: tool_input is the agent's own edit/command
    payload (code and JSON), not durable prose, and mining it filed code
    fragments as memories. Structured outputs contribute their prose-like
    string leaves instead of serialized JSON.
    """
    name = str(event.get("tool_name", ""))
    parts: list[str] = []
    for key in ("tool_response", "tool_result", "result", "output"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
        elif isinstance(val, (dict, list)):
            leaves: list[str] = []
            _collect_strings(val, leaves)
            parts.extend(leaves)
    return name, "\n".join(parts)


def log_diagnostic(message: str) -> None:
    """Append one timestamped line to the diagnostics log; never raises."""
    try:
        ensure_imports()
        from muse_memory.paths import diagnostics_log
        path = diagnostics_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            try:
                if path.stat().st_size > DIAGNOSTICS_MAX_BYTES:
                    tail = path.read_bytes()[-DIAGNOSTICS_KEEP_BYTES:]
                    cut = tail.find(b"\n")
                    path.write_bytes(tail[cut + 1:] if cut >= 0 else tail)
            except OSError:
                pass
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message.strip()}\n")
    except Exception:  # noqa: BLE001 - diagnostics must never break hooks
        pass


def run(main) -> None:
    """Run a hook main(event) -> None; never fail the session."""
    try:
        ensure_imports()
        event = read_event()
        main(event)
    except Exception as exc:  # noqa: BLE001 - hooks must never break a session
        hook = os.path.basename(sys.argv[0]) if sys.argv else "hook"
        log_diagnostic(f"{hook} skipped: {exc!r}")
        try:
            sys.stderr.write(f"muse-memory hook skipped: {exc}\n")
        except OSError:
            pass
    sys.stdout.flush()

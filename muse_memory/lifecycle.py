"""Session lifecycle: transcript mining, pending queue, end-of-session jobs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from . import config as config_mod
from . import consolidate as cons
from . import extractor, paths
from .store import MemoryStore

TRANSCRIPT_TAIL_BYTES = 200_000


def find_session_file(session_id: str) -> Path | None:
    """Locate <data>/sessions/YYYY/MM/DD/<session-id>/session.jsonl."""
    if not session_id:
        return None
    sessions = paths.muse_data_dir() / "sessions"
    if not sessions.is_dir():
        return None
    # Direct glob is cheaper than a full walk on Windows.
    matches = list(sessions.glob(f"*/*/*/{session_id}/session.jsonl"))
    if matches:
        return matches[0]
    # Fallback: session id may be a prefix.
    for day in sessions.glob("*/*/*"):
        if not day.is_dir():
            continue
        try:
            children = list(day.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and child.name.startswith(session_id):
                candidate = child / "session.jsonl"
                if candidate.is_file():
                    return candidate
    return None


def tail_transcript_text(session_file: Path,
                         max_bytes: int = TRANSCRIPT_TAIL_BYTES) -> str:
    try:
        size = session_file.stat().st_size
    except OSError:
        return ""
    try:
        with session_file.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop partial first line
            raw = fh.read()
    except OSError:
        return ""
    texts: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        texts.extend(_record_texts(record))
    return "\n".join(texts[-400:])


def _record_texts(record: dict) -> list[str]:
    """Pull human/tool text out of one session.jsonl envelope (best effort)."""
    out: list[str] = []
    payload = record.get("payload", record)

    def walk(node: object, depth: int = 0) -> None:
        if depth > 6 or len(out) > 60:
            return
        if isinstance(node, str):
            s = node.strip()
            if 30 < len(s) < 4000 and " " in s:
                out.append(s)
        elif isinstance(node, dict):
            kind = str(node.get("type", ""))
            if kind in ("text", "prompt", "summary", "output", "result", "error"):
                text = node.get("text") or node.get("content") or node.get("message")
                if isinstance(text, str) and text.strip():
                    out.append(text.strip()[:4000])
            for key in ("text", "prompt", "content", "output", "summary",
                        "message", "input", "result"):
                if key in node:
                    walk(node[key], depth + 1)
        elif isinstance(node, list):
            for item in node[:20]:
                walk(item, depth + 1)

    if isinstance(payload, dict):
        walk(payload)
    return out


# -- pending queue -----------------------------------------------------------


def queue_candidates(candidates: Iterable[dict], store: MemoryStore,
                     max_pending: int = 200) -> int:
    """Append candidates to each target scope's pending queue. Returns queued count."""
    if not candidates:
        return 0
    grouped: dict[str, list[dict]] = {}
    for cand in candidates:
        grouped.setdefault(cand.get("scope", "project-personal"), []).append(cand)
    queued = 0
    for scope_name, items in grouped.items():
        scope = store.get_scope(scope_name) or store.get_scope("project-personal")
        if scope is None:
            continue
        try:
            scope.dir.mkdir(parents=True, exist_ok=True)
            with (scope.dir / paths.PENDING_FILE).open("a", encoding="utf-8") as fh:
                for cand in items[:max_pending]:
                    fh.write(json.dumps(cand) + "\n")
                    queued += 1
        except OSError:
            continue
    return queued


def drain_pending(store: MemoryStore, max_items: int = 200) -> list[dict]:
    out: list[dict] = []
    for scope in store.scopes.values():
        pending = scope.dir / paths.PENDING_FILE
        if not pending.is_file():
            continue
        try:
            lines = pending.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines[:max_items]:
            try:
                cand = json.loads(line)
            except ValueError:
                continue
            if isinstance(cand, dict) and cand.get("text"):
                out.append(cand)
        try:
            if len(lines) <= max_items:
                pending.unlink(missing_ok=True)
            else:
                pending.write_text("\n".join(lines[max_items:]) + "\n", encoding="utf-8")
        except OSError:
            pass
    return out[:max_items]


# -- session jobs ---------------------------------------------------------------


def session_end(cwd: str | os.PathLike | None = None,
                session_id: str = "") -> dict:
    """Full end-of-session job: mine transcript + pending, file, cleanup."""
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    store.ensure_all()
    report: dict = {"session_id": session_id, "cwd": str(store.cwd)}

    candidates: list[dict] = []
    if cfg.get("session_consolidation", True):
        transcript_file = find_session_file(session_id)
        if transcript_file is not None:
            text = tail_transcript_text(transcript_file)
            candidates.extend(extractor.extract_candidates(
                text, source="session", session_id=session_id,
                max_candidates=12))
    candidates.extend(drain_pending(store, int(cfg.get("max_pending", 200))))
    # Drop low-value captures when configured.
    min_imp = int(cfg.get("capture_min_importance", 2))
    candidates = [c for c in candidates if int(c.get("importance", 3)) >= min_imp]

    report["consolidation"] = cons.consolidate_session(
        candidates, store, float(cfg.get("dedupe_threshold", 0.85)))
    if cfg.get("auto_cleanup", True):
        report["cleanup"] = cons.cleanup(store, cfg)
    return report


def stop_checkpoint(cwd: str | os.PathLike | None = None,
                    session_id: str = "") -> dict:
    """Lightweight Stop-hook job: file pending candidates without full cleanup."""
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    store.ensure_all()
    candidates = drain_pending(store, int(cfg.get("max_pending", 200)))
    if not candidates:
        return {"filed": 0}
    report = cons.consolidate_session(
        candidates, store, float(cfg.get("dedupe_threshold", 0.85)))
    return {"filed": report.get("candidates", 0), "actions": report.get("actions", {})}

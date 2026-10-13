"""Session lifecycle: transcript mining, pending queue, end-of-session jobs."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

from . import config as config_mod
from . import consolidate as cons
from . import extractor, paths
from .store import MemoryStore
from .locking import scope_lock

TRANSCRIPT_TAIL_BYTES = 200_000


RECENT_DAY_SCAN_MAX = 60


def find_session_file(session_id: str) -> Path | None:
    """Locate <data>/sessions/YYYY/MM/DD/<session-id>/session.jsonl.

    Scans day directories newest-first and stops at the first hit, so
    cost stays bounded no matter how much session history exists. Day
    segments are zero-padded (YYYY/MM/DD), so lexicographic order is
    chronological.
    """
    if not session_id:
        return None
    sessions = paths.muse_data_dir() / "sessions"
    if not sessions.is_dir():
        return None
    try:
        days = sorted((p for p in sessions.glob("*/*/*") if p.is_dir()),
                      reverse=True)[:RECENT_DAY_SCAN_MAX]
    except OSError:
        return None
    for day in days:
        exact = day / session_id / "session.jsonl"
        try:
            if exact.is_file():
                return exact
        except OSError:
            continue
        # Fallback: session id may be a prefix.
        try:
            children = list(day.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and child.name.startswith(session_id):
                candidate = child / "session.jsonl"
                try:
                    if candidate.is_file():
                        return candidate
                except OSError:
                    continue
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
        cand = {**cand, "queued_at": float(cand.get("queued_at") or time.time())}
        grouped.setdefault(cand.get("scope", "project-personal"), []).append(cand)
    queued = 0
    for scope_name, items in grouped.items():
        scope = store.get_scope(scope_name)
        if scope is None:
            continue
        with scope_lock(scope.dir):
            try:
                scope.dir.mkdir(parents=True, exist_ok=True)
                capacity = max(0, max_pending - scope.pending_count())
                with (scope.dir / paths.PENDING_FILE).open("a", encoding="utf-8") as fh:
                    for cand in items[:capacity]:
                        fh.write(json.dumps(cand) + "\n")
                        queued += 1
            except OSError:
                continue
    return queued


def queue_access(store: MemoryStore, scope_name: str, files) -> int:
    """Append recall hits to a scope's access log (hot-path, append-only).

    UserPromptSubmit records use signals here instead of writing topic
    metadata; Stop/SessionEnd drain the log into real access counts.
    """
    scope = store.get_scope(scope_name)
    names = [f for f in files if f]
    if scope is None or not names:
        return 0
    try:
        scope.dir.mkdir(parents=True, exist_ok=True)
        with (scope.dir / paths.ACCESS_FILE).open("a", encoding="utf-8") as fh:
            for name in names:
                fh.write(json.dumps({"file": name, "ts": time.time()}) + "\n")
    except OSError:
        return 0
    return len(names)


def drain_access(store: MemoryStore) -> int:
    """Apply queued access signals, one count per file per drain."""
    applied = 0
    for scope in store.scopes.values():
        log = scope.dir / paths.ACCESS_FILE
        if not log.is_file():
            continue
        with scope_lock(scope.dir):
            try:
                lines = log.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            seen: list[str] = []
            for line in lines:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                name = item.get("file") if isinstance(item, dict) else None
                if name and name not in seen:
                    seen.append(str(name))
            for name in seen:
                try:
                    scope.touch_access(name)
                    applied += 1
                except OSError:
                    continue
            try:
                log.unlink(missing_ok=True)
            except OSError:
                pass
    return applied


def drain_pending(store: MemoryStore, max_items: int = 200) -> list[dict]:
    out: list[dict] = []
    for scope in store.scopes.values():
        remaining = max_items - len(out)
        if remaining <= 0:
            break
        with scope_lock(scope.dir):
            pending = scope.dir / paths.PENDING_FILE
            if not pending.is_file():
                continue
            try:
                lines = pending.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines[:remaining]:
                try:
                    cand = json.loads(line)
                except ValueError:
                    continue
                if isinstance(cand, dict) and cand.get("text"):
                    out.append(cand)
            try:
                if len(lines) <= remaining:
                    pending.unlink(missing_ok=True)
                else:
                    pending.write_text("\n".join(lines[remaining:]) + "\n", encoding="utf-8")
            except OSError:
                pass
    return out


# -- session jobs ---------------------------------------------------------------


def _proposal_eligible(cand: dict, auto_file_importance: int | None,
                       approve_all: bool) -> bool:
    if approve_all or auto_file_importance is None:
        return True
    if cand.get("source") == "manual":
        return True
    if cand.get("kind") in ("rule", "decision"):
        return True
    try:
        return int(cand.get("importance", 3)) >= auto_file_importance
    except (TypeError, ValueError):
        return False


def file_pending(store: MemoryStore, max_items: int = 200,
                 dedupe_threshold: float = 0.85, min_importance: int = 1,
                 approve_all: bool = False,
                 auto_file_importance: int | None = None,
                 proposal_ttl_days: int | None = None,
                 now: float | None = None) -> dict:
    """Acknowledge queued lines only after successful filing; failures stay queued.

    Proposal policy (when auto_file_importance is set): auto-captured
    candidates below the threshold stay queued as proposals until they
    expire after proposal_ttl_days or are filed with approve_all. Manual
    saves, rules, and decisions always file.
    """
    actions: dict[str, int] = {}
    files: list[str] = []
    attempted = 0
    held = 0
    expired = 0
    now = now if now is not None else time.time()
    for scope in store.scopes.values():
        if attempted >= max_items:
            break
        with scope_lock(scope.dir):
            pending = scope.dir / paths.PENDING_FILE
            if not pending.is_file():
                continue
            lines = pending.read_text(encoding='utf-8').splitlines()
            kept: list[str] = []
            for line in lines:
                if attempted >= max_items:
                    kept.append(line)
                    continue
                try:
                    cand = json.loads(line)
                    if not isinstance(cand, dict) or not isinstance(cand.get('text'), str):
                        raise ValueError('invalid queued candidate')
                    if int(cand.get('importance', 3)) < min_importance:
                        continue
                    if not _proposal_eligible(cand, auto_file_importance,
                                              approve_all):
                        queued_at = float(cand.get('queued_at') or 0.0)
                        if (proposal_ttl_days and queued_at and
                                now - queued_at > proposal_ttl_days * 86400):
                            expired += 1
                            continue
                        held += 1
                        kept.append(line)
                        continue
                    attempted += 1
                    result = cons.file_candidate(cand, store, dedupe_threshold)
                except (OSError, ValueError, TypeError):
                    kept.append(line)
                    continue
                actions[result['action']] = actions.get(result['action'], 0) + 1
                files.append(f"{result['scope']}/{result['file']}")
            if kept:
                tmp = pending.with_suffix('.tmp')
                tmp.write_text('\n'.join(kept) + '\n', encoding='utf-8')
                os.replace(tmp, pending)
            else:
                pending.unlink(missing_ok=True)
    return {'actions': actions, 'files': files, 'candidates': len(files),
            'proposals_held': held, 'proposals_expired': expired}


def session_end(cwd: str | os.PathLike | None = None,
                session_id: str = "") -> dict:
    """Full end-of-session job: mine transcript + pending, file, cleanup."""
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    store.ensure_all()
    report: dict = {"session_id": session_id, "cwd": str(store.cwd)}

    candidates: list[dict] = []
    if cfg.get("capture_enabled", True) and cfg.get("session_consolidation", True):
        transcript_file = find_session_file(session_id)
        if transcript_file is not None:
            text = tail_transcript_text(transcript_file)
            candidates.extend(extractor.extract_candidates(
                text, source="session", session_id=session_id,
                default_scope=cfg['default_scope'], max_candidates=12))
    # Drop low-value captures when configured.
    min_imp = int(cfg.get("capture_min_importance", 2))
    candidates = [c for c in candidates if int(c.get("importance", 3)) >= min_imp]

    # Transcript captures join the same proposal queue as tool captures;
    # one writer (this call) files everything that clears the policy.
    if candidates:
        queue_candidates(candidates, store, int(cfg['max_pending']))
    drain_access(store)
    report["consolidation"] = file_pending(
        store, int(cfg['max_pending']), float(cfg['dedupe_threshold']),
        min_imp, auto_file_importance=int(cfg['capture_auto_file_importance']),
        proposal_ttl_days=int(cfg['proposal_ttl_days']))
    if cfg.get("auto_cleanup", True):
        report["cleanup"] = cons.cleanup(store, cfg)
    return report


# Stop fires after nearly every agent turn: bound one run so it always
# fits its hook timeout. Leftovers stay queued for the next Stop or the
# full SessionEnd drain.
STOP_DRAIN_MAX = 60


def stop_checkpoint(cwd: str | os.PathLike | None = None,
                    session_id: str = "") -> dict:
    """Lightweight Stop-hook job: file pending candidates without full cleanup."""
    cfg = config_mod.load(cwd)
    store = MemoryStore(cwd)
    store.ensure_all()
    drain_access(store)
    limit = min(int(cfg.get("max_pending", 200)), STOP_DRAIN_MAX)
    report = file_pending(
        store, limit, float(cfg['dedupe_threshold']),
        int(cfg['capture_min_importance']),
        auto_file_importance=int(cfg['capture_auto_file_importance']),
        proposal_ttl_days=int(cfg['proposal_ttl_days']))
    retired = 0
    if cfg.get("auto_cleanup", True):
        # SessionEnd may never fire (killed terminals, crashes); enforce
        # the storage cap on every Stop so stores cannot grow unbounded.
        for scope in store.scopes.values():
            retired += cons.enforce_file_cap(
                scope,
                int(cfg.get("max_files_per_scope", 40)))["retired_for_cap"]
    return {"filed": report.get("candidates", 0),
            "actions": report.get("actions", {}),
            "proposals_held": report.get("proposals_held", 0),
            "proposals_expired": report.get("proposals_expired", 0),
            "retired_for_cap": retired}

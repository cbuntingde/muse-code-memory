"""Automated cleanup, merging, and consolidation.

Jobs (all idempotent, safe to run on every SessionEnd):
- dedupe:      drop near-duplicate bullets across topic files (SequenceMatcher)
- merge:       fold highly similar topics into one file
- expire:      delete expired records; archive stale low-value ones
- trim:        shrink oversized files by keeping highest-signal bullets
- consolidate_session: file pending candidates into topics (create/append/skip)
- cleanup:     run the full pass and return a report dict
"""

from __future__ import annotations

import difflib
import time
from pathlib import Path

from . import paths
from .extractor import candidate_to_bullet, redact_secrets
from .store import (MemoryRecord, MemoryStore, ScopeStore, frontmatter_block,
                      summary_of)

BULLET_PREFIXES = ("- ", "* ")


def _header_of(target: Path) -> str:
    """Raw frontmatter block of a topic file, "" when absent/unreadable."""
    try:
        return frontmatter_block(target.read_text(encoding="utf-8"))
    except OSError:
        return ""


def _bullets(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(BULLET_PREFIXES) and len(s) > 4:
            out.append(s)
    return out


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedupe_scope(store: ScopeStore, threshold: float = 0.85) -> dict:
    """Remove near-duplicate bullets, keeping the copy in the most important file."""
    records = sorted(store.read_all(), key=lambda r: (-r.importance, r.file))
    seen: list[str] = []
    removed = 0
    for rec in records:
        kept: list[str] = []
        changed = False
        for line in rec.content.splitlines():
            s = line.strip()
            if s.startswith(BULLET_PREFIXES) and len(s) > 4:
                if any(similarity(s, prev) >= threshold for prev in seen):
                    removed += 1
                    changed = True
                    continue
                seen.append(s)
            kept.append(line)
        if changed:
            target = store.dir / rec.file
            header = _header_of(target)
            target.write_text(header + "\n".join(kept).rstrip() + "\n",
                              encoding="utf-8")
            store.set_file_meta(rec.file, updated=time.time())
    if removed:
        store.refresh_index()
    return {"scope": store.scope, "bullets_removed": removed}


def merge_scope(store: ScopeStore, threshold: float = 0.72) -> dict:
    """Fold whole topics into a sibling when their bodies are highly similar."""
    records = store.read_all()
    merged = 0
    consumed: set[str] = set()
    for i, rec in enumerate(records):
        if rec.file in consumed:
            continue
        for other in records[i + 1:]:
            if other.file in consumed:
                continue
            if not rec.content.strip() or not other.content.strip():
                continue
            if similarity(rec.content[:2000], other.content[:2000]) < threshold:
                continue
            # Keep the more important / larger file, append the other's unique bullets.
            keep, drop = (rec, other) if (
                rec.importance, len(rec.content)) >= (other.importance, len(other.content)) \
                else (other, rec)
            keep_bullets = _bullets(keep.content)
            additions = [b for b in _bullets(drop.content)
                         if not any(similarity(b, k) >= 0.85 for k in keep_bullets)]
            if additions:
                store.append_to_topic(keep.file, "\n".join(additions))
            meta_keep = store.file_meta(keep.file)
            meta_drop = store.file_meta(drop.file)
            store.set_file_meta(
                keep.file,
                tags=sorted(set(meta_keep.get("tags", [])) | set(meta_drop.get("tags", []))),
                importance=max(int(meta_keep.get("importance", 3)),
                               int(meta_drop.get("importance", 3))),
                access_count=int(meta_keep.get("access_count", 0)) + int(meta_drop.get("access_count", 0)),
            )
            store.archive_topic(drop.file)
            consumed.add(drop.file)
            merged += 1
    return {"scope": store.scope, "topics_merged": merged}


def expire_scope(store: ScopeStore, stale_days: int = 90,
                 now: float | None = None) -> dict:
    """Delete expired topics; archive stale, low-importance, never-recalled ones."""
    now = now if now is not None else time.time()
    expired = 0
    archived = 0
    for rec in store.read_all():
        if rec.expires and rec.expires <= now:
            store.delete_topic(rec.file)
            expired += 1
            continue
        age_days = (now - (rec.updated or now)) / 86400
        if (age_days > stale_days and rec.importance <= 2
                and rec.access_count == 0 and rec.source not in ("manual",)):
            store.archive_topic(rec.file)
            archived += 1
    return {"scope": store.scope, "expired": expired, "archived": archived}


def trim_scope(store: ScopeStore, max_chars: int = 12000) -> dict:
    """Shrink oversized topic files, keeping highest-signal content."""
    trimmed = 0
    for rec in store.read_all():
        if len(rec.content) <= max_chars:
            continue
        bullets = _bullets(rec.content)
        if len(bullets) > 3:
            # Keep first (usually the definition) + longest (usually most specific).
            scored = sorted(bullets, key=len, reverse=True)
            keep = [bullets[0]] + [b for b in scored if b != bullets[0]]
            buf: list[str] = []
            total = 0
            for b in keep:
                if total + len(b) > max_chars - 200:
                    break
                buf.append(b)
                total += len(b)
            body = "\n".join(buf) + "\n"
        else:
            body = rec.content[:max_chars]
        target = store.dir / rec.file
        target.write_text(_header_of(target) + body.rstrip() + "\n",
                          encoding="utf-8")
        store.set_file_meta(rec.file, updated=time.time())
        trimmed += 1
    if trimmed:
        store.refresh_index()
    return {"scope": store.scope, "files_trimmed": trimmed}



def enforce_file_cap(store: ScopeStore, max_files: int = 40) -> dict:
    records = sorted(store.read_all(),
                     key=lambda r: (r.importance, r.access_count, r.updated or 0.0))
    overflow = len(records) - max_files
    retired = 0
    for rec in records:
        if overflow <= 0:
            break
        if rec.importance >= 5 or rec.pinned:
            continue  # standing rules and pins are never auto-retired
        store.archive_topic(rec.file)
        overflow -= 1
        retired += 1
    return {"scope": store.scope, "retired_for_cap": retired}


def file_candidate(cand: dict, store: MemoryStore,
                   dedupe_threshold: float = 0.85) -> dict:
    """File one candidate: skip if duplicate, append if near-match, else create/append topic."""
    scope = store.get_scope(cand.get("scope", "project-personal"))
    if scope is None:
        scope = store.get_scope("project-personal") or next(iter(store.scopes.values()))
    # Defense in depth: scrub anything a caller filed without redacting,
    # so secrets can never reach topic files (or slug-derived filenames).
    clean_text, _ = redact_secrets(cand.get("text", ""))
    clean_topic, _ = redact_secrets(str(cand.get("topic", "")))
    cand = {**cand, "text": clean_text, "topic": clean_topic}
    bullet = candidate_to_bullet(cand)
    text = cand["text"]
    # 1. Exact/near duplicate anywhere in scope -> skip (but refresh access).
    for rec in scope.read_all():
        if text.strip().lower() in rec.content.lower():
            scope.touch_access(rec.file)
            return {"action": "duplicate", "file": rec.file, "scope": scope.scope}
        for existing in _bullets(rec.content):
            if similarity(existing, bullet) >= dedupe_threshold:
                scope.touch_access(rec.file)
                return {"action": "duplicate", "file": rec.file, "scope": scope.scope}
    # 2. Near-match topic -> append.
    best: MemoryRecord | None = None
    best_score = 0.0
    for rec in scope.read_all():
        score = similarity(rec.title + " " + summary_of(rec.content, 200),
                           cand.get("topic", "") + " " + text[:200])
        if score > best_score:
            best_score = score
            best = rec
    if best is not None and best_score >= 0.45:
        scope.append_to_topic(best.file, bullet)
        scope.touch_access(best.file)
        return {"action": "appended", "file": best.file, "scope": scope.scope}
    # 3. New topic file.
    topic = cand.get("topic", "note") or "note"
    title = topic.replace("-", " ").title()
    body = f"# {title}\n\n{bullet}\n"
    record = scope.write_topic(topic, body, meta={
        "tags": cand.get("tags", []),
        "importance": cand.get("importance", 3),
        "source": cand.get("source", "auto"),
        "session_id": cand.get("session_id", ""),
    })
    return {"action": "created", "file": record.file, "scope": scope.scope}


def consolidate_session(candidates: list[dict], store: MemoryStore,
                        dedupe_threshold: float = 0.85) -> dict:
    actions: dict[str, int] = {}
    files: list[str] = []
    for cand in candidates:
        try:
            result = file_candidate(cand, store, dedupe_threshold)
        except OSError:
            continue
        actions[result["action"]] = actions.get(result["action"], 0) + 1
        files.append(f"{result['scope']}/{result['file']}")
    return {"actions": actions, "files": files, "candidates": len(candidates)}


def cleanup(store: MemoryStore, cfg: dict | None = None) -> dict:
    """Full maintenance pass across scopes; returns a report dict."""
    cfg = cfg or {}
    report: dict[str, dict] = {}
    for scope in store.scopes.values():
        report[scope.scope] = {
            **dedupe_scope(scope, float(cfg.get("dedupe_threshold", 0.85))),
            **merge_scope(scope, float(cfg.get("merge_threshold", 0.72))),
            **expire_scope(scope, int(cfg.get("stale_days", 90))),
            **trim_scope(scope, int(cfg.get("max_chars_per_file", 12000))),
            **enforce_file_cap(scope, int(cfg.get("max_files_per_scope", 40))),
        }
    # Drop the cached RAG indexes so the next recall rebuilds from cleaned files.
    for scope in store.scopes.values():
        try:
            (scope.dir / paths.RAG_INDEX).unlink(missing_ok=True)
        except OSError:
            pass
    return report


def pending_path(scope: ScopeStore) -> Path:
    return scope.dir / paths.PENDING_FILE

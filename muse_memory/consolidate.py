"""Automated cleanup, merging, and consolidation.

Jobs (all idempotent, safe to run on every SessionEnd):
- retire_fragments: archive machine artifacts (payload JSON, code dumps)
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
from functools import wraps

from . import paths
from .locking import scope_lock
from .extractor import candidate_to_bullet, fragment_reason, redact_secrets
from .store import (MemoryRecord, MemoryStore, ScopeStore, frontmatter_block,
                   slugify_topic, summary_of)

BULLET_PREFIXES = ("- ", "* ")


def _locked_job(method):
    @wraps(method)
    def wrapped(store, *args, **kwargs):
        with scope_lock(store.dir):
            store._meta_loaded = False
            return method(store, *args, **kwargs)
    return wrapped


def _locked_filing(method):
    @wraps(method)
    def wrapped(cand, store, *args, **kwargs):
        scope = store.get_scope(cand.get('scope', 'project-personal'))
        if scope is None:
            raise ValueError('requested memory scope is unavailable')
        with scope_lock(scope.dir):
            scope._meta_loaded = False
            return method(cand, store, *args, **kwargs)
    return wrapped


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


# Shared SequenceMatcher budget for one maintenance run: caps worst-case
# cleanup cost so SessionEnd stays inside its hook timeout. Exhaustion is
# always safe-direction (content is kept, merges are skipped) and reported
# via comparisons_capped. Filing (file_candidate) never takes a budget:
# it must not silently skip or duplicate memories.
SIMILARITY_BUDGET = 40000


class SimilarityBudget:
    """Deterministic cap on similarity() calls; exhausted reads as dissimilar."""

    def __init__(self, limit: int = SIMILARITY_BUDGET) -> None:
        self.limit = limit
        self.used = 0
        self.exhausted = False

    def score(self, a: str, b: str) -> float | None:
        """similarity(a, b), or None once the budget is spent (keep content)."""
        if self.used >= self.limit:
            self.exhausted = True
            return None
        self.used += 1
        return similarity(a, b)


@_locked_job
def dedupe_scope(store: ScopeStore, threshold: float = 0.85,
                 budget: SimilarityBudget | None = None) -> dict:
    """Remove near-duplicate bullets, keeping the copy in the most important file."""
    records = sorted(store.read_all(), key=lambda r: (-r.importance, r.file))
    seen: list[str] = []
    seen_exact: set[str] = set()
    removed = 0
    capped = False
    for rec in records:
        if budget is not None and budget.exhausted:
            capped = True
            break
        kept: list[str] = []
        changed = False
        for line in rec.content.splitlines():
            s = line.strip()
            if s.startswith(BULLET_PREFIXES) and len(s) > 4:
                if s in seen_exact:
                    removed += 1
                    changed = True
                    continue
                duplicate = False
                for prev in seen:
                    if budget is None:
                        if similarity(s, prev) >= threshold:
                            duplicate = True
                            break
                    else:
                        sim = budget.score(s, prev)
                        if sim is None:
                            capped = True
                            break
                        if sim >= threshold:
                            duplicate = True
                            break
                if duplicate:
                    removed += 1
                    changed = True
                    continue
                seen.append(s)
                seen_exact.add(s)
            kept.append(line)
        if changed:
            target = store.dir / rec.file
            header = _header_of(target)
            target.write_text(header + "\n".join(kept).rstrip() + "\n",
                              encoding="utf-8")
            store.set_file_meta(rec.file, updated=time.time())
    if removed:
        store.refresh_index()
    return {"scope": store.scope, "bullets_removed": removed,
            "comparisons_capped": capped}


@_locked_job
def merge_scope(store: ScopeStore, threshold: float = 0.72,
                 budget: SimilarityBudget | None = None) -> dict:
    """Fold whole topics into a sibling when their bodies are highly similar."""
    records = store.read_all()
    merged = 0
    capped = False
    consumed: set[str] = set()

    def _score(a: str, b: str, cutoff: float) -> bool | None:
        """True/False on a similarity cutoff, None when the budget is spent."""
        if budget is None:
            return similarity(a, b) >= cutoff
        sim = budget.score(a, b)
        return None if sim is None else sim >= cutoff

    for i, rec in enumerate(records):
        if budget is not None and budget.exhausted:
            capped = True
            break
        if rec.file in consumed or rec.pinned:
            continue
        for other in records[i + 1:]:
            if other.file in consumed or other.pinned:
                continue
            if not rec.content.strip() or not other.content.strip():
                continue
            pair = _score(rec.content[:2000], other.content[:2000], threshold)
            if pair is None:
                capped = True
                break
            if not pair:
                continue
            # Keep the more important / larger file, append the other's unique bullets.
            keep, drop = (rec, other) if (
                rec.importance, len(rec.content)) >= (other.importance, len(other.content)) \
                else (other, rec)
            keep_bullets = _bullets(keep.content)
            additions = []
            for b in _bullets(drop.content):
                unique = True
                for k in keep_bullets:
                    known = _score(b, k, 0.85)
                    if known is None:
                        capped = True
                        break
                    if known:
                        unique = False
                        break
                if capped:
                    break
                if unique:
                    additions.append(b)
            if capped:
                break  # budget spent mid-merge: skip it, keep both topics
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
            # Do not continue comparing a record that this merge retired.
            if drop is rec:
                break
            rec.content = store.read_record(store.dir / rec.file).content
    return {"scope": store.scope, "topics_merged": merged,
            "comparisons_capped": capped}


@_locked_job
def expire_scope(store: ScopeStore, stale_days: int = 90,
                 now: float | None = None) -> dict:
    """Delete expired topics; archive stale, low-importance, never-recalled ones."""
    now = now if now is not None else time.time()
    expired = 0
    archived = 0
    for rec in store.read_all():
        if rec.pinned:
            continue
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


@_locked_job
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
                    if not buf:
                        buf.append(b[:max_chars - 200])
                        total = len(buf[0])
                    continue
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



@_locked_job
def retire_fragments(store: ScopeStore) -> dict:
    """Archive auto-captured machine artifacts (payload JSON, code dumps).

    Old captures and any payload that slipped past the extractor guard
    can hold raw tool JSON, line-numbered code, escaped strings, or
    mojibake. Retire those topics (recoverable in archive/, still
    searchable with archive recall). Manual saves and pinned topics are
    kept; a human put those there on purpose.
    """
    retired = 0
    for rec in store.read_all():
        if rec.pinned or rec.source == "manual":
            continue
        if fragment_reason(rec.content) is None:
            continue
        if store.archive_topic(rec.file, refresh=False):
            retired += 1
    if retired:
        store.refresh_index()
    return {"scope": store.scope, "fragments_retired": retired}


@_locked_job
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
        if store.archive_topic(rec.file, refresh=False):
            overflow -= 1
            retired += 1
    if retired:
        store.refresh_index()
    return {"scope": store.scope, "retired_for_cap": retired}


@_locked_filing
def file_candidate(cand: dict, store: MemoryStore,
                   dedupe_threshold: float = 0.85,
                   records: dict[str, list[MemoryRecord]] | None = None,
                   ) -> dict:
    """File one candidate: skip if duplicate, append if near-match, else create/append topic.

    records is an optional per-scope mirror (scope name -> records) shared
    across a batch: each scope is read from disk at most once and the
    mirror is updated on append/create, so filing N candidates costs one
    scan per scope instead of N. Callers filing a single candidate omit it.
    """
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
    if records is not None:
        scanned = records.get(scope.scope)
        if scanned is None:
            scanned = scope.read_all()
            records[scope.scope] = scanned
    else:
        scanned = scope.read_all()
    # 1. Exact/near duplicate anywhere in scope -> skip (but refresh access).
    for rec in scanned:
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
    for rec in scanned:
        score = similarity(rec.title + " " + summary_of(rec.content, 200),
                           cand.get("topic", "") + " " + text[:200])
        if score > best_score:
            best_score = score
            best = rec
    if best is not None and best_score >= 0.45:
        scope.append_to_topic(best.file, bullet)
        scope.touch_access(best.file)
        if records is not None:
            best.content = best.content.rstrip() + "\n\n" + bullet + "\n"
        return {"action": "appended", "file": best.file, "scope": scope.scope}
    # 3. New topic file.
    topic = cand.get("topic", "note") or "note"
    filename = slugify_topic(topic) + ".md"
    if (scope.dir / filename).is_file():
        scope.append_to_topic(filename, bullet)
        if records is not None:
            for existing in scanned:
                if existing.file == filename:
                    existing.content = scope.read_record(scope.dir / filename).content
                    break
        return {"action": "appended", "file": filename, "scope": scope.scope}
    title = topic.replace("-", " ").title()
    body = f"# {title}\n\n{bullet}\n"
    record = scope.write_topic(topic, body, meta={
        "tags": cand.get("tags", []),
        "importance": cand.get("importance", 3),
        "source": cand.get("source", "auto"),
        "session_id": cand.get("session_id", ""),
    })
    if records is not None:
        records.setdefault(scope.scope, []).append(record)
    return {"action": "created", "file": record.file, "scope": scope.scope}


def consolidate_session(candidates: list[dict], store: MemoryStore,
                        dedupe_threshold: float = 0.85) -> dict:
    actions: dict[str, int] = {}
    files: list[str] = []
    errors: list[str] = []
    mirror: dict[str, list[MemoryRecord]] = {}
    for cand in candidates:
        try:
            result = file_candidate(cand, store, dedupe_threshold,
                                    records=mirror)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(str(exc))
            continue
        actions[result["action"]] = actions.get(result["action"], 0) + 1
        files.append(f"{result['scope']}/{result['file']}")
    return {"actions": actions, "files": files, "candidates": len(files),
            "errors": errors}


def cleanup(store: MemoryStore, cfg: dict | None = None) -> dict:
    """Full maintenance pass across scopes; returns a report dict."""
    cfg = cfg or {}
    budget = SimilarityBudget()
    report: dict[str, dict] = {}
    for scope in store.scopes.values():
        fragments_rep = retire_fragments(scope)
        dedupe_rep = dedupe_scope(scope, float(cfg.get("dedupe_threshold", 0.85)),
                                  budget=budget)
        merge_rep = merge_scope(scope, float(cfg.get("merge_threshold", 0.72)),
                                budget=budget)
        capped = bool(dedupe_rep.pop("comparisons_capped", False)
                      or merge_rep.pop("comparisons_capped", False))
        report[scope.scope] = {
            **fragments_rep,
            **dedupe_rep,
            **merge_rep,
            **expire_scope(scope, int(cfg.get("stale_days", 90))),
            **trim_scope(scope, int(cfg.get("max_chars_per_file", 12000))),
            **enforce_file_cap(scope, int(cfg.get("max_files_per_scope", 40))),
            "comparisons_capped": capped,
        }
    # Drop the cached RAG indexes so the next recall rebuilds from cleaned files.
    for scope in store.scopes.values():
        try:
            (scope.dir / paths.RAG_INDEX).unlink(missing_ok=True)
        except OSError:
            pass
    return report

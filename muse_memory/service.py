"""Single application service behind the CLI, MCP server, and hooks.

Every adapter (argparse CLI, MCP stdio tools, lifecycle hooks) delegates
here so recall/save/list/stats behave identically on all surfaces. Hooks
that must stay read-only call recall with read_only=True and queue their
access signals instead of touching topic metadata.
"""

from __future__ import annotations

import os

from . import config as config_mod
from . import dates, extractor, lifecycle, listing, rag
from .store import MemoryStore


class MemoryService:
    def __init__(self, cwd: str | os.PathLike | None = None,
                 scopes: list[str] | None = None):
        self.store = MemoryStore(cwd, scopes=scopes)
        self.cfg = config_mod.load(self.store.cwd)

    # -- recall ---------------------------------------------------------
    def parse_range(self, query: str, since: str | None = None,
                    until: str | None = None):
        """(cleaned_query, since_ts, until_ts); ValueError on bad dates."""
        cleaned, auto = dates.split_query(query)
        since_ts, until_ts = auto if auto else (None, None)
        for value, is_start in ((since, True), (until, False)):
            if value:
                parsed = dates.parse_day(str(value))
                if parsed is None:
                    key = "since" if is_start else "until"
                    raise ValueError(f"{key} must be YYYY-MM-DD, got {value!r}")
                if is_start:
                    since_ts = parsed[0]
                else:
                    until_ts = parsed[1]
        if since_ts is not None and until_ts is not None and since_ts > until_ts:
            raise ValueError(
                f"empty date range ({dates.label(since_ts, until_ts)}): "
                "since is after until")
        return cleaned, since_ts, until_ts

    def recall(self, query: str, top_k: int | None = None,
               include_archive: bool = False,
               since: str | None = None, until: str | None = None,
               read_only: bool = False, access: str = "touch"):
        """(cleaned, since_ts, until_ts, hits). access: touch|queue|none.

        top_k of None or 0 selects the configured default: 0 is the
        long-standing "unspecified" sentinel used by the CLI (argparse
        default) and the MCP tool, so an explicit 0 cannot request
        zero hits.
        """
        cleaned, since_ts, until_ts = self.parse_range(query, since, until)
        hits = rag.search_all(
            list(self.store.scopes.values()), cleaned,
            top_k=int(top_k or self.cfg["recall_top_k"]),
            min_score=float(self.cfg["min_score"]),
            include_archive=include_archive,
            since=since_ts, until=until_ts, read_only=read_only)
        if access == "touch":
            self._touch(hits)
        elif access == "queue":
            by_scope: dict[str, list[str]] = {}
            for hit in hits[:3]:
                if not hit.archived:
                    by_scope.setdefault(hit.scope, []).append(hit.file)
            for scope_name, files in by_scope.items():
                lifecycle.queue_access(self.store, scope_name, files)
        return cleaned, since_ts, until_ts, hits

    def _touch(self, hits) -> None:
        for hit in hits[:3]:
            if hit.archived:
                continue
            scope = self.store.get_scope(hit.scope)
            if scope is not None:
                try:
                    scope.touch_access(hit.file)
                except OSError:
                    pass

    def recall_text(self, query: str, **kwargs) -> str:
        _cleaned, since_ts, until_ts, hits = self.recall(query, **kwargs)
        if not hits:
            return "(no relevant memory)"
        header = (f"Relevant memory ({dates.label(since_ts, until_ts)}):"
                  if since_ts is not None or until_ts is not None else None)
        return rag.format_context(
            hits, int(self.cfg["recall_max_chars"]), header=header)

    def explain(self, query: str, top_k: int | None = None,
                since: str | None = None, until: str | None = None):
        cleaned, since_ts, until_ts = self.parse_range(query, since, until)
        return rag.explain_all(
            list(self.store.scopes.values()), cleaned,
            top_k=int(top_k or self.cfg["recall_top_k"]),
            since=since_ts, until=until_ts)

    # -- save -----------------------------------------------------------
    def save(self, text: str, topic: str | None = None,
             scope: str | None = None, importance: int = 3,
             tags: list[str] | None = None, source: str = "manual",
             supersedes: str | None = None) -> dict:
        """File one explicit memory; result dict from consolidate.

        Raises ValueError for an unknown scope. A result with action
        "blocked-secret" means the committed shared scope refused the text.
        """
        from . import consolidate as cons
        # file_candidate redacts before filing; keep the original text for
        # the fail-closed shared-scope check, which must see credentials.
        _cleaned, secret_kinds = extractor.redact_secrets(text)
        scope_name = scope or self.cfg.get("default_scope", "project-personal")
        available = sorted(self.store.scopes)
        if scope_name not in available:
            known = ", ".join(available) or "none"
            raise ValueError(f"unknown scope {scope_name!r} (available: {known})")
        if not text.strip():
            raise ValueError("empty text")
        cand = {"text": text, "kind": "note",
                "topic": topic or extractor.suggest_topic(text, "note"),
                "importance": importance, "tags": list(tags or []),
                "scope": scope_name, "source": source, "session_id": ""}
        # Archive the replaced topic first so the new fact files fresh
        # instead of being fuzzy-appended to the topic it replaces.
        archived_old = None
        if supersedes:
            old = supersedes.strip().lstrip("/").replace("\\", "/").split("/")[-1]
            target_scope = self.store.get_scope(scope_name)
            if (target_scope is not None
                    and (target_scope.dir / old).is_file()
                    and target_scope.archive_topic(old)):
                archived_old = old
        result = cons.file_candidate(cand, self.store)
        result["redacted"] = secret_kinds
        if archived_old and result.get("file"):
            target_scope = self.store.get_scope(scope_name)
            if target_scope is not None:
                target_scope.set_file_meta(result["file"],
                                           supersedes=archived_old)
                target_scope.set_file_meta(f"archive/{archived_old}",
                                           superseded_by=result["file"])
            result["superseded"] = archived_old
        return result

    # -- listing / stats --------------------------------------------------
    def list_rows(self, scope=None, tag=None, min_importance: int = 0):
        return listing.list_rows(self.store, scope, tag, min_importance)

    def stats(self) -> dict:
        out: dict = {}
        for name, scope in self.store.scopes.items():
            records = scope.read_all()
            histogram: dict[str, int] = {}
            for rec in records:
                histogram[str(rec.importance)] = histogram.get(
                    str(rec.importance), 0) + 1
            out[name] = {
                "dir": str(scope.dir), "files": len(records),
                "chars": sum(len(r.content) for r in records),
                "importance": histogram,
                "pending": scope.pending_count(),
                "archived": scope.archive_count(),
                "trashed": len(scope.trashed_files()),
                "index": rag.index_health(scope)}
        return out

    # -- proposals / maintenance -------------------------------------------
    def approve_pending(self) -> dict:
        return lifecycle.file_pending(
            self.store, int(self.cfg["max_pending"]),
            float(self.cfg["dedupe_threshold"]), approve_all=True)

    def cleanup(self) -> dict:
        from . import consolidate as cons
        return cons.cleanup(self.store, self.cfg)

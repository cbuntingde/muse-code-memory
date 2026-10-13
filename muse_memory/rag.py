"""Built-in RAG retrieval over memory scopes. Standard library only.

Pipeline: chunk Markdown records -> SQLite FTS5 (stdlib sqlite3) BM25
ranking fused with recency / importance / access signals. The index
persists as a disposable `.rag-index.db` next to each scope and is rebuilt
from the Markdown topics whenever their signature changes; deleting it
loses nothing. When FTS5 is unavailable in the local SQLite build, search
falls back to the in-memory BM25 implementation below.

This is deliberately dependency-free: hooks run with a cleared environment
outside the sandbox, so numpy/sentence-transformers cannot be assumed.
Hybrid lexical + behavioral scoring is the right baseline for short
memory notes; an optional embeddings backend can be layered later.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

from . import paths
from .locking import scope_lock
from .store import MemoryRecord, ScopeStore, utc_now

TOKEN_RE = re.compile(r"[a-z0-9]+")
HEADING_SPLIT_RE = re.compile(r"(?m)^(?=#{1,3}\s)")

STOPWORDS = frozenset("""
a an and are as at be but by for from has have he her his in into is it its
of on or our she that the their them there these they this to was we were
will with you your not no yes if then than so such can could should would
do does did done its it's i'm i've i'll we'd we've they'll they're
""".split())

# BM25-ish constants
K1 = 1.2
B = 0.75


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def chunk_record(rec: MemoryRecord, max_chars: int = 1200) -> list[dict]:
    """Split a record into retrievable chunks (heading sections, then paragraphs)."""
    body = rec.content.strip()
    if not body:
        return []
    sections = [s for s in HEADING_SPLIT_RE.split(body) if s.strip()] or [body]
    chunks: list[dict] = []
    for section in sections:
        section = section.strip()
        if len(section) <= max_chars:
            chunks.append(section)
            continue
        paras = [p.strip() for p in section.split("\n\n") if p.strip()]
        buf = ""
        for para in paras:
            if len(para) > max_chars:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.extend(para[i:i + max_chars]
                              for i in range(0, len(para), max_chars))
                continue
            if len(buf) + len(para) + 2 <= max_chars:
                buf = (buf + "\n\n" + para).strip()
            else:
                if buf:
                    chunks.append(buf)
                buf = para[:max_chars]
        if buf:
            chunks.append(buf)
    out = []
    for i, text in enumerate(chunks):
        out.append({
            "chunk_id": f"{rec.id}:{i}",
            "record_id": rec.id,
            "scope": rec.scope,
            "file": rec.file,
            "title": rec.title,
            "text": text,
            "importance": rec.importance,
            "updated": rec.updated,
            "created": rec.created,
            "accessed": rec.accessed,
            "access_count": rec.access_count,
        })
    return out


@dataclass
class ScoredChunk:
    chunk_id: str
    scope: str
    file: str
    title: str
    text: str
    score: float
    lexical: float
    importance: int
    archived: bool = False


class RagIndex:
    def __init__(self) -> None:
        self.chunks: list[dict] = []
        self.doc_freq: dict[str, int] = {}
        self.avg_len: float = 1.0
        self.built_at: float = 0.0
        self.source_mtime: float = 0.0
        self.source_signature: str = ''
        self._lengths: list[int] = []
        self._tokens: list[list[str]] = []
        self._title_tokens: list[set[str]] = []
        self._counters: list[Counter] = []
        self._db_path: str | None = None  # set when backed by the FTS5 store

    def _derive(self) -> None:
        """Rebuild in-memory BM25 structures from self.chunks."""
        self._tokens = [tokenize(c.get("text", "") + " " + c.get("title", ""))
                        for c in self.chunks]
        self._lengths = [len(t) or 1 for t in self._tokens]
        self._title_tokens = [set(tokenize(c.get("title", "")))
                              for c in self.chunks]
        self._counters = [Counter(t) for t in self._tokens]
        self.doc_freq = {}
        for toks in self._tokens:
            for term in set(toks):
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        self.avg_len = (sum(self._lengths) / len(self._lengths)
                        if self._lengths else 1.0)

    # -- build ------------------------------------------------------------
    def build(self, records: list[MemoryRecord], source_mtime: float = 0.0) -> None:
        self.chunks = []
        for rec in records:
            self.chunks.extend(chunk_record(rec))
        self._derive()
        self.built_at = utc_now()
        self.source_mtime = source_mtime
        self._db_path = None

    # -- search -----------------------------------------------------------
    def _bm25(self, query_terms: list[str], idx: int) -> float:
        counts = self._counters[idx]
        length = self._lengths[idx]
        n_docs = len(self.chunks) or 1
        score = 0.0
        for term in set(query_terms):
            tf = counts.get(term, 0)
            if not tf:
                continue
            df = self.doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + K1 * (1 - B + B * length / self.avg_len)
            score += idf * (tf * (K1 + 1)) / denom
        # Title hit bonus: query terms appearing in the title matter for memory recall.
        overlap = len(set(query_terms) & self._title_tokens[idx])
        if overlap:
            score *= 1 + 0.25 * overlap
        return score

    def _search_fts(self, q_terms: list[str], top_k: int,
                    min_score: float, now: float,
                    since: float | None,
                    until: float | None) -> list[ScoredChunk] | None:
        """FTS5 BM25 search over the persisted index; None means unavailable."""
        match = " OR ".join(
            '"' + term.replace('"', '""') + '"' for term in dict.fromkeys(q_terms))
        if not match:
            return []
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT chunk_id, scope, file, title, text, importance,"
                    " updated, created, accessed, access_count,"
                    " bm25(chunks_fts, 0.0, 0.0, 0.0, 4.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)"
                    " FROM chunks_fts WHERE chunks_fts MATCH ?"
                    " ORDER BY bm25(chunks_fts, 0.0, 0.0, 0.0, 4.0, 1.0,"
                    " 0.0, 0.0, 0.0, 0.0, 0.0)",
                    (match,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        # FTS5 bm25() is negative-better, but its sign flips on tiny
        # corpora (IDF goes negative when a term is in most chunks), so
        # derive a positive lexical score relative to the worst match.
        worst = max((float(r[10] or 0.0) for r in rows), default=0.0)
        scored: list[ScoredChunk] = []
        for (chunk_id, scope, file, title, text, importance, updated,
             created, accessed, access_count, rank) in rows:
            chunk = {"importance": int(importance or 3),
                     "updated": float(updated or 0.0),
                     "created": float(created or 0.0),
                     "accessed": float(accessed or 0.0),
                     "access_count": int(access_count or 0)}
            if not _in_range(chunk, since, until):
                continue
            lexical = worst - float(rank or 0.0) + 1.0
            if lexical <= 0:
                continue
            score = _fuse(lexical, chunk, now)
            if score >= min_score:
                scored.append(ScoredChunk(
                    chunk_id=str(chunk_id), scope=str(scope), file=str(file),
                    title=str(title), text=str(text), score=score,
                    lexical=lexical, importance=int(importance or 3)))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0,
               now: float | None = None,
               since: float | None = None,
               until: float | None = None) -> list[ScoredChunk]:
        q_terms = tokenize(query)
        if not q_terms or not self.chunks:
            return []
        if self._db_path:
            hits = self._search_fts(q_terms, top_k, min_score,
                                    now if now is not None else utc_now(),
                                    since, until)
            if hits is not None:
                return hits
        now = now if now is not None else utc_now()
        scored: list[ScoredChunk] = []
        for i, chunk in enumerate(self.chunks):
            if not _in_range(chunk, since, until):
                continue
            lexical = self._bm25(q_terms, i)
            if lexical <= 0:
                continue
            score = _fuse(lexical, chunk, now)
            if score >= min_score:
                scored.append(ScoredChunk(
                    chunk_id=chunk["chunk_id"], scope=chunk["scope"],
                    file=chunk["file"], title=chunk["title"], text=chunk["text"],
                    score=score, lexical=lexical,
                    importance=int(chunk.get("importance", 3)),
                    archived=bool(chunk.get("archived", False)),
                ))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    # -- persistence ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {"chunks": self.chunks, "doc_freq": self.doc_freq,
                "avg_len": self.avg_len, "built_at": self.built_at,
                "source_mtime": self.source_mtime,
                "source_signature": self.source_signature}

    @classmethod
    def from_dict(cls, data: dict) -> "RagIndex":
        """Legacy JSON-cache shape, kept so old callers/tests still parse."""
        idx = cls()
        idx.chunks = data.get("chunks", [])
        idx.avg_len = data.get("avg_len", 1.0) or 1.0
        idx.built_at = data.get("built_at", 0.0)
        idx.source_mtime = data.get("source_mtime", 0.0)
        idx.source_signature = data.get('source_signature', '')
        idx._derive()
        idx.avg_len = data.get("avg_len", 1.0) or idx.avg_len
        return idx


_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE chunks_fts USING fts5("
    "chunk_id UNINDEXED, scope UNINDEXED, file UNINDEXED, title, text,"
    " importance UNINDEXED, updated UNINDEXED, created UNINDEXED,"
    " accessed UNINDEXED, access_count UNINDEXED)")


def _write_db(store: ScopeStore, idx: RagIndex, signature: str) -> bool:
    """Persist idx as a disposable FTS5 database; False when unavailable."""
    index_path = store.dir / paths.RAG_INDEX
    tmp = store.dir / f".rag-index.db.tmp-{os.getpid()}"
    try:
        store.dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp))
        try:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(_FTS_SCHEMA)
            conn.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [("source_signature", signature),
                 ("built_at", str(idx.built_at)),
                 ("source_mtime", str(idx.source_mtime))])
            conn.executemany(
                "INSERT INTO chunks_fts VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(str(c.get("chunk_id", "")), str(c.get("scope", "")),
                  str(c.get("file", "")), str(c.get("title", "")),
                  str(c.get("text", "")), int(c.get("importance", 3)),
                  float(c.get("updated", 0.0)), float(c.get("created", 0.0)),
                  float(c.get("accessed", 0.0)),
                  int(c.get("access_count", 0))) for c in idx.chunks])
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, index_path)
        for suffix in ("-wal", "-shm"):
            try:
                (store.dir / (paths.RAG_INDEX + suffix)).unlink(missing_ok=True)
            except OSError:
                pass
        return True
    except (OSError, sqlite3.Error):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _read_db(index_path) -> RagIndex | None:
    """Load a persisted FTS5 index, or None when unreadable/misshapen."""
    try:
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            rows = conn.execute(
                "SELECT chunk_id, scope, file, title, text, importance,"
                " updated, created, accessed, access_count"
                " FROM chunks_fts").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    idx = RagIndex()
    idx.chunks = [{
        "chunk_id": str(r[0]), "record_id": str(r[0]).rsplit(":", 1)[0],
        "scope": str(r[1]), "file": str(r[2]), "title": str(r[3]),
        "text": str(r[4]), "importance": int(r[5] or 3),
        "updated": float(r[6] or 0.0), "created": float(r[7] or 0.0),
        "accessed": float(r[8] or 0.0), "access_count": int(r[9] or 0),
    } for r in rows]
    idx.source_signature = meta.get("source_signature", "")
    try:
        idx.built_at = float(meta.get("built_at", 0.0))
        idx.source_mtime = float(meta.get("source_mtime", 0.0))
    except ValueError:
        idx.built_at = idx.source_mtime = 0.0
    idx._derive()
    idx._db_path = str(index_path)
    return idx


def _fuse_parts(lexical: float, chunk: dict, now: float) -> dict:
    """Lexical score plus each behavioral boost, for scoring and explain."""
    importance = int(chunk.get("importance", 3))
    imp_boost = 1 + 0.08 * (importance - 3)  # 0.84x .. 1.16x
    updated = float(chunk.get("updated", 0.0)) or now
    age_days = max(0.0, (now - updated) / 86400)
    recency = 1 / (1 + age_days / 45)  # half-weight after ~45 days
    recency_boost = 0.85 + 0.3 * recency
    use = min(int(chunk.get("access_count", 0)), 20)
    use_boost = 1 + 0.01 * use
    return {"lexical": lexical, "importance_boost": imp_boost,
            "recency_boost": recency_boost, "use_boost": use_boost,
            "score": lexical * imp_boost * recency_boost * use_boost}


def _fuse(lexical: float, chunk: dict, now: float) -> float:
    """Fuse lexical score with behavioral signals (recency/importance/use)."""
    return _fuse_parts(lexical, chunk, now)["score"]


def _in_range(chunk: dict, since: float | None,
              until: float | None) -> bool:
    """True when the chunk's filing date falls in [since, until).

    Either the last update or the creation counts: a topic created weeks
    ago but appended to yesterday is yesterday's work. Undated chunks
    (pre-date indexes still cached on disk) are kept rather than hidden.
    """
    if since is None and until is None:
        return True
    times = [t for t in (float(chunk.get("updated", 0.0) or 0.0),
                         float(chunk.get("created", 0.0) or 0.0)) if t]
    if not times:
        return True
    lo = since if since is not None else 0.0
    hi = until if until is not None else float("inf")
    return any(lo <= t < hi for t in times)


def scope_mtime(store: ScopeStore) -> float:
    latest = 0.0
    if store.dir.is_dir():
        for p in store.topic_files():
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    # The meta sidecar carries importance/access signals baked into the
    # index at build time: any touch/pin/import must retire the cache,
    # or behavioral boosts freeze until an unrelated topic file changes.
    try:
        latest = max(latest, store.meta_path.stat().st_mtime)
    except OSError:
        pass
    return latest


def load_or_build(store: ScopeStore, force: bool = False) -> RagIndex:
    """Load the persisted FTS5 index, rebuilding when topics changed."""
    index_path = store.dir / paths.RAG_INDEX
    signature = scope_signature(store)
    if not force and index_path.is_file():
        idx = _read_db(index_path)
        if idx is not None and idx.source_signature == signature:
            return idx
    idx = RagIndex()
    idx.build(store.read_all(), source_mtime=scope_mtime(store) or utc_now())
    idx.source_signature = signature
    try:
        with scope_lock(store.dir):
            # Another writer may have changed source files during this build.
            if scope_signature(store) != signature:
                return load_or_build(store, force=True)
            if _write_db(store, idx, signature):
                idx._db_path = str(index_path)
            try:  # retire the pre-SQLite JSON cache on first rebuild
                (store.dir / paths.LEGACY_RAG_INDEX).unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass
    return idx


def load_cached(store: ScopeStore) -> RagIndex:
    """Read-only index for hot paths: fresh DB if present, else in-memory.

    Never writes the index file, so SessionStart/UserPromptSubmit stay
    read-only with respect to topics, metadata, and the persisted index.
    """
    index_path = store.dir / paths.RAG_INDEX
    if index_path.is_file():
        idx = _read_db(index_path)
        if idx is not None and idx.source_signature == scope_signature(store):
            return idx
    idx = RagIndex()
    idx.build(store.read_all(), source_mtime=scope_mtime(store) or utc_now())
    idx.source_signature = scope_signature(store)
    return idx


def scope_signature(store: ScopeStore) -> str:
    """Detect deletion and same-timestamp changes as well as newer files."""
    state = []
    for path in store.topic_files() + [store.meta_path]:
        try:
            stat = path.stat()
            state.append((path.name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return hashlib.sha256(json.dumps(state).encode('utf-8')).hexdigest()


def index_health(store: ScopeStore) -> dict:
    """Cached-index status without rebuilding: indexed/chunks/stale.

    Never raises: any unreadable or misshapen index reports as stale so
    callers can surface it (and the next recall rebuilds from disk).
    """
    index_path = store.dir / paths.RAG_INDEX
    if not index_path.is_file():
        legacy = (store.dir / paths.LEGACY_RAG_INDEX).is_file()
        return {"indexed": False, "chunks": 0, "stale": legacy}
    idx = _read_db(index_path)
    if idx is None:
        return {"indexed": False, "chunks": 0, "stale": True}
    return {"indexed": True, "chunks": len(idx.chunks),
            "stale": idx.source_signature != scope_signature(store)}


def search_all(stores: list[ScopeStore], query: str, top_k: int = 5,
               min_score: float = 0.0,
               include_archive: bool = False,
               since: float | None = None,
               until: float | None = None,
               now: float | None = None,
               read_only: bool = False) -> list[ScoredChunk]:
    """Search every scope index and merge rankings (round-robin safe merge).

    since/until restrict hits to memories filed in [since, until), for
    time questions ("what did we complete yesterday"). When the query has
    no lexical terms left after date parsing — or its terms match nothing
    in range — in-range memories list back ranked by recency/importance
    instead of returning nothing.
    """
    ranged = since is not None or until is not None
    now = now if now is not None else utc_now()
    per: list[ScoredChunk] = []
    for store in stores:
        indexes = [load_cached(store) if read_only else load_or_build(store)]
        if include_archive:
            archived_recs = store.read_archive()
            if archived_recs:
                # Archive set is small and opt-in: build just it fresh and
                # keep the cached live index, instead of rebuilding both.
                aidx = RagIndex()
                aidx.build(archived_recs)
                for chunk in aidx.chunks:
                    chunk["archived"] = True
                indexes.append(aidx)
        for idx in indexes:
            if query.strip():
                per.extend(idx.search(query, top_k=top_k, min_score=0.0,
                                      now=now, since=since, until=until))
            elif ranged:
                for chunk in idx.chunks:
                    if not _in_range(chunk, since, until):
                        continue
                    score = _fuse_parts(1.0, chunk, now)["score"]
                    per.append(ScoredChunk(
                        chunk_id=chunk["chunk_id"], scope=chunk["scope"],
                        file=chunk["file"], title=chunk["title"],
                        text=chunk["text"], score=score, lexical=1.0,
                        importance=int(chunk.get("importance", 3)),
                        archived=bool(chunk.get("archived", False))))
    if ranged and not per and query.strip():
        return search_all(stores, "", top_k=top_k, min_score=min_score,
                          include_archive=include_archive,
                          since=since, until=until, now=now,
                          read_only=read_only)
    # Normalize per-scope lexical scales by best hit, then re-sort by fused score.
    best: dict[str, float] = {}
    for hit in per:
        best[hit.scope] = max(best.get(hit.scope, 0.0), hit.lexical or 1e-9)
    rescored = []
    for hit in per:
        norm = hit.lexical / best.get(hit.scope, 1.0)
        rescored.append((hit.score * (0.5 + 0.5 * norm), hit))
    rescored.sort(key=lambda t: t[0], reverse=True)
    out = [h for _, h in rescored if h.score >= min_score]
    return out[:top_k]


def explain_all(stores: list[ScopeStore], query: str,
                top_k: int = 5,
                since: float | None = None,
                until: float | None = None) -> list[dict]:
    """Score breakdown per hit (lexical + boosts), same ordering as search_all."""
    q_terms = tokenize(query)
    if not q_terms:
        return []
    now = utc_now()
    rows: list[dict] = []
    for store in stores:
        idx = load_or_build(store)
        for i, chunk in enumerate(idx.chunks):
            if not _in_range(chunk, since, until):
                continue
            lexical = idx._bm25(q_terms, i)
            if lexical <= 0:
                continue
            parts = _fuse_parts(lexical, chunk, now)
            rows.append({"scope": chunk["scope"], "file": chunk["file"],
                         "title": chunk["title"], **parts})
    best: dict[str, float] = {}
    for row in rows:
        best[row["scope"]] = max(best.get(row["scope"], 0.0),
                                 row["lexical"] or 1e-9)
    for row in rows:
        norm = row["lexical"] / best.get(row["scope"], 1.0)
        row["final"] = row["score"] * (0.5 + 0.5 * norm)
    rows.sort(key=lambda r: r["final"], reverse=True)
    return rows[:top_k]


def format_context(hits: list[ScoredChunk], max_chars: int = 4000,
                 header: str | None = None) -> str:
    if not hits:
        return ""
    parts = [header or "Relevant memory (auto-recalled):"]
    used = 0
    for hit in hits:
        snippet = hit.text.strip().replace("\n", " ")
        if len(snippet) > 600:
            snippet = snippet[:600] + "…"
        tag = " (archived)" if hit.archived else ""
        line = f"- [{hit.scope}/{hit.file}]{tag} {hit.title}: {snippet}"
        if used + len(line) > max_chars:
            if used:
                break
            # The first hit is always emitted: trim its snippet to the
            # budget rather than returning a header with no memories.
            over = used + len(line) - max_chars
            snippet = snippet[:max(0, len(snippet) - over - 1)] + "…"
            line = f"- [{hit.scope}/{hit.file}]{tag} {hit.title}: {snippet}"
            parts.append(line)
            break
        parts.append(line)
        used += len(line)
    return "\n".join(parts) + "\n"

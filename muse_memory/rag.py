"""Built-in RAG retrieval over memory scopes. Standard library only.

Pipeline: chunk Markdown records -> TF-IDF vectors with BM25-style term
saturation -> cosine similarity fused with recency / importance / access
signals. The index persists as JSON next to each scope so hooks stay fast
(incremental rebuild only when topic files changed).

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

    # -- build ------------------------------------------------------------
    def build(self, records: list[MemoryRecord], source_mtime: float = 0.0) -> None:
        self.chunks = []
        for rec in records:
            self.chunks.extend(chunk_record(rec))
        self._tokens = [tokenize(c["text"] + " " + c["title"]) for c in self.chunks]
        self._lengths = [len(t) or 1 for t in self._tokens]
        self._title_tokens = [set(tokenize(c["title"])) for c in self.chunks]
        self._counters = [Counter(t) for t in self._tokens]
        self.doc_freq = {}
        for toks in self._tokens:
            for term in set(toks):
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        self.avg_len = (sum(self._lengths) / len(self._lengths)) if self._lengths else 1.0
        self.built_at = utc_now()
        self.source_mtime = source_mtime

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

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0,
               now: float | None = None,
               since: float | None = None,
               until: float | None = None) -> list[ScoredChunk]:
        q_terms = tokenize(query)
        if not q_terms or not self.chunks:
            return []
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
        idx = cls()
        idx.chunks = data.get("chunks", [])
        idx.doc_freq = data.get("doc_freq", {})
        idx.avg_len = data.get("avg_len", 1.0) or 1.0
        idx.built_at = data.get("built_at", 0.0)
        idx.source_mtime = data.get("source_mtime", 0.0)
        idx.source_signature = data.get('source_signature', '')
        idx._tokens = [tokenize(c.get("text", "") + " " + c.get("title", ""))
                       for c in idx.chunks]
        idx._lengths = [len(t) or 1 for t in idx._tokens]
        idx._title_tokens = [set(tokenize(c.get("title", ""))) for c in idx.chunks]
        idx._counters = [Counter(t) for t in idx._tokens]
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
    """Load a cached scope index, rebuilding when topic files changed."""
    index_path = store.dir / paths.RAG_INDEX
    mtime = scope_mtime(store)
    signature = scope_signature(store)
    if not force and index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            idx = RagIndex.from_dict(data)
            if idx.source_signature == signature:
                return idx
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # corrupt cache: fall through and rebuild from disk
    idx = RagIndex()
    idx.build(store.read_all(), source_mtime=mtime or utc_now())
    idx.source_signature = signature
    try:
        with scope_lock(store.dir):
            # Another writer may have changed source files during this build.
            if scope_signature(store) != signature:
                return load_or_build(store, force=True)
            tmp = index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(idx.to_dict()), encoding="utf-8")
            os.replace(tmp, index_path)
    except OSError:
        pass
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
        return {"indexed": False, "chunks": 0, "stale": False}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(
                data.get("chunks"), list):
            raise ValueError("misshapen index")
        chunks = data["chunks"]
        stale = data.get('source_signature') != scope_signature(store)
    except (OSError, ValueError, TypeError):
        return {"indexed": False, "chunks": 0, "stale": True}
    return {"indexed": True, "chunks": len(chunks), "stale": stale}


def search_all(stores: list[ScopeStore], query: str, top_k: int = 5,
               min_score: float = 0.0,
               include_archive: bool = False,
               since: float | None = None,
               until: float | None = None,
               now: float | None = None) -> list[ScoredChunk]:
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
        indexes = [load_or_build(store)]
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
                          since=since, until=until, now=now)
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

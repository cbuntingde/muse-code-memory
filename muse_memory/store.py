"""Markdown memory store with three scopes and an index file per scope.

Layout per scope directory:
    MEMORY.md            index, one line per topic file: - [title](file.md) - summary
    <topic>.md           one Markdown file per topic (human + agent readable)
    .memory-meta.json    machine sidecar: per-file importance/access/expiry
    .rag-index.json      RAG sidecar (see rag.py)
    .pending.jsonl       capture queue drained at Stop/SessionEnd
    archive/             retired memories (still searchable on demand)

Native Muse memory (MEMORY.md + up to 48 topic files) keeps working untouched:
this store reads those files as-is and only adds dotfile sidecars.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
BULLET_RE = re.compile(r"(?m)^\s*[-*]\s+(.+)$")
SAFE_NAME_RE = re.compile(r"[^a-z0-9]+")

MAX_INDEX_FILES = 48  # Muse injects at most 48 memory files into context

# Grouped MEMORY.md sections, in display order. Records land in the first
# group whose rule matches (see index_group); everything else is a fact/note.
INDEX_GROUP_ORDER = ("Standing rules", "Decisions", "Fixes & Gotchas",
                     "Preferences", "Facts & Notes")


def utc_now() -> float:
    return time.time()


def slugify_topic(topic: str) -> str:
    slug = SAFE_NAME_RE.sub("-", topic.strip().lower()).strip("-")
    return slug[:60] or "memory"


@dataclass
class MemoryRecord:
    id: str
    scope: str
    file: str  # topic filename, e.g. "api-conventions.md"
    title: str
    content: str  # full markdown body (without frontmatter)
    tags: list[str] = field(default_factory=list)
    importance: int = 3  # 1 (trivia) .. 5 (standing rule)
    pinned: bool = False  # pinned topics win index slots, never auto-retire
    created: float = 0.0
    updated: float = 0.0
    accessed: float = 0.0
    access_count: int = 0
    expires: float = 0.0  # 0 = never
    source: str = "manual"
    session_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return meta, text
    for line in m.group(1).splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, text[m.end():]


def title_of(body: str, fallback: str) -> str:
    m = HEADING_RE.search(body)
    if m:
        return m.group(1).strip()[:120]
    for line in body.splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return fallback


def summary_of(body: str, limit: int = 140) -> str:
    bullets = BULLET_RE.findall(body)
    if bullets:
        first = bullets[0].strip()
        return first[:limit]
    text = " ".join(ln.strip() for ln in body.splitlines() if ln.strip())
    return text[:limit]


def iso_date(ts: float) -> str:
    """Format an epoch timestamp as YYYY-MM-DD (UTC), "" when unset."""
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def index_group(rec: MemoryRecord) -> str:
    """Map a record to one INDEX_GROUP_ORDER section."""
    tags = {t.lower() for t in rec.tags}
    if rec.importance >= 5 or "rule" in tags:
        return "Standing rules"
    if "decision" in tags:
        return "Decisions"
    if tags & {"fix", "gotcha"}:
        return "Fixes & Gotchas"
    if "preference" in tags:
        return "Preferences"
    return "Facts & Notes"


def frontmatter_block(text: str) -> str:
    """Return the raw leading `---...---` block of a file, or ""."""
    m = FRONTMATTER_RE.match(text)
    return m.group(0) if m else ""


def render_topic(title: str, body: str, meta: dict | None = None) -> str:
    """Wrap a topic body with documentary frontmatter.

    Emits title/tags/importance/source/created above the body. The body
    itself is untouched (any stale frontmatter on it is replaced), so
    read_record strips the header and content roundtrips byte-identical.
    Machine truth stays in `.memory-meta.json`; this header is for
    humans and future-proofing. No `updated` line: appends would leave
    it stale, and the sidecar already tracks it.
    """
    meta = meta or {}
    _, stripped = split_frontmatter(body.strip())
    lines = ["---\n", f"title: {title.strip()}\n"]
    tags = meta.get("tags", [])
    tag_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
    if tag_str:
        lines.append(f"tags: {tag_str}\n")
    lines.append(f"importance: {meta.get('importance', 3)}\n")
    if meta.get("source"):
        lines.append(f"source: {meta['source']}\n")
    try:
        created_ts = float(meta.get("created", 0.0) or 0.0)
    except (TypeError, ValueError):
        created_ts = 0.0
    created = iso_date(created_ts)
    if created:
        lines.append(f"created: {created}\n")
    lines.append("---\n")
    return "".join(lines) + stripped.strip() + "\n"


def index_row(rec: MemoryRecord) -> str:
    """One professional index line: link + summary + compact metadata."""
    summary = summary_of(rec.content, 110)
    meta = [f"imp:{rec.importance}"]
    updated = iso_date(rec.updated or rec.created)
    if updated:
        meta.append(updated)
    meta.extend(f"#{t}" for t in rec.tags[:3])
    suffix = " · ".join(meta)
    if summary:
        return f"- [{rec.title}]({rec.file}) - {summary} · {suffix}\n"
    return f"- [{rec.title}]({rec.file}) · {suffix}\n"


class ScopeStore:
    """One scope directory on disk."""

    def __init__(self, scope: str, directory: Path):
        self.scope = scope
        self.dir = directory
        self.meta_path = directory / paths.META_FILE
        self._meta: dict[str, dict] = {}
        self._meta_loaded = False

    # -- setup ------------------------------------------------------------
    def ensure(self, index_title: str = "Memory") -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        index = self.dir / paths.MEMORY_INDEX
        if not index.exists():
            scope_line = {"global": "personal", "project-personal": "project",
                          "project-shared": "project"}.get(self.scope, "project")
            index.write_text(
                f"---\ntype: {scope_line}\n---\n\n# {index_title}\n\n",
                encoding="utf-8",
            )

    # -- meta sidecar ------------------------------------------------------
    def _load_meta(self) -> dict[str, dict]:
        if self._meta_loaded:
            return self._meta
        self._meta_loaded = True
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._meta = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._meta = {}
        return self._meta

    def _save_meta(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._meta, indent=1), encoding="utf-8")
        os.replace(tmp, self.meta_path)

    def file_meta(self, filename: str) -> dict:
        return self._load_meta().get(filename, {})

    def set_file_meta(self, filename: str, **fields: object) -> None:
        meta = self._load_meta().setdefault(filename, {})
        meta.update(fields)
        self._save_meta()

    # -- topic files --------------------------------------------------------
    def topic_files(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.iterdir()):
            if not p.is_file() or p.suffix.lower() != ".md":
                continue
            if p.name == paths.MEMORY_INDEX or p.name.startswith("."):
                continue
            out.append(p)
        return out

    def record_id(self, filename: str) -> str:
        raw = f"{self.scope}:{self.dir}:{filename}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def read_record(self, path: Path) -> MemoryRecord:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        _, body = split_frontmatter(text)
        meta = self.file_meta(path.name)
        stat_updated = path.stat().st_mtime if path.exists() else utc_now()
        return MemoryRecord(
            id=self.record_id(path.name),
            scope=self.scope,
            file=path.name,
            title=str(meta.get("title") or title_of(body, path.stem)),
            content=body,
            tags=list(meta.get("tags", [])),
            importance=int(meta.get("importance", 3)),
            pinned=bool(meta.get("pinned", False)),
            created=float(meta.get("created", stat_updated)),
            updated=float(meta.get("updated", stat_updated)),
            accessed=float(meta.get("accessed", 0.0)),
            access_count=int(meta.get("access_count", 0)),
            expires=float(meta.get("expires", 0.0)),
            source=str(meta.get("source", "manual")),
            session_id=str(meta.get("session_id", "")),
        )

    def read_all(self) -> list[MemoryRecord]:
        return [self.read_record(p) for p in self.topic_files()]

    def read_archive(self) -> list[MemoryRecord]:
        """Records retired to archive/ (for opt-in archive recall)."""
        archive_dir = self.dir / paths.ARCHIVE_DIR
        if not archive_dir.is_dir():
            return []
        out = []
        for p in sorted(archive_dir.iterdir()):
            if (p.is_file() and p.suffix.lower() == ".md"
                    and not p.name.startswith(".")):
                rec = self.read_record(p)
                rec.file = f"archive/{p.name}"
                out.append(rec)
        return out

    def write_topic(self, topic: str, body: str, meta: dict | None = None) -> MemoryRecord:
        """Create or replace a topic file; returns the stored record."""
        self.ensure()
        filename = slugify_topic(topic) + ".md"
        target = self.dir / filename
        now = utc_now()
        header_meta = dict(meta or {})
        header_meta.setdefault(
            "created", self.file_meta(filename).get("created", now))
        title = title_of(body.strip(), topic)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(render_topic(title, body, header_meta), encoding="utf-8")
        os.replace(tmp, target)
        merged = dict(self.file_meta(filename))
        merged.update({
            "title": title,
            "updated": now,
            "created": merged.get("created", now),
        })
        if meta:
            merged.update(meta)
        self._load_meta()[filename] = merged
        self._save_meta()
        self.refresh_index()
        return self.read_record(target)

    def append_to_topic(self, filename: str, addition: str) -> MemoryRecord | None:
        target = self.dir / filename
        if not target.is_file():
            return None
        try:
            current = target.read_text(encoding="utf-8")
        except OSError:
            return None
        if addition.strip() in current:
            return self.read_record(target)
        target.write_text(current.rstrip() + "\n\n" + addition.strip() + "\n",
                           encoding="utf-8")
        self.set_file_meta(filename, updated=utc_now())
        self.refresh_index()
        return self.read_record(target)

    def _invalidate_index(self) -> None:
        """Drop the cached RAG index so the next recall rebuilds from disk."""
        try:
            (self.dir / paths.RAG_INDEX).unlink(missing_ok=True)
        except OSError:
            pass

    def delete_topic(self, filename: str) -> bool:
        target = self.dir / filename
        try:
            target.unlink()
        except OSError:
            return False
        self._load_meta().pop(filename, None)
        self._save_meta()
        self.refresh_index()
        self._invalidate_index()
        return True

    def archive_topic(self, filename: str) -> bool:
        src = self.dir / filename
        if not src.is_file():
            return False
        dest_dir = self.dir / paths.ARCHIVE_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dest_dir / filename)
        except OSError:
            return False
        self._load_meta().pop(filename, None)
        self._save_meta()
        self.refresh_index()
        self._invalidate_index()
        return True

    def touch_access(self, filename: str) -> None:
        meta = self.file_meta(filename)
        self.set_file_meta(
            filename,
            accessed=utc_now(),
            access_count=int(meta.get("access_count", 0)) + 1,
        )

    def pending_count(self) -> int:
        """Queued capture lines waiting in .pending.jsonl (0 when absent)."""
        pending = self.dir / paths.PENDING_FILE
        if not pending.is_file():
            return 0
        try:
            return sum(1 for line in pending.read_text(
                encoding="utf-8").splitlines() if line.strip())
        except OSError:
            return 0

    def archive_count(self) -> int:
        """Topic files retired to archive/."""
        archive_dir = self.dir / paths.ARCHIVE_DIR
        if not archive_dir.is_dir():
            return 0
        try:
            return sum(1 for p in archive_dir.iterdir()
                       if p.is_file() and p.suffix.lower() == ".md")
        except OSError:
            return 0

    # -- index ---------------------------------------------------------------
    def refresh_index(self) -> None:
        """Rewrite MEMORY.md preserving its heading/frontmatter.

        Professional grouped template: one `##` section per index group,
        one `- [title](file)` line per topic with compact metadata, the
        48-file cap with an overflow note, and a footer with live counts.
        """
        index = self.dir / paths.MEMORY_INDEX
        try:
            text = index.read_text(encoding="utf-8") if index.exists() else ""
        except OSError:
            text = ""
        fm, _ = split_frontmatter(text)
        heading = "# Memory\n"
        m = HEADING_RE.search(text)
        if m:
            heading = m.group(0) + "\n"
        lines = ["---\n"] if fm else []
        for k, v in fm.items():
            lines.append(f"{k}: {v}\n")
        if fm:
            lines.append("---\n")
        lines.append("\n" + heading + "\n")
        records = self.read_all()
        grouped: dict[str, list[MemoryRecord]] = {g: [] for g in INDEX_GROUP_ORDER}
        for rec in records:
            grouped[index_group(rec)].append(rec)
        for recs in grouped.values():
            recs.sort(key=lambda r: (-r.importance, r.title.lower()))
        rank = {group: i for i, group in enumerate(INDEX_GROUP_ORDER)}
        visible = sorted(records, key=lambda r: (
            not r.pinned, rank[index_group(r)], -r.importance, r.title.lower()))
        shown = set(f"{r.scope}:{r.file}" for r in visible[:MAX_INDEX_FILES])
        for group in INDEX_GROUP_ORDER:
            rows = [r for r in grouped[group]
                    if f"{r.scope}:{r.file}" in shown]
            if not rows:
                continue
            lines.append(f"## {group}\n\n")
            for rec in rows:
                lines.append(index_row(rec))
            lines.append("\n")
        if len(records) > MAX_INDEX_FILES:
            lines.append(f"<!-- +{len(records) - MAX_INDEX_FILES} more files "
                         "not indexed (over Muse 48-file context limit) -->\n")
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(f"\n---\n_{self._index_footer(records)}_\n")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = index.with_suffix(".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, index)

    def _index_footer(self, records: list[MemoryRecord]) -> str:
        """Live footer counts: topics, latest update, archived files."""
        total = len(records)
        latest = 0.0
        for rec in records:
            latest = max(latest, rec.updated or rec.created or 0.0)
        archived = self.archive_count()
        noun = "topic" if total == 1 else "topics"
        footer = f"{total} {noun}"
        if latest:
            footer += f" · updated {iso_date(latest)}"
        footer += f" · {archived} archived"
        return footer


class MemoryStore:
    """All scopes for one workspace."""

    def __init__(self, cwd: str | os.PathLike | None = None,
                 scopes: list[str] | None = None):
        self.cwd = paths.canonical_cwd(cwd)
        wanted = scopes or ["global", "project-personal", "project-shared"]
        dirs = paths.all_scope_dirs(self.cwd)
        self.scopes: dict[str, ScopeStore] = {}
        for name in wanted:
            d = dirs.get(name)
            if d is not None:
                self.scopes[name] = ScopeStore(name, d)

    def ensure_all(self) -> None:
        # One-time move of the pre-1.1 tree (<data>/memory/) into the
        # config-based memories dir; no-op afterwards. Runs here so every
        # hook and CLI command sees the migrated layout.
        paths.migrate_legacy_base()
        titles = {"global": "Global Memory",
                  "project-personal": f"Project Memory ({self.cwd.name})",
                  "project-shared": f"Shared Project Memory ({self.cwd.name})"}
        for name, store in self.scopes.items():
            store.ensure(titles.get(name, "Memory"))

    def all_records(self) -> list[MemoryRecord]:
        out: list[MemoryRecord] = []
        for store in self.scopes.values():
            out.extend(store.read_all())
        return out

    def get_scope(self, scope: str) -> ScopeStore | None:
        return self.scopes.get(scope)

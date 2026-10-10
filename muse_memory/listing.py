"""Shared memory listing: filter rows, render table/markdown.

One implementation serves the `list` CLI and the `memory_list` MCP tool so
both surfaces stay identical. Rows are sorted by (scope, file).
"""

from __future__ import annotations

from .store import MemoryStore, iso_date


def list_rows(store: MemoryStore, scope_filter: str | None = None,
               tag_filter: str | None = None,
               min_importance: int = 0) -> list[dict]:
    wanted_tag = (tag_filter or "").lower() or None
    rows = []
    for scope in store.scopes.values():
        if scope_filter and scope.scope != scope_filter:
            continue
        for rec in scope.read_all():
            if wanted_tag and wanted_tag not in {t.lower() for t in rec.tags}:
                continue
            if rec.importance < min_importance:
                continue
            rows.append({"scope": rec.scope, "file": rec.file, "title": rec.title,
                         "importance": rec.importance, "tags": rec.tags,
                         "access_count": rec.access_count, "pinned": rec.pinned,
                         "updated": iso_date(rec.updated or rec.created)})
    rows.sort(key=lambda r: (r["scope"], r["file"]))
    return rows


def empty_message(filtered: bool) -> str:
    if filtered:
        return "(no memories match the current filters)"
    return "(no memories stored yet)"


def format_table(rows: list[dict]) -> str:
    headers = ("Scope", "File", "Imp", "Updated", "Title")
    widths = [len(h) for h in headers]
    cells = []
    for row in rows:
        title = row["title"].replace("\n", " ")
        if len(title) > 60:
            title = title[:57] + "..."
        cell = (row["scope"], row["file"], str(row["importance"]),
                row["updated"], title)
        cells.append(cell)
        for i, value in enumerate(cell):
            widths[i] = max(widths[i], len(value))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
             "  ".join("-" * widths[i] for i in range(len(headers)))]
    for cell in cells:
        lines.append("  ".join(value.ljust(widths[i])
                               for i, value in enumerate(cell)))
    noun = "topic" if len(rows) == 1 else "topics"
    lines.append(f"\n{len(rows)} {noun}")
    return "\n".join(lines)


def format_markdown(rows: list[dict]) -> str:
    lines = ["| Scope | File | Imp | Updated | Title |",
             "|---|---|---|---|---|"]
    for row in rows:
        title = row["title"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['scope']} | {row['file']} | {row['importance']} "
                     f"| {row['updated']} | {title} |")
    return "\n".join(lines)

"""`python3 -m muse_memory` command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse_memory import config as config_mod
from muse_memory import consolidate as cons
from muse_memory import extractor, lifecycle, listing, paths, rag
from muse_memory.store import MemoryStore


def _store(args) -> MemoryStore:
    return MemoryStore(args.cwd, scopes=args.scopes)


def cmd_init(args) -> int:
    store = _store(args)
    store.ensure_all()
    for name, scope in store.scopes.items():
        scope.refresh_index()
        rag.load_or_build(scope, force=True)
    print(json.dumps({"scopes": {n: str(s.dir) for n, s in store.scopes.items()}},
                     indent=2))
    return 0


def cmd_recall(args) -> int:
    from muse_memory import dates
    from muse_memory.service import MemoryService

    cfg = config_mod.load(args.cwd)
    store = _store(args)
    svc = MemoryService(args.cwd, scopes=args.scopes)
    top_k = args.top_k or int(cfg["recall_top_k"])
    try:
        cleaned, since, until = svc.parse_range(
            args.query, getattr(args, "since", None),
            getattr(args, "until", None))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    if getattr(args, "explain", False):
        rows = rag.explain_all(list(store.scopes.values()), cleaned,
                               top_k=top_k, since=since, until=until)
        if args.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print("(no relevant memory)")
        else:
            for row in rows:
                print(f"{row['scope']}/{row['file']} "
                      f"score={row['score']:.3f} final={row['final']:.3f} "
                      f"lexical={row['lexical']:.3f} "
                      f"imp x{row['importance_boost']:.2f} "
                      f"recency x{row['recency_boost']:.2f} "
                      f"use x{row['use_boost']:.2f} :: {row['title']}")
        return 0
    hits = rag.search_all(list(store.scopes.values()), cleaned,
                          top_k=top_k, min_score=float(cfg["min_score"]),
                          include_archive=bool(
                              getattr(args, "include_archive", False)),
                          since=since, until=until)

    if args.json:
        print(json.dumps([h.__dict__ for h in hits], indent=2))
    else:
        header = (f"Relevant memory ({dates.label(since, until)}):"
                  if since is not None or until is not None else None)
        text = rag.format_context(hits, int(cfg["recall_max_chars"]),
                                  header=header)
        print(text if text else "(no relevant memory)")
    for hit in hits[:3]:
        if hit.archived:
            continue
        scope = store.get_scope(hit.scope)
        if scope:
            scope.touch_access(hit.file)
    return 0


def cmd_save(args) -> int:
    store = _store(args)
    text = args.text
    if args.stdin:
        text += "\n" + sys.stdin.read()
    raw_text = text
    text, secret_kinds = extractor.redact_secrets(text)
    scope_name = args.scope or config_mod.load(args.cwd)["default_scope"]
    if args.auto:
        cands = extractor.extract_candidates(text, source="manual",
                                             default_scope=scope_name)
        if not cands and text.strip():
            cands = [{"text": text.strip(), "kind": "note",
                      "topic": extractor.suggest_topic(text, "note"),
                      "importance": args.importance, "tags": args.tag or [],
                      "scope": scope_name, "source": "manual", "session_id": ""}]
        report = cons.consolidate_session(cands, store)
        report["redacted"] = secret_kinds
        print(json.dumps(report, indent=2))
        return 1 if report.get('errors') else 0
    from muse_memory.service import MemoryService
    try:
        result = MemoryService(args.cwd, scopes=args.scopes).save(
            raw_text, topic=args.topic, scope=scope_name,
            importance=args.importance, tags=args.tag or [],
            supersedes=getattr(args, "supersedes", None))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    if result.get("action") == "blocked-secret":
        print(json.dumps({"error": "refused for project-shared scope: "
                          f"credential-like content ({', '.join(result.get('blocked', []))})"}))
        return 1
    out = {"file": f"{result['scope']}/{result['file']}",
           "action": result.get("action", ""),
           "redacted": result.get("redacted", [])}
    if result.get("superseded"):
        out["superseded"] = result["superseded"]
    print(json.dumps(out, indent=2))
    return 0


def cmd_search(args) -> int:
    return cmd_recall(args)


def cmd_list(args) -> int:
    store = _store(args)
    scope_filter = getattr(args, "scope", None)
    tag_filter = getattr(args, "tag", None)
    min_importance = int(getattr(args, "min_importance", 0) or 0)
    fmt = getattr(args, "format", "table")
    rows = listing.list_rows(store, scope_filter, tag_filter, min_importance)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(listing.empty_message(
            bool(scope_filter or tag_filter or min_importance)))
        return 0
    if fmt == "markdown":
        print(listing.format_markdown(rows))
    else:
        print(listing.format_table(rows))
    return 0


def cmd_consolidate(args) -> int:
    cfg = config_mod.load(args.cwd)
    store = _store(args)
    if args.text:
        cands = extractor.extract_candidates(args.text, source="manual",
                                             max_candidates=20)
    else:
        report = lifecycle.file_pending(
            store, int(cfg['max_pending']), float(cfg['dedupe_threshold']),
            approve_all=bool(getattr(args, "approve_all", False)),
            auto_file_importance=int(cfg['capture_auto_file_importance']),
            proposal_ttl_days=int(cfg['proposal_ttl_days']))
        print(json.dumps(report, indent=2))
        return 0
    report = cons.consolidate_session(cands, store, float(cfg['dedupe_threshold']))
    print(json.dumps(report, indent=2))
    return 1 if report.get('errors') else 0


def cmd_cleanup(args) -> int:
    cfg = config_mod.load(args.cwd)
    store = _store(args)
    report = cons.cleanup(store, cfg)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return 0
    for scope_name, info in report.items():
        actions = ", ".join(
            f"{key.replace('_', ' ')} {value}"
            for key, value in sorted(info.items())
            if isinstance(value, int) and not isinstance(value, bool)
            and value and key != "scope")
        capped = " (comparisons capped)" if info.get("comparisons_capped") else ""
        print(f"[{scope_name}] {actions or 'no changes'}{capped}")
    return 0


def _scope_health(scope) -> dict:
    records = scope.read_all()
    histogram: dict[str, int] = {}
    for rec in records:
        key = str(rec.importance)
        histogram[key] = histogram.get(key, 0) + 1
    return {
        "dir": str(scope.dir),
        "files": len(records),
        "bullets": sum(r.content.count("\n- ") + r.content.count("\n* ")
                       for r in records),
        "chars": sum(len(r.content) for r in records),
        "importance": histogram,
        "pending": scope.pending_count(),
        "archived": scope.archive_count(),
        "index": rag.index_health(scope),
    }


def cmd_stats(args) -> int:
    store = _store(args)
    stats: dict = {"cwd": str(store.cwd), "scopes": {}}
    for name, scope in store.scopes.items():
        stats["scopes"][name] = _scope_health(scope)
    print(json.dumps(stats, indent=2))
    return 0


def _pending_items(scope, limit: int) -> list[dict]:
    pending = scope.dir / paths.PENDING_FILE
    if not pending.is_file():
        return []
    try:
        lines = pending.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items = []
    for line in lines:
        if len(items) >= limit:
            break
        try:
            cand = json.loads(line)
        except ValueError:
            continue
        if isinstance(cand, dict) and cand.get("text"):
            items.append({"text": str(cand["text"])[:120],
                          "topic": str(cand.get("topic", "")),
                          "importance": int(cand.get("importance", 3))})
    return items


def cmd_pending(args) -> int:
    store = _store(args)
    limit = int(getattr(args, "limit", 5) or 5)
    report: dict = {"scopes": {}}
    for name, scope in store.scopes.items():
        report["scopes"][name] = {
            "queued": scope.pending_count(),
            "items": _pending_items(scope, limit),
        }
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        for name, info in report["scopes"].items():
            print(f"[{name}] {info['queued']} queued")
            for item in info["items"]:
                print(f"  - ({item['topic']}) {item['text']}")
    return 0


def cmd_doctor(args) -> int:
    cfg = config_mod.load(args.cwd)
    store = _store(args)
    max_chars = int(cfg.get("max_chars_per_file", 12000))
    report: dict = {"cwd": str(store.cwd), "scopes": {}, "diagnostics": []}
    for name, scope in store.scopes.items():
        records = scope.read_all()
        oversized = sorted(rec.file for rec in records
                           if len(rec.content) > max_chars)
        report["scopes"][name] = {
            "files": len(records),
            "pending": scope.pending_count(),
            "archived": scope.archive_count(),
            "oversized": oversized,
            "index": rag.index_health(scope),
        }
    try:
        log = paths.diagnostics_log()
        if log.is_file():
            report["diagnostics"] = log.read_text(
                encoding="utf-8").splitlines()[-5:]
    except OSError:
        pass
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        for name, info in report["scopes"].items():
            idx = info["index"]
            state = ("stale" if idx["stale"]
                     else "indexed" if idx["indexed"] else "not built")
            print(f"[{name}] {info['files']} topics, "
                  f"{info['pending']} pending, {info['archived']} archived, "
                  f"index {state} ({idx['chunks']} chunks)")
            for filename in info["oversized"]:
                print(f"  ! oversized: {filename} (over {max_chars} chars)")
        if report["diagnostics"]:
            print("recent diagnostics:")
            for line in report["diagnostics"]:
                print(f"  {line}")
        else:
            print("diagnostics: clean (no hook errors logged)")
    return 0


def _resolve_topic(store: MemoryStore, value: str,
                     default_scope: str | None) -> tuple:
    """Split "scope/file" or "file" into (ScopeStore, filename, error).

    Returns (scope, filename, "") on success, (None, "", message) when the
    reference is malformed, the scope is unknown, or the file is missing.
    """
    scope_name = default_scope or "project-personal"
    filename = value
    if "/" in value:
        maybe_scope, maybe_file = value.split("/", 1)
        if maybe_scope in ("global", "project-personal", "project-shared"):
            scope_name, filename = maybe_scope, maybe_file
    filename = filename.strip().lstrip("/").replace("\\", "/").split("/")[-1]
    if (not filename.endswith(".md") or filename in ("MEMORY.md", ".md")
            or filename.startswith(".")):
        return None, "", f"not a topic file: {value!r}"
    scope = store.get_scope(scope_name)
    if scope is None:
        return None, "", f"unknown scope: {scope_name!r}"
    if not (scope.dir / filename).is_file():
        return None, "", f"not found: {scope_name}/{filename}"
    return scope, filename, ""


def cmd_forget(args) -> int:
    scope, filename, error = _resolve_topic(
        _store(args), args.file, getattr(args, "scope", None))
    if error:
        print(json.dumps({"error": error}))
        return 1
    assert scope is not None
    if getattr(args, "purge", False):
        if not scope.delete_topic(filename):
            print(json.dumps({"error": "topic could not be deleted"}))
            return 1
        print(json.dumps({"action": "deleted", "scope": scope.scope,
                          "file": filename}))
        return 0
    if not scope.trash_topic(filename):
        print(json.dumps({"error": "topic could not be trashed"}))
        return 1
    print(json.dumps({"action": "trashed", "scope": scope.scope,
                      "file": filename,
                      "hint": "restore with: muse-memory restore "
                              f"{scope.scope}/{filename}"}))
    return 0


def cmd_restore(args) -> int:
    store = _store(args)
    scope_name = getattr(args, "scope", None) or "project-personal"
    filename = args.file.strip().lstrip("/").replace("\\", "/").split("/")[-1]
    if "/" in args.file:
        maybe_scope, _maybe_file = args.file.split("/", 1)
        if maybe_scope in ("global", "project-personal", "project-shared"):
            scope_name = maybe_scope
    scope = store.get_scope(scope_name)
    if scope is None or not scope.restore_topic(filename):
        print(json.dumps({"error": f"not in trash: {scope_name}/{filename}"}))
        return 1
    print(json.dumps({"action": "restored", "scope": scope.scope,
                      "file": filename}))
    return 0


def cmd_approve(args) -> int:
    from muse_memory.service import MemoryService
    report = MemoryService(args.cwd, scopes=args.scopes).approve_pending()
    print(json.dumps(report, indent=2))
    return 0


def cmd_eval(args) -> int:
    from muse_memory import evalset
    report = evalset.run_eval()
    print(json.dumps(report, indent=2))
    ok = (report["recall_top1"] >= 0.99 and report["recall_at3"] >= 0.99
          and report["capture_precision"] >= 0.99
          and report["capture_recall"] >= 0.99)
    return 0 if ok else 1


def cmd_archive(args) -> int:
    scope, filename, error = _resolve_topic(
        _store(args), args.file, getattr(args, "scope", None))
    if error:
        print(json.dumps({"error": error}))
        return 1
    assert scope is not None
    if not scope.archive_topic(filename):
        print(json.dumps({"error": "topic could not be archived"}))
        return 1
    print(json.dumps({"action": "archived", "scope": scope.scope,
                      "file": filename}))
    return 0


def cmd_pin(args) -> int:
    scope, filename, error = _resolve_topic(
        _store(args), args.file, getattr(args, "scope", None))
    if error:
        print(json.dumps({"error": error}))
        return 1
    assert scope is not None
    scope.set_file_meta(filename, pinned=True)
    scope.refresh_index()
    print(json.dumps({"action": "pinned", "scope": scope.scope,
                      "file": filename}))
    return 0


def cmd_unpin(args) -> int:
    scope, filename, error = _resolve_topic(
        _store(args), args.file, getattr(args, "scope", None))
    if error:
        print(json.dumps({"error": error}))
        return 1
    assert scope is not None
    scope.set_file_meta(filename, pinned=False)
    scope.refresh_index()
    print(json.dumps({"action": "unpinned", "scope": scope.scope,
                      "file": filename}))
    return 0


EXPORT_SCOPE_MARK = "<<<< MEMORY-SCOPE: {} >>>>"
EXPORT_FILE_MARK = "<<<< MEMORY-FILE: {} >>>>"


def cmd_export(args) -> int:
    store = _store(args)
    only = getattr(args, "scope", None)
    lines = ["# Memory export", ""]
    files = 0
    written: list[str] = []
    for name, scope in store.scopes.items():
        if only and name != only:
            continue
        written.append(name)
        lines.append(EXPORT_SCOPE_MARK.format(name))
        for path in scope.topic_files():
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            lines.append(EXPORT_FILE_MARK.format(path.name))
            lines.append(raw.rstrip("\n"))
            files += 1
        lines.append("")
    bundle = "\n".join(lines)
    output = getattr(args, "output", None)
    if output:
        Path(output).write_text(bundle + "\n", encoding="utf-8")
        print(json.dumps({"scopes": written, "files": files,
                          "output": output}))
    else:
        print(bundle)
    return 0


def cmd_import(args) -> int:
    from muse_memory.store import split_frontmatter, title_of, utc_now

    store = _store(args)
    override = getattr(args, "scope", None)
    force = bool(getattr(args, "force", False))
    try:
        text = Path(args.bundle).read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"error": f"unreadable bundle: {exc}"}))
        return 1
    imported: list[str] = []
    skipped: list[str] = []
    redacted: list[str] = []
    scope_name = override or config_mod.load(args.cwd)["default_scope"]
    filename = ""
    buf: list[str] = []

    def flush() -> None:
        if not filename or not buf:
            return
        scope = store.get_scope(scope_name)
        if scope is None:
            skipped.append(f"{scope_name}/{filename} (unknown scope)")
            return
        clean = filename.strip().lstrip("/").replace("\\", "/").split("/")[-1]
        if (not clean.endswith(".md") or clean in ("MEMORY.md", ".md")
                or clean.startswith(".")):
            skipped.append(f"{scope_name}/{filename} (bad name)")
            return
        target = scope.dir / clean
        if target.exists() and not force:
            skipped.append(f"{scope_name}/{clean} (exists, use --force)")
            return
        content = "\n".join(buf)
        # Filing path: scrub like every other capture/filing route so a
        # bundle can never smuggle secrets into topic files (redaction is
        # identity for clean text, so honest roundtrips are unaffected).
        content, secret_kinds = extractor.redact_secrets(content)
        if secret_kinds:
            redacted.append(f"{scope_name}/{clean}: {', '.join(secret_kinds)}")
        scope.dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        now = utc_now()
        meta = dict(scope.file_meta(clean))
        meta.update({"title": title_of(content, clean[:-3]),
                     "updated": now, "created": meta.get("created", now)})
        # Restore filing metadata from the exported frontmatter so the
        # roundtrip preserves importance ranking and tag grouping.
        front, _ = split_frontmatter(content)
        if front.get("tags"):
            tags = [t.strip() for t in front["tags"].split(",") if t.strip()]
            if tags:
                meta["tags"] = tags
        try:
            importance = int(front.get("importance", 0))
        except (TypeError, ValueError):
            importance = 0
        if 1 <= importance <= 5:
            meta["importance"] = importance
        if front.get("source"):
            meta["source"] = front["source"]
        scope._load_meta()[clean] = meta
        scope._dirty_updates.add(clean)
        scope._save_meta()
        imported.append(f"{scope_name}/{clean}")

    for line in text.splitlines():
        if line.startswith("<<<< MEMORY-SCOPE: ") and line.endswith(" >>>>"):
            flush()
            filename, buf = "", []
            if not override:
                scope_name = line[len("<<<< MEMORY-SCOPE: "):-len(" >>>>")]
            continue
        if line.startswith("<<<< MEMORY-FILE: ") and line.endswith(" >>>>"):
            flush()
            filename = line[len("<<<< MEMORY-FILE: "):-len(" >>>>")]
            buf = []
            continue
        if filename:
            buf.append(line)
    flush()
    for scope in store.scopes.values():
        scope.refresh_index()
    print(json.dumps({"imported": imported, "skipped": skipped,
                      "redacted": redacted}))
    return 0


def cmd_config(args) -> int:
    user_file = paths.user_config_file()
    project_file = paths.project_config_file(args.cwd)
    print(json.dumps({
        "user_config": str(user_file),
        "user_config_exists": user_file.is_file(),
        "project_config": str(project_file) if project_file else None,
        "project_config_exists": bool(project_file and project_file.is_file()),
        "effective": config_mod.load(args.cwd),
    }, indent=2))
    return 0


def cmd_reindex(args) -> int:
    store = _store(args)
    for scope in store.scopes.values():
        idx = rag.load_or_build(scope, force=True)
        print(f"{scope.scope}: {len(idx.chunks)} chunks indexed")
    return 0


def cmd_session_end(args) -> int:
    print(json.dumps(lifecycle.session_end(args.cwd, args.session_id), indent=2))
    return 0


def cmd_paths(args) -> int:
    dirs = paths.all_scope_dirs(args.cwd)
    print(json.dumps({k: str(v) if v else None for k, v in dirs.items()}, indent=2))
    print(f"slug: {paths.slug_for(args.cwd)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="muse-memory",
                                 description="Automatic memory + RAG for Muse Code")
    ap.add_argument("--cwd", default=None, help="workspace path (default: cwd)")
    ap.add_argument("--scopes", nargs="*", default=None,
                    help="limit scopes (global project-personal project-shared)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create scope dirs, indexes").set_defaults(fn=cmd_init)

    p = sub.add_parser("recall", help="RAG recall for a query")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--include-archive", action="store_true",
                   help="also search archived topics")
    p.add_argument("--since", default=None,
                   help="only memories filed on/after YYYY-MM-DD")
    p.add_argument("--until", default=None,
                   help="only memories filed before the day after YYYY-MM-DD")
    p.add_argument("--explain", action="store_true",
                   help="show per-hit score breakdown")
    p.set_defaults(fn=cmd_recall)

    p = sub.add_parser("save", help="save a memory")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--topic")
    p.add_argument("--scope", choices=["global", "project-personal", "project-shared"])
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--importance", type=int, default=3)
    p.add_argument("--auto", action="store_true",
                   help="extract + file candidates automatically")
    p.add_argument("--supersedes", default=None,
                   help="topic file this memory replaces (archived)")
    p.set_defaults(fn=cmd_save)

    p = sub.add_parser("search", help="alias of recall")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--include-archive", action="store_true")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--explain", action="store_true")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("list", help="list stored memories")
    p.add_argument("--json", action="store_true")
    p.add_argument("--scope",
                   choices=["global", "project-personal", "project-shared"],
                   default=None)
    p.add_argument("--tag", default=None, help="only show topics with this tag")
    p.add_argument("--min-importance", type=int, default=0,
                   help="only show topics at or above this importance (1-5)")
    p.add_argument("--format", choices=["table", "markdown"], default="table")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("consolidate", help="file pending candidates / text")
    p.add_argument("--text", default="")
    p.add_argument("--approve-all", action="store_true",
                   help="file every queued proposal, not just high-confidence ones")
    p.set_defaults(fn=cmd_consolidate)

    sub.add_parser("approve", help="file all queued capture proposals").set_defaults(fn=cmd_approve)
    sub.add_parser("eval", help="run the golden recall/capture evaluation").set_defaults(fn=cmd_eval)

    p = sub.add_parser("cleanup", help="dedupe + merge + expire + trim + caps")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_cleanup)

    sub.add_parser("stats", help="scope statistics").set_defaults(fn=cmd_stats)
    sub.add_parser("reindex", help="rebuild RAG indexes").set_defaults(fn=cmd_reindex)

    p = sub.add_parser("session-end", help="run end-of-session job")
    p.add_argument("--session-id", default="")
    p.set_defaults(fn=cmd_session_end)

    sub.add_parser("paths", help="show resolved memory paths").set_defaults(fn=cmd_paths)

    p = sub.add_parser("pending", help="inspect queued captures (no drain)")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_pending)

    p = sub.add_parser("doctor", help="memory health check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    sub.add_parser("config", help="show effective config").set_defaults(fn=cmd_config)

    for name, fn, help_text in (
            ("forget", cmd_forget, "trash a topic file (restore to undo)"),
            ("archive", cmd_archive, "retire a topic file to archive/"),
            ("pin", cmd_pin, "pin a topic (index priority, never retired)"),
            ("unpin", cmd_unpin, "remove a topic pin")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("file", help="topic file, optionally scope/file")
        p.add_argument("--scope",
                       choices=["global", "project-personal", "project-shared"],
                       default=None)
        if name == "forget":
            p.add_argument("--purge", action="store_true",
                           help="delete permanently instead of trashing")
        p.set_defaults(fn=fn)

    p = sub.add_parser("restore", help="restore a trashed topic file")
    p.add_argument("file", help="topic file, optionally scope/file")
    p.add_argument("--scope",
                   choices=["global", "project-personal", "project-shared"],
                   default=None)
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("export", help="bundle topics to one portable file")
    p.add_argument("--scope",
                   choices=["global", "project-personal", "project-shared"],
                   default=None)
    p.add_argument("--output", default=None,
                   help="output file (default: stdout)")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("import", help="restore topics from an export bundle")
    p.add_argument("bundle", help="bundle file created by export")
    p.add_argument("--scope",
                   choices=["global", "project-personal", "project-shared"],
                   default=None, help="import everything into this scope")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing topic files")
    p.set_defaults(fn=cmd_import)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scopes is not None:
        args.scopes = [s for s in args.scopes
                       if s in ("global", "project-personal", "project-shared")] or None
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

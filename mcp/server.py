#!/usr/bin/env python3
"""MCP stdio server exposing memory tools to the agent. Standard library only.

Protocol: newline-delimited JSON-RPC 2.0 over stdio (MCP stdio transport).
Methods: initialize, tools/list, tools/call, ping, plus notifications/* ignore.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVER_INFO = {"name": "muse-memory", "version": "1.0.1"}

TOOLS = [
    {
        "name": "memory_recall",
        "description": ("Recall relevant memories for a query using the built-in "
                        "RAG index. Searches global, project-personal, and "
                        "project-shared scopes. Use before answering questions "
                        "about past decisions, conventions, or fixes."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to recall. Date phrases "
                                         "(yesterday, last Monday, last month) "
                                         "filter by filing date automatically"},
                "top_k": {"type": "integer", "default": 5, "minimum": 1,
                          "maximum": 20},
                "include_archive": {"type": "boolean", "default": False,
                                    "description": "Also search archived "
                                                   "topics"},
                "since": {"type": "string",
                          "description": "Only memories filed on/after this "
                                         "date (YYYY-MM-DD)"},
                "until": {"type": "string",
                          "description": "Only memories filed before the day "
                                         "after this date (YYYY-MM-DD)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_save",
        "description": ("Save a durable memory: a decision, preference, standing rule, "
                        "fix, or fact worth remembering across sessions. Ephemeral "
                        "task details should NOT be saved."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "Memory content, one clear statement"},
                "topic": {"type": "string",
                          "description": "Topic slug (auto-derived if omitted)"},
                "scope": {"type": "string", "enum": ["global", "project-personal",
                                                     "project-shared"],
                          "default": "project-personal"},
                "importance": {"type": "integer", "minimum": 1, "maximum": 5,
                               "default": 3},
                "tags": {"type": "array", "items": {"type": "string"},
                         "default": []},
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory_stats",
        "description": "Show memory scope statistics (topics, chunks, sizes).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_list",
        "description": ("List stored memories as a Markdown table (scope, file, "
                        "importance, updated, title). Use to enumerate topics "
                        "before reading MEMORY.md files or recalling detail."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string",
                          "enum": ["global", "project-personal",
                                   "project-shared"],
                          "description": "Only list this scope (default: all)"},
                "tag": {"type": "string",
                        "description": "Only topics with this tag"},
                "min_importance": {"type": "integer", "minimum": 1,
                                   "maximum": 5, "default": 1,
                                   "description": "Only topics at or above "
                                                  "this importance"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 48,
                          "default": 20,
                          "description": "Max rows (default 20)"},
            },
        },
    },
]


def _lazy():
    from muse_memory import config as config_mod
    from muse_memory import consolidate as cons
    from muse_memory import extractor, rag
    from muse_memory.store import MemoryStore
    return config_mod, cons, extractor, rag, MemoryStore


def tool_recall(args: dict) -> str:
    config_mod, _c, _e, rag, MemoryStore = _lazy()
    from muse_memory import dates
    cfg = config_mod.load()
    store = MemoryStore()
    query = str(args.get("query", ""))
    cleaned, auto = dates.split_query(query)
    since, until = auto if auto else (None, None)
    for key, is_start in (("since", True), ("until", False)):
        if args.get(key):
            parsed = dates.parse_day(str(args[key]))
            if parsed is None:
                return f"error: {key} must be YYYY-MM-DD, got {args[key]!r}"
            if is_start:
                since = parsed[0]
            else:
                until = parsed[1]
    if since is not None and until is not None and since > until:
        return (f"error: empty date range ({dates.label(since, until)}): "
                f"since is after until")
    hits = rag.search_all(
        list(store.scopes.values()), cleaned,
        top_k=int(args.get("top_k", 0) or cfg["recall_top_k"]),
        min_score=float(cfg["min_score"]),
        include_archive=bool(args.get("include_archive", False)),
        since=since, until=until)
    if not hits:
        return "(no relevant memory)"
    for hit in hits[:3]:
        if hit.archived:
            continue
        scope = store.get_scope(hit.scope)
        if scope is not None:
            try:
                scope.touch_access(hit.file)
            except OSError:
                pass
    header = (f"Relevant memory ({dates.label(since, until)}):"
              if since is not None or until is not None else None)
    return rag.format_context(hits, int(cfg["recall_max_chars"]),
                              header=header)


def tool_save(args: dict) -> str:
    config_mod, cons, extractor, _rag, MemoryStore = _lazy()
    if not isinstance(args.get('text'), str):
        return 'error: text must be a string'
    importance = args.get('importance', 3)
    if type(importance) is not int or not 1 <= importance <= 5:
        return 'error: importance must be an integer from 1 to 5'
    tags = args.get('tags', [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return 'error: tags must be an array of strings'
    text = args['text'].strip()
    if not text:
        return "error: empty text"
    text, secret_kinds = extractor.redact_secrets(text)
    # An explicit scope always wins; otherwise honor the user's configured
    # default (same rule as the save CLI), instead of a hardcoded scope.
    cfg = config_mod.load()
    scope_name = str(args.get("scope")
                     or cfg.get("default_scope", "project-personal"))
    if scope_name not in ("global", "project-personal", "project-shared"):
        return f"error: unknown scope {scope_name!r}"
    cand = {
        "text": text,
        "kind": "note",
        "topic": str(args.get("topic") or extractor.suggest_topic(text, "note")),
        "importance": int(args.get("importance", 3)),
        "tags": list(args.get("tags", [])),
        "scope": scope_name,
        "source": "manual",
        "session_id": "",
    }
    result = cons.file_candidate(cand, MemoryStore())
    headline = (f"muse-memory saved: {result['scope']}/{result['file']} "
                f"({result['action']})")
    if secret_kinds:
        headline += f" [secrets redacted: {', '.join(secret_kinds)}]"
    return headline + "\n" + json.dumps(result)


def tool_stats(_args: dict) -> str:
    _cfg, _c, _e, rag, MemoryStore = _lazy()
    store = MemoryStore()
    stats: dict = {}
    for name, scope in store.scopes.items():
        records = scope.read_all()
        histogram: dict[str, int] = {}
        for rec in records:
            histogram[str(rec.importance)] = histogram.get(
                str(rec.importance), 0) + 1
        stats[name] = {"dir": str(scope.dir), "files": len(records),
                       "chars": sum(len(r.content) for r in records),
                       "importance": histogram,
                       "pending": scope.pending_count(),
                       "archived": scope.archive_count(),
                       "index": rag.index_health(scope)}
    return json.dumps(stats, indent=2)


def tool_list(args: dict) -> str:
    from muse_memory import listing
    from muse_memory.store import MemoryStore

    scope = args.get("scope")
    if scope not in (None, "global", "project-personal", "project-shared"):
        return f"error: unknown scope {scope!r}"
    tag = args.get("tag")
    try:
        min_importance = max(1, min(5, int(args.get("min_importance", 1))))
    except (TypeError, ValueError):
        min_importance = 1
    try:
        limit = max(1, min(48, int(args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    rows = listing.list_rows(MemoryStore(), scope,
                             str(tag) if tag else None, min_importance)
    if not rows:
        return listing.empty_message(bool(scope or tag or min_importance > 1))
    shown = rows[:limit]
    out = listing.format_markdown(shown)
    hidden = len(rows) - len(shown)
    if hidden:
        out += f"\n\n+{hidden} more (raise limit to see all {len(rows)})"
    return out


HANDLERS = {"memory_recall": tool_recall, "memory_save": tool_save,
            "memory_stats": tool_stats, "memory_list": tool_list}


def respond(req_id, result=None, error=None) -> None:
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _stdin_lines():
    """Yield input lines decoded as UTF-8.

    Real stdio is read as bytes: sys.stdin would use the locale codec
    (cp1252 on Windows), mojibaking non-ASCII content. Text streams
    (tests, unusual hosts) are used as-is.
    """
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        yield from sys.stdin
        return
    for raw in stream:
        yield raw.decode("utf-8", "replace")


def serve() -> None:
    for line in _stdin_lines():
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict) or "method" not in req:
            continue
        method = req.get("method", "")
        req_id = req.get("id")
        if not isinstance(method, str):
            respond(req_id, error={"code": -32600, "message": "method must be a string"})
            continue
        if "id" not in req:
            continue
        if method.startswith("notifications/"):
            continue
        try:
            if method == "initialize":
                respond(req_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                })
            elif method == "tools/list":
                respond(req_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name", "")
                args = params.get("arguments", {})
                handler = HANDLERS.get(name)
                if handler is None:
                    respond(req_id, error={"code": -32602,
                                           "message": f"unknown tool: {name}"})
                else:
                    text = handler(args if isinstance(args, dict) else {})
                    respond(req_id, {"isError": text.startswith("error:"),
                                     "content": [{"type": "text",
                                                  "text": text}]})
            elif method == "ping":
                respond(req_id, {})
            else:
                respond(req_id, error={"code": -32601,
                                       "message": f"unknown method: {method}"})
        except Exception as exc:  # noqa: BLE001 - must answer, never crash
            if req_id is not None:
                respond(req_id, error={"code": -32603, "message": str(exc)[:500]})


if __name__ == "__main__":
    serve()

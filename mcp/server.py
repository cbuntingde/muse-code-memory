#!/usr/bin/env python3
"""MCP stdio server exposing memory tools to the agent. Standard library only.

Protocol: newline-delimited JSON-RPC 2.0 over stdio (MCP stdio transport).
Methods: initialize, tools/list, tools/call, ping, plus notifications/* ignore.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVER_INFO = {"name": "muse-memory", "version": "1.1.0"}

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
                "supersedes": {"type": "string",
                               "description": "Topic file this memory "
                                              "replaces; it is archived"},
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


def _service():
    from muse_memory.service import MemoryService
    return MemoryService()


def tool_recall(args: dict) -> str:
    svc = _service()
    try:
        return svc.recall_text(
            str(args.get("query", "")),
            top_k=int(args.get("top_k", 0) or svc.cfg["recall_top_k"]),
            include_archive=bool(args.get("include_archive", False)),
            since=str(args["since"]) if args.get("since") else None,
            until=str(args["until"]) if args.get("until") else None)
    except ValueError as exc:
        return f"error: {exc}"


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
    # An explicit scope always wins; otherwise the configured default.
    svc = _service()
    try:
        result = svc.save(
            text, topic=str(args.get("topic") or "") or None,
            scope=str(args.get("scope") or "") or None,
            importance=int(args.get("importance", 3)),
            tags=list(args.get("tags", [])),
            supersedes=str(args.get("supersedes") or "") or None)
    except ValueError as exc:
        return f"error: {exc}"
    secret_kinds = result.pop("redacted", [])
    if result.get("action") == "blocked-secret":
        return ("error: refused for project-shared scope: credential-like "
                f"content ({', '.join(result.get('blocked', []))})")
    headline = (f"muse-memory saved: {result['scope']}/{result['file']} "
                f"({result['action']})")
    if secret_kinds:
        headline += f" [secrets redacted: {', '.join(secret_kinds)}]"
    return headline + "\n" + json.dumps(result)


def tool_stats(_args: dict) -> str:
    return json.dumps(_service().stats(), indent=2)


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
    rows = _service().list_rows(scope, str(tag) if tag else None,
                                min_importance)
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

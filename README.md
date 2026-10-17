# muse-memory — Automatic Memory + RAG for Muse Code

Zero-dependency, hook-driven memory for [Muse Code](https://dev.meta.ai/products/muse-code).
Install the plugin, restart `muse`, and durable facts start surviving across sessions —
captured automatically, recalled with built-in retrieval, and maintained without attention.

- **Three scopes** — `global` (machine-wide), `project-personal` (private per project,
  shares the native Muse directory), `project-shared` (`<repo>/.agents/memory`, committed)
- **Automatic capture** — tool outputs mined for durable prose (payload dumps and
  code fragments rejected), failures recorded, transcripts mined. Captures queue as
  proposals: rules, decisions, and importance-4+ facts file at Stop/SessionEnd, the
  rest are held for `/approve` and expire after 7 days (`proposal_ttl_days`)
- **Built-in RAG** — SQLite FTS5 BM25 retrieval (stdlib `sqlite3`, lexical + recency +
  importance + use signals) over a disposable per-scope `.rag-index.db`, rebuilt from
  the Markdown topics whenever they change; works offline in hook context, with an
  in-memory BM25 fallback when FTS5 is unavailable
- **Self-maintenance** — fragment retirement, dedupe, topic merge, expiry, stale
  archival, oversize trim, and file-cap retirement (at stop and session end;
  SessionStart and prompt recall are read-only; pinned topics protected)
- **Safe by design** — pasted credentials dropped, secret values redacted before filing;
  every hook is exception-safe and exits 0, with real errors in a rotated diagnostics log
- **Full agent surface** — 4 MCP tools, 8 slash commands, and a 23-command CLI sharing
  one implementation (`muse_memory/service.py`)

Status: version 1.1.0. `muse skills validate` and `muse plugins validate` both return
`valid: true` with empty diagnostics, and 197 unit/contract tests pass
(`python3 -m unittest discover -s tests`). The installed plugin was verified live on
Muse 1.4.7: marketplace update, upgrade, a headless-session `memory_save` /
`memory_recall` round-trip, a second session recalling the first session's save, and
uninstall + reinstall (see AUDIT.md). The golden evaluation (`python3 -m muse_memory
eval`) reports recall@1, recall@3, capture precision, and capture recall at 1.0 on its
frozen corpus.

## Install

Requires Python 3.9+ on `PATH` as `python3` (hooks, MCP server) and `muse` 1.x. On Windows, make sure the `python3` alias exists: either install via `winget install -e --id Python.Python.3`, or enable the Microsoft Store "Python" app execution alias (Settings > Apps > Advanced app settings); the python.org installer alone only provides `python`.

```bash
muse plugins marketplace add muse-memory https://github.com/cbuntingde/muse-code-memory.git
muse plugins install muse-memory@muse-memory
muse plugins approve muse-memory
muse plugins enable muse-memory
```

The repo ships `.agents/plugins/marketplace.json`, which Muse reads as the
marketplace catalog. Memory stores initialize automatically on the first
session — no manual seeding needed.

Refresh the catalog before upgrading: `muse plugins marketplace update muse-memory`,
then `muse plugins update muse-memory`. Restart `muse` afterwards so hooks load.

> **Windows note:** Git-URL marketplace sources currently fail on Windows with
> `ERROR: Invalid syntax. Default option is not allowed more than '1' time(s).`
> Muse shells its clone through GNU `timeout`, but Windows resolves that to
> `System32\timeout.exe`, which rejects the flags (verified byte-identical).
> There is no user-side fix — Windows searches System32 before `PATH`.
> Workaround: clone first, then add the local path:
>
> ```powershell
> git clone https://github.com/cbuntingde/muse-code-memory.git
> muse plugins marketplace add muse-memory .\muse-code-memory
> muse plugins install muse-memory@muse-memory
> ```

## How it works

```
SessionStart        -> ensure stores, inject digest (read-only: no sweeps, no index writes)
UserPromptSubmit    -> RAG recall over all scopes, inject top hits; use signals queued
PostToolUse         -> extract candidates from tool outputs (fragments rejected), queue
PostToolUseFailure  -> same with importance boost + redacted error fallback
PreCompact          -> snapshot key topics so compaction cannot lose them
Stop                -> apply use signals, file high-confidence proposals, enforce file cap
SessionEnd          -> mine transcript tail, file proposals, full maintenance
```

Capture is heuristic and precision-biased: explicit decisions, always/never rules,
preferences, fixes, milestones, and project changes (everyday build verbs only
with project context; stack statements and dev-doc/plan paths auto-file). Chatter and task-local detail are ignored, and tool
payloads — JSON dumps, patch bodies, line-numbered dumps, escaped text, terminal
mojibake, session-log paths — are rejected at extraction time, so raw code and
logs never become memory files. Legacy fragments already on disk are retired to
`archive/` automatically at the next session end (SessionStart is read-only).
Pasted credentials are dropped at capture time and remaining secret values are
redacted (`[redacted:<kind>]`) before anything is filed — including slugs derived
for topic filenames. Bare security words without values ("token", "password")
are left alone. The committed `project-shared` scope is fail-closed: credential-like
or high-entropy content is refused outright there instead of being redacted and filed.

Storage per scope: human-readable topic Markdown files (each with documentary
frontmatter) + a grouped `MEMORY.md` index with section headers and live footer
counts, plus dotfile sidecars (`.memory-meta.json`, `.rag-index.db`,
`.pending.jsonl`, `.access.jsonl`, `.snapshot-*`, `archive/`, `.trash/`). The project-personal scope adopts
Muse's native slug directory when one already exists, so both systems share
files instead of forking. Machine truth lives in the sidecars; topic bodies
roundtrip byte-identically.

## Agent surface

Slash commands:

| Command      | Purpose                                                    |
|--------------|------------------------------------------------------------|
| `/remember`  | Save a durable memory now                                  |
| `/recall`    | Search memory with RAG                                     |
| `/memories`  | List stored memories in a grouped, filterable table        |
| `/forget`    | Trash a memory topic (restorable; `--purge` deletes)       |
| `/archive`   | Retire a topic (still searchable with archive recall)      |
| `/consolidate` | File pending captures and run maintenance now            |
| `/approve`   | File all held capture proposals now                        |
| `/restore`   | Restore a trashed topic (undoes `/forget`)                 |

MCP tools (`memory` server over stdio):

| Tool           | Purpose                                                        |
|----------------|----------------------------------------------------------------|
| `memory_recall`| RAG recall (`query`, `top_k`, `include_archive`)               |
| `memory_save`  | Save one durable statement (auto-redacts; `supersedes` archives a replaced topic) |
| `memory_list`  | Enumerate topics as a Markdown table (filters + `limit`)       |
| `memory_stats` | Per-scope files, importance histogram, queue, archive, index   |

The `memory` skill teaches agents the recall-first / save-deliberately workflow.
Listing never marks topics as used, so browsing cannot poison RAG's use signal.

## CLI

```bash
export PYTHONPATH=/path/to/muse-code-memory   # or pip install -e .
python3 -m muse_memory init                    # seed stores + indexes
python3 -m muse_memory recall "deploy process" # RAG recall (--explain, --include-archive)
python3 -m muse_memory save "We use Postgres" --auto
python3 -m muse_memory list --tag decision --min-importance 3 --format markdown
python3 -m muse_memory doctor | pending | config   # health, queue, effective config
python3 -m muse_memory forget|archive|pin|unpin <scope/file>
python3 -m muse_memory export --output bundle.md  # import restores (metadata included)
python3 -m muse_memory consolidate | cleanup | stats | reindex | paths
python3 -m muse_memory approve                 # file all held capture proposals
python3 -m muse_memory restore <scope/file>    # undo a forget (trash -> live)
python3 -m muse_memory eval                    # golden recall/capture evaluation
```

`save --supersedes <file>` archives the topic a new memory replaces and links both
records. `forget` trashes a topic (restore with `restore`; `--purge` deletes permanently);
`archive` retires to `archive/` (excluded from
recall unless requested); `pin` reserves index slots under the 48-file cap and
exempts topics from auto-retirement. `import` restores a bundle byte-identically
including importance/tags, and refuses to overwrite existing files without
`--force`.

Config (all optional, invalid values fall back to defaults):
`~/.config/muse/memory/config.json` (user) or `<repo>/.muse/memory.json`
(project). See `config/memory.json` for the core keys with defaults; advanced
thresholds (`dedupe_threshold`, `merge_threshold`, `stale_days`, `scopes`, and
capture-size limits) remain accepted but undocumented. Recorded design decisions
(embeddings and session-end model summarisation parked under the stdlib-only
offline constraint; explicit supersede instead of contradiction detection) are
in `dev/IMPROVEMENTS.md`.

## Layout

```
.muse-plugin/plugin.json   native manifest (7 hooks, 1 skill, 8 commands, 1 MCP server)
.agents/plugins/marketplace.json  marketplace catalog for Git-based install
hooks/                     one script per lifecycle event + _common.py (import-only)
muse_memory/               stdlib-only core: paths, store, rag (SQLite FTS5), extractor,
                           listing, consolidate, lifecycle, config, service, evalset, cli
mcp/server.py              MCP stdio server (recall/save/stats/list)
skills/memory/SKILL.md     agent instructions for using memory
commands/                  /remember /recall /memories /forget /archive /consolidate
                           /approve /restore
tests/                     197 unittest tests incl. hook + MCP contract tests and the
                           golden recall/capture eval (tests/test_eval_golden.py)
.github/workflows/ci.yml   unittest matrix + bundle validation when muse is present
examples/                  best-effort hooks.json / settings hooks block
```

## Corrections to common hook/plugin write-ups

Verified against the official docs and the installed Muse Code binary:

- Failure event is `PostToolUseFailure`, not `ToolUseFailure`; 6 more events exist
  (`PreLLMCall`, `PostLLMCall`, `PreCompact`, `PostCompact`, `SubagentStart`,
  `SubagentStop`).
- Plugins no longer need `MUSE_EXPERIMENTAL_PLUGINS=1` on Muse 1.4.3 —
  `muse plugins ...` works directly.
- Plugin capabilities are exactly `skills`, `commands`, `hooks`, `mcpServers`,
  `reminders` — a direct `tools` capability is rejected; custom tools go behind
  `mcpServers`.

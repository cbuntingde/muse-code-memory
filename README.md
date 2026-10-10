# muse-memory — Automatic Memory + RAG for Muse Code

Zero-dependency, hook-driven memory for [Muse Code](https://dev.meta.ai/products/muse-code).
Install the plugin, restart `muse`, and durable facts start surviving across sessions —
captured automatically, recalled with built-in retrieval, and maintained without attention.

- **Three scopes** — `global` (machine-wide), `project-personal` (private per project,
  shares the native Muse directory), `project-shared` (`<repo>/.agents/memory`, committed)
- **Automatic capture** — tool results observed, failures recorded, transcripts mined
- **Built-in RAG** — BM25-hybrid retrieval (lexical + recency + importance + use signals)
  over per-scope JSON indexes; standard library only, works offline in hook context
- **Self-maintenance** — dedupe, topic merge, expiry, stale archival, oversize trim,
  file-cap retirement with pin protection
- **Safe by design** — pasted credentials dropped, secret values redacted before filing;
  every hook is exception-safe and exits 0, with real errors in a rotated diagnostics log
- **Full agent surface** — 4 MCP tools, 6 slash commands, and a 20-command CLI sharing
  one implementation

Status: `muse skills validate` and `muse plugins validate` both return `valid: true`
with empty diagnostics. 102 unit/contract tests pass
(`python -m unittest discover -s tests`).

## Install

Requires Python 3.9+ on `PATH` as `python` (hooks) and `muse` 1.x.

```powershell
# Windows (PowerShell, from this directory)
.\install.ps1
```

```bash
# macOS / Linux
./install.sh
```

This runs tests, validates the bundles, installs + approves + enables the plugin
(user scope), and seeds the stores. Restart `muse` afterwards so hooks load.

Manual equivalent:

```bash
muse plugins install /path/to/muse-code-memory --scope user
muse plugins approve muse-memory
muse plugins enable muse-memory
PYTHONPATH=/path/to/muse-code-memory python -m muse_memory init
```

To use plain hook files instead of the plugin (e.g. per-project), copy
`examples/hooks.json` to `<project>/.muse/hooks.json` and adjust the paths.
This is best-effort (Muse publishes no project-hooks schema); the plugin is
the supported path.

### From the Git marketplace

Install without cloning — the repo ships `.agents/plugins/marketplace.json`,
which Muse reads as the marketplace catalog:

```bash
muse plugins marketplace add muse-memory https://github.com/cbuntingde/muse-code-memory.git
muse plugins install muse-memory@muse-memory
muse plugins approve muse-memory
muse plugins enable muse-memory
```

Refresh the catalog before upgrading: `muse plugins marketplace update <name>`,
then `muse plugins update muse-memory`. Restart `muse` afterwards so hooks load.

## How it works

```
SessionStart        -> ensure stores, rebuild stale RAG indexes, inject digest
UserPromptSubmit    -> RAG recall over all scopes, inject top hits as context
PostToolUse         -> extract candidates (decisions/rules/fixes/facts), queue
PostToolUseFailure  -> same with importance boost + redacted error fallback
PreCompact          -> snapshot key topics so compaction cannot lose them
Stop                -> file queued candidates (create/append/skip-if-duplicate)
SessionEnd          -> mine transcript tail, file everything, full maintenance
```

Capture is heuristic and precision-biased: explicit decisions, always/never rules,
preferences, fixes, and durable facts. Chatter and task-local detail are ignored.
Pasted credentials are dropped at capture time and remaining secret values are
redacted (`[redacted:<kind>]`) before anything is filed — including slugs derived
for topic filenames. Bare security words without values ("token", "password")
are left alone.

Storage per scope: human-readable topic Markdown files (each with documentary
frontmatter) + a grouped `MEMORY.md` index with section headers and live footer
counts, plus dotfile sidecars (`.memory-meta.json`, `.rag-index.json`,
`.pending.jsonl`, `.snapshot-*`, `archive/`). The project-personal scope adopts
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
| `/forget`    | Permanently delete a memory topic                          |
| `/archive`   | Retire a topic (still searchable with archive recall)      |
| `/consolidate` | File pending captures and run maintenance now            |

MCP tools (`memory` server over stdio):

| Tool           | Purpose                                                        |
|----------------|----------------------------------------------------------------|
| `memory_recall`| RAG recall (`query`, `top_k`, `include_archive`)               |
| `memory_save`  | Save one durable statement (auto-redacts secrets)              |
| `memory_list`  | Enumerate topics as a Markdown table (filters + `limit`)       |
| `memory_stats` | Per-scope files, importance histogram, queue, archive, index   |

The `memory` skill teaches agents the recall-first / save-deliberately workflow.
Listing never marks topics as used, so browsing cannot poison RAG's use signal.

## CLI

```bash
export PYTHONPATH=/path/to/muse-code-memory   # or pip install -e .
python -m muse_memory init                    # seed stores + indexes
python -m muse_memory recall "deploy process" # RAG recall (--explain, --include-archive)
python -m muse_memory save "We use Postgres" --auto
python -m muse_memory list --tag decision --min-importance 3 --format markdown
python -m muse_memory doctor | pending | config   # health, queue, effective config
python -m muse_memory forget|archive|pin|unpin <scope/file>
python -m muse_memory export --output bundle.md  # import restores (metadata included)
python -m muse_memory consolidate | cleanup | stats | reindex | paths
```

`forget` deletes permanently; `archive` retires to `archive/` (excluded from
recall unless requested); `pin` reserves index slots under the 48-file cap and
exempts topics from auto-retirement. `import` restores a bundle byte-identically
including importance/tags, and refuses to overwrite existing files without
`--force`.

Config (all optional, invalid values fall back to defaults):
`~/.config/muse/memory/config.json` (user) or `<repo>/.muse/memory.json`
(project). See `config/memory.json` for every key with defaults.

## Layout

```
.muse-plugin/plugin.json   native manifest (7 hooks, 1 skill, 6 commands, 1 MCP server)
.agents/plugins/marketplace.json  marketplace catalog for Git-based install
hooks/                     one script per lifecycle event + _common.py (import-only)
muse_memory/               stdlib-only core: paths, store, rag, extractor, listing,
                           consolidate, lifecycle, config, cli
mcp/server.py              MCP stdio server (recall/save/stats/list)
skills/memory/SKILL.md     agent instructions for using memory
commands/                  /remember /recall /memories /forget /archive /consolidate
tests/                     102 unittest tests incl. hook + MCP contract tests
.github/workflows/ci.yml   unittest matrix + bundle validation when muse is present
examples/                  best-effort hooks.json / settings hooks block
docs/HOOKS-PLUGINS-RESEARCH.md  what the supplied brief got right/wrong, with sources
```

## Corrections to common hook/plugin write-ups

Research details in `docs/HOOKS-PLUGINS-RESEARCH.md`. Short version:

- Failure event is `PostToolUseFailure`, not `ToolUseFailure`; 6 more events exist
  (`PreLLMCall`, `PostLLMCall`, `PreCompact`, `PostCompact`, `SubagentStart`,
  `SubagentStop`).
- Plugins no longer need `MUSE_EXPERIMENTAL_PLUGINS=1` on Muse 1.4.3 —
  `muse plugins ...` works directly.
- Plugin capabilities are exactly `skills`, `commands`, `hooks`, `mcpServers`,
  `reminders` — a direct `tools` capability is rejected; custom tools go behind
  `mcpServers`.

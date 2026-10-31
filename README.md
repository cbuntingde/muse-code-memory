# muse-memory — Automatic Memory for Muse Code

Memory for [Muse Code](https://dev.meta.ai/products/muse-code) that needs nothing beyond Python.
Install the plugin, restart `muse`, and your decisions, files, and commands start surviving across sessions —
saved automatically, found when you need them, and tidied without attention.

- **Three scopes** — `global` (machine-wide), `project-personal` (private per project,
  shares the native Muse directory), `project-shared` (`<repo>/.agents/memory`, committed)
- **Automatic capture** — reads tool output for lasting facts (raw data and code
  skipped), records failures, and reviews transcripts. Most captures wait as
  proposals: rules, decisions, build commands, project facts, and other
  high-confidence items are filed when a turn or session ends, while the rest
  are held for `/approve` and expire after 7 days (`proposal_ttl_days`)
- **Project basics, remembered** — build and test commands, files you work with,
  and files named in discussion are stored word-for-word in kept topics
  (command ledger, file registry), and each project gets an auto-written profile
  (tools, setup files, docs, code location). These file promptly instead of
  waiting for approval, and the profile plus recent commands and files are
  added to every prompt — no search needed.
- **Built-in search** — finds relevant memories by wording, recency, and importance.
  Works offline with nothing to install; the search index is rebuilt from your
  memory files whenever they change.
- **Self-maintenance** — removes duplicates and clutter, archives stale items,
  and never touches pinned topics.
- **Safe by design** — passwords and keys are removed before anything is saved, and
  team-shared memory refuses anything secret-like outright. Errors go to a log
  instead of interrupting your session.
- **Full agent surface** — 4 MCP tools, 8 slash commands, and a 23-command CLI
  that all behave the same.

Status: version 1.1.0. `muse skills validate` and `muse plugins validate` both return
`valid: true` with empty diagnostics, and 275 unit/contract tests pass
(`python3 -m unittest discover -s tests`). The installed plugin was verified live on
Muse 1.4.7: marketplace update, upgrade, a headless-session `memory_save` /
`memory_recall` round-trip, a second session recalling the first session's save, and
uninstall + reinstall (see AUDIT.md). The quality check (`python3 -m muse_memory
eval`) reports recall@1, recall@3, capture precision, and capture recall at 1.0 on its
frozen corpus, plus a project-facts corpus (files, docs, commands, profile)
also at 1.0.

## Install

Requires Python 3.11+ on `PATH` as `python3` (hooks, MCP server) and `muse` 1.x. On Windows, make sure the `python3` alias exists: either install via `winget install -e --id Python.Python.3`, or enable the Microsoft Store "Python" app execution alias (Settings > Apps > Advanced app settings); the python.org installer alone only provides `python`.

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
SessionStart        -> ensure stores, inject digest + project basics;
                       queue a profile scan when missing or outdated (startup reads only —
                       saved memories are never changed there)
UserPromptSubmit    -> project basics + relevant memories for this prompt; usage noted quietly
PostToolUse         -> pull possible memories from tool output (raw data skipped), queue;
                       shell commands, authored files, and named project files queue as facts
PostToolUseFailure  -> same, treated as more important, with secrets removed from errors
PreCompact          -> snapshot key topics first so compaction cannot lose them
Stop                -> record usage, file confident captures, enforce the file cap
SessionEnd          -> review the session transcript, file proposals, full cleanup
```

The plugin saves what is worth keeping: decisions, standing rules, preferences,
fixes, milestones, project changes, and practical facts (build and test commands,
version checks, setup-file notes). Everyday chat and task-local detail are ignored,
and raw tool output — data dumps, patch text, numbered code listings, garbled text,
session-log paths — is never saved as memory (for shell tools the command itself is
kept alongside the output, so a bare version number still makes sense). Outdated
machine artifacts already on disk are moved to `archive/` at the next session end.
Pasted passwords are dropped on sight and remaining secret values are
removed (`[redacted:<kind>]`) before anything is filed — including names derived
for topic files. Words like "token" or "password" on their own are left alone.
Team-shared memory refuses anything secret-like outright instead of saving a
cleaned-up copy.

Each scope keeps readable Markdown files — one per topic — plus a grouped `MEMORY.md`
index with live counts, alongside small helper files that hold settings
(`.memory-meta.json`, `.rag-index.db`, `.pending.jsonl`, `.access.jsonl`,
`.snapshot-*`, `archive/`, `.trash/`). When Muse already keeps project memory for
the workspace, the plugin shares that folder instead of starting a second one.
Three kept topics (`project-profile.md`, `command-ledger.md`, `file-registry.md`)
hold the project basics; kept topics are always listed and never auto-removed,
while the normal size limit still applies.

## Agent surface

Slash commands:

| Command      | Purpose                                                    |
|--------------|------------------------------------------------------------|
| `/remember`  | Save a durable memory now                                  |
| `/recall`    | Search memory                                     |
| `/memories`  | List stored memories in a grouped, filterable table        |
| `/forget`    | Trash a memory topic (restorable; `--purge` deletes)       |
| `/archive`   | Retire a topic (still searchable with archive recall)      |
| `/consolidate` | File pending captures and run maintenance now            |
| `/approve`   | File all held capture proposals now                        |
| `/restore`   | Restore a trashed topic (undoes `/forget`)                 |

MCP tools (`memory` server over stdio):

| Tool           | Purpose                                                        |
|----------------|----------------------------------------------------------------|
| `memory_recall`| Find relevant memories (`query`, `top_k`, `include_archive`)               |
| `memory_save`  | Save one durable statement (auto-redacts; `supersedes` archives a replaced topic) |
| `memory_list`  | Enumerate topics as a Markdown table (filters + `limit`)       |
| `memory_stats` | Per-scope files, importance histogram, queue, archive, index   |

The `memory` skill teaches agents the recall-first / save-deliberately workflow.
Browsing never affects what is found later.

### Reading topics with Muse's native `read_memory`

Muse's built-in memory tools (`read_memory`, `add_memory`, `edit_memory`) address
the same directories this plugin manages, but with their own scope names and
relative paths. Every recall hit, list row, and save result therefore carries a
read pointer, for example `personal_project/rule-conventions.md`:

```
- [project-personal/rule-conventions.md] Conventions: always run cargo check
  (read_memory personal_project/rule-conventions.md)
```

Pass that scope and path straight to `read_memory` to open the full topic.
Scope mapping: `global` -> `personal`, `project-personal` -> `personal_project`,
`project-shared` -> `project`.

The plugin adopts Muse's own project memory directory when it exists (the two systems name project folders differently, so the plugin matches on the
readable part of the name rather than the trailing code). Topics written before adoption
are copied across, and free-form notes that Muse wrote into `MEMORY.md` are
preserved when the plugin rewrites the index.

## CLI

```bash
export PYTHONPATH=/path/to/muse-code-memory   # or pip install -e .
python3 -m muse_memory init                    # seed stores + indexes
python3 -m muse_memory recall "deploy process" # find relevant memories (--explain, --include-archive)
python3 -m muse_memory save "We use Postgres" --auto
python3 -m muse_memory list --tag decision --min-importance 3 --format markdown
python3 -m muse_memory doctor | pending | config   # health, queue, effective config
python3 -m muse_memory forget|archive|pin|unpin <scope/file>
python3 -m muse_memory export --output bundle.md  # import restores (metadata included)
python3 -m muse_memory consolidate | cleanup | stats | reindex | paths
python3 -m muse_memory approve                 # file all held capture proposals
python3 -m muse_memory restore <scope/file>    # undo a forget (trash -> live)
python3 -m muse_memory eval                    # recall/capture quality check (general + project facts)
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
(project). See `config/memory.json` for the core keys with defaults, including
`working_set_max_chars` (size of the auto-added project block) and
`entity_sync_enabled` (master switch for the project basics); further
fine-tuning settings (`dedupe_threshold`, `merge_threshold`, `stale_days`,
`scopes`, and capture-size limits) still work but are not listed here. Two ideas
stay parked: meaning-based search (it needs downloads this plugin will not
require) and automatic contradiction handling (a replaced fact is handed off
explicitly instead).

## Layout

```
.muse-plugin/plugin.json   native manifest (7 hooks, 1 skill, 8 commands, 1 MCP server)
.agents/plugins/marketplace.json  marketplace catalog for Git-based install
hooks/                     one script per lifecycle event + _common.py (shared helpers)
muse_memory/               core with no extra downloads: paths, store, search, extractor,
                           entities, listing, consolidate, lifecycle, config, service,
                           evalset, dates, locking (+ `__main__` command line)
mcp/server.py              MCP stdio server (recall/save/stats/list)
skills/memory/SKILL.md     agent instructions for using memory
commands/                  /remember /recall /memories /forget /archive /consolidate
                           /approve /restore
tests/                     275 unittest tests incl. hook + MCP contract tests and both
                           quality evals (tests/test_eval_golden.py, tests/test_project_eval.py)
.github/workflows/ci.yml   tests on Python 3.11 + 3.14 (ubuntu + Windows) + bundle validation
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

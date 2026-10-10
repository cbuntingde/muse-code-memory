# muse-memory — Gaps & Improvements

Brainstorm date: 2026-10-10
Status: suggested next step (`/memories` list + grouped index template) implemented 2026-10-10 — 58 tests pass, skill + plugin bundles validate clean.
Status: secret/PII redaction guard implemented 2026-10-10 — 71 tests pass. Scope note: credentials, tokens, keys, and passwords-in-URLs are covered; plain filesystem paths are deliberately NOT redacted (project paths are legitimate durable facts).
Status: MCP memory_list + structured topic template implemented 2026-10-10 — 77 tests pass. Scope note: template is documentary frontmatter only (title/tags/importance/source/created); Summary/Details/Context section injection was cut because it would break the exact content-roundtrip contract, and no `updated` line is emitted because appends would leave it stale.
Status: remaining backlog implemented 2026-10-10 — 96 tests pass. Shipped: hook diagnostics log, CI workflow, richer stats (CLI+MCP), /forget + /archive (+CLI), config validation + `config`, `pending` + `doctor`, pin/unpin with index priority + cap immunity, export/import bundle, archive-aware recall (CLI+MCP), recall --explain. Deliberately NOT shipped: optional embeddings backend — it would violate the stdlib-only/offline-hooks constraint and scored -1.
Status: code-review fixes + README rewrite 2026-10-10 — 101 tests pass. Fixed: corrupt-index hardening (load + health), import metadata restore + --force no-clobber, first-hit recall guarantee, honest export scope report. README rewritten professionally with Muse Code link; fixed stale config example path and manifest counts.
Status: marketplace catalog added 2026-10-10 — 102 tests pass. `.agents/plugins/marketplace.json` verified against the real binary (`marketplace add` → plugin_count 1, `list --available` resolves with digest); test entry removed afterwards. Remaining step for Git installs: push the repo to GitHub.
Mode: Improve — gaps and friction in the existing memory loop, listing and templates first.
Suite status at review time: 47 unittest tests pass (`python -m unittest discover -s tests`).

## Snapshot

muse-memory is automatic cross-session memory plus BM25-hybrid RAG for Muse Code, stdlib-only Python (`pyproject.toml:1`).
It serves solo developers and small teams via 3 scopes, 7 lifecycle hooks, 3 MCP tools, and 3 slash commands (`muse_memory/paths.py:187`, `mcp/server.py:16`, `.muse-plugin/plugin.json:1`).
Mature core: 47 unittest tests pass, hook-driven capture plus SessionEnd dedupe/merge/expire/trim (`muse_memory/consolidate.py:226`, `muse_memory/lifecycle.py:160`).
Core promise: durable facts survive sessions with zero dependencies and hooks that never break a session (`hooks/_common.py:93`).
Assumption: personal use first, team sharing via committed scope second; no new dependencies allowed.

## Top picks

### 1. Professional /memories list command — p=8 (i3/e1/c3)

Grouped professional listing of project memories with scope sections, importance badges, and filters.

- Why here: current `list` prints one flat line per file with no grouping, dates, tags, or summary (`muse_memory/__main__.py:84`).
- Sketch: extend `cmd_list` in `muse_memory/__main__.py` with `--scope/--tag/--min-importance/--format markdown|table`, add `commands/memories.md` calling it, and register the command id in `.muse-plugin/plugin.json`.
- Impact / Effort / Confidence: 3 / 1 / 3

### 2. Professional MEMORY.md index template — p=8 (i3/e1/c3)

Rewrite `refresh_index` to emit grouped sections, importance badges, updated dates, and footer stats instead of flat bullets.

- Why here: `refresh_index` emits one unsorted-by-date bullet per file and hides overflow behind an HTML comment (`muse_memory/store.py:274`).
- Sketch: rewrite `ScopeStore.refresh_index` in `muse_memory/store.py` to group by Standing rules / Decisions / Fixes / Facts, add `imp:5 · updated 2026-10-10 · #tag` suffixes, and append a footer with counts plus archive pointer.
- Impact / Effort / Confidence: 3 / 1 / 3

### 3. Secret and PII redaction guard [foundation] — p=8 (i3/e1/c3)

Block API keys, tokens, and private paths at extract and save time; extractor currently has zero secret patterns.

- Why here: extractor matches decisions and rules but has zero secret patterns, so a pasted key sentence would file as a note (`muse_memory/extractor.py:84`).
- Sketch: add `REDACT_PATTERNS` plus `redact()` in `muse_memory/extractor.py`, call it from `extract_candidates` and `mcp/server.py` `tool_save`, and add `tests/test_redaction.py` on `IsolatedCase` (`tests/helpers.py:13`).
- Impact / Effort / Confidence: 3 / 1 / 3

### 4. MCP memory_list tool — p=5 (i3/e1/c2)

Add `memory_list` to MCP so agents can list project memories without shelling to CLI.

- Why here: MCP exposes only recall, save, and stats, so an agent cannot enumerate project topics (`mcp/server.py:16`).
- Sketch: add `memory_list` to `TOOLS` in `mcp/server.py` reusing `ScopeStore.read_all` from `muse_memory/store.py`, with `scope/top_n/min_importance` args and the same Markdown row format as the CLI list.
- Impact / Effort / Confidence: 3 / 1 / 2

### 5. Structured new-topic template — p=5 (i2/e1/c3)

New topics get frontmatter plus Summary/Details/Context sections so every file starts professional and RAG-friendly.

- Why here: `write_topic` writes only `# Title` plus one bullet with no summary or metadata header (`muse_memory/store.py:191`).
- Sketch: add `render_topic()` in `muse_memory/store.py` emitting frontmatter plus `## Summary`, `## Details`, `## Context` sections, and call it from `write_topic` and `file_candidate` in `muse_memory/consolidate.py:169`.
- Impact / Effort / Confidence: 2 / 1 / 3

## More ideas

Quick wins:

- Tag and importance filters (`muse_memory/__main__.py:191`) — p=5 (i2/e1/c3). Add `--tag`, `--min-importance`, `--scope` to list and recall.
- Richer memory_stats output (`mcp/server.py:114`) — p=3 (i2/e1/c2). Importance histogram, pending depth, archive counts, index health.
- Forget and archive commands (`muse_memory/store.py:237`) — p=3 (i2/e1/c2). Expose existing delete/archive as CLI plus `/forget` and `/archive`.
- Config validation plus show (`muse_memory/config.py:38`) — p=2 (i1/e1/c3). Range-check values on load, add `config show`.
- Fix stale test count and docs (`README.md:12`) — p=2 (i1/e1/c3). README says 41 tests, suite runs 47; document new list commands.

Big bets: none scored above threshold.

Foundations:

- CI gate for tests plus validates (`AGENTS.md:5`) — p=5 (i2/e1/c3). Actions job running unittest plus both `muse validate` gates; repo has no CI today.
- Hook diagnostics log file (`hooks/_common.py:93`) — p=5 (i2/e1/c3). Rotating log for the 10 silent handlers instead of stderr-only.

Parked:

- Pending queue and doctor command (`muse_memory/lifecycle.py:130`) — p=2 (i2/e2/c2).
- Shared-memory export and import (`muse_memory/paths.py:179`) — p=2 (i2/e2/c2).
- Archive-aware recall toggle (`muse_memory/store.py:249`) — p=1 (i1/e1/c2).
- Over-48-files overflow UX (`muse_memory/store.py:295`) — p=0 (i2/e2/c1).
- Recall explainability flag (`muse_memory/rag.py:144`) — p=0 (i1/e2/c2).
- Optional embeddings backend (`muse_memory/rag.py:1`) — p=-1 (i2/e3/c1).

## Risks and blind spots

- Assumed personal-use priority and stdlib-only constraint from `AGENTS.md:3`; team-shared template needs may differ.
- Could not verify live hook payloads or RAG recall quality on real transcripts; scores assume current BM25 baseline in `muse_memory/rag.py:144` is adequate.
- Trap: over-templating MEMORY.md could bloat the 48-file context injection (`muse_memory/store.py:295`); keep index rows under ~140 chars.
- Trap: an embeddings backend (`muse_memory/rag.py:1`) looks tempting but breaks offline hook use; parked intentionally.

## Suggested next step

Implement the professional `/memories` list plus MEMORY.md template first since they directly answer the listing ask: `cmd_list` filters, `commands/memories.md`, and the grouped `refresh_index` with tests.

## Full ranked table

Priority = impact x confidence - effort (threshold 5). 18 ideas, 5 worth acting on now.

| Bucket | Idea | p | i/e/c | Evidence |
|---|---|---|---|---|
| Quick win | Professional /memories list command | 8 | 3/1/3 | muse_memory/__main__.py:84 |
| Quick win | Professional MEMORY.md index template | 8 | 3/1/3 | muse_memory/store.py:274 |
| Quick win | MCP memory_list tool | 5 | 3/1/2 | mcp/server.py:16 |
| Quick win | Structured new-topic template | 5 | 2/1/3 | muse_memory/store.py:191 |
| Quick win | Tag and importance filters | 5 | 2/1/3 | muse_memory/__main__.py:191 |
| Foundation | Secret and PII redaction guard | 8 | 3/1/3 | muse_memory/extractor.py:84 |
| Foundation | CI gate for tests plus validates | 5 | 2/1/3 | pyproject.toml:1 |
| Foundation | Hook diagnostics log file | 5 | 2/1/3 | hooks/_common.py:93 |
| Parked | Forget and archive commands | 3 | 2/1/2 | muse_memory/store.py:237 |
| Parked | Richer memory_stats output | 3 | 2/1/2 | mcp/server.py:114 |
| Parked | Config validation plus show | 2 | 1/1/3 | muse_memory/config.py:38 |
| Parked | Fix stale test count and docs | 2 | 1/1/3 | README.md:12 |
| Parked | Pending queue and doctor command | 2 | 2/2/2 | muse_memory/lifecycle.py:130 |
| Parked | Shared-memory export and import | 2 | 2/2/2 | muse_memory/paths.py:179 |
| Parked | Archive-aware recall toggle | 1 | 1/1/2 | muse_memory/store.py:249 |
| Parked | Over-48-files overflow UX | 0 | 2/2/1 | muse_memory/store.py:295 |
| Parked | Recall explainability flag | 0 | 1/2/2 | muse_memory/rag.py:144 |
| Parked | Optional embeddings backend | -1 | 2/3/1 | muse_memory/rag.py:1 |

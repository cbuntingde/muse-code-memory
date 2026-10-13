# Plugin code audit — 2026-10-13

Reviewed source based on commit `7d3bfcca8469c9b7b2a9288940e64875451a235d`, plus the
uncommitted fixes in this checkout. Scope: Python core, seven hooks, MCP stdio
server, CLI, installers, manifest, skill/command instructions, tests and CI.
Security checklist: ASVS Level 2 as a review baseline; this is not an ASVS
certification or a complete verification of every control.

## Result

The plugin had real defects, including lost captures, incomplete credential
redaction and a malformed-request server crash. Verified defects found during
this review were fixed. Passing checks do not establish that the plugin has no
bugs or that it is production-ready in every host configuration.

## Fixed security finding

**Medium, high confidence — credential material could reach memory files.**
CWE-532/CWE-200; CVSS v3.1 `AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N` (5.5).
Location: `muse_memory/extractor.py:64`, `SECRET_PATTERNS` and `_sub_group`.
The original private-key pattern replaced only the BEGIN delimiter. The
assignment pattern required an unquoted key immediately before `:`/`=` and
matched values only up to the first space. Consequently JSON passwords and
private-key bodies survived redaction. Reachable chain: MCP/CLI save or failure
capture -> `redact_secrets` -> topic or pending storage. The failure-capture
fallback permits raw error text to be filed even when heuristic extraction
drops it. A local process able to supply such text could cause credentials to
be persisted; exposure depends on who can read/share the resulting files.

Fix: redact complete PEM blocks (including incomplete tails), JSON-style keys,
and complete quoted values; preserve idempotence for redaction markers.
Regression cases failed before and pass after. Remediation is complete in
source; no outstanding SLA. Relevant control area: sensitive-data protection.
This is pattern-based redaction, not a guarantee of detecting arbitrary secrets.

## Fixed correctness findings

| Evidence / location | Defect and resulting fix |
|---|---|
| `lifecycle.py`, `drain_pending`: per-scope slicing followed by `out[:max_items]` | Other scopes' removed candidates were discarded. Enforce one global drain budget. |
| `lifecycle.py`, Stop/SessionEnd/CLI filing | Queues were drained before writes succeeded. New `file_pending` acknowledges only successful filing, retains failures, and uses atomic queue replacement under a scope lock. |
| `consolidate.py`, `file_candidate`: unconditional `write_topic(topic, ...)` after failed fuzzy matching | Existing slug content could be replaced. Append on filename collision. |
| `consolidate.py`, `merge_scope` | A retired outer-loop record could continue participating in merges. Stop when it is retired and refresh retained content after merges. Pinned topics are excluded from automatic merging. |
| `consolidate.py`, `expire_scope` | Pins were ignored during expiry/stale archival. Skip pinned records. |
| `consolidate.py`, `trim_scope` | An oversized first bullet could leave an empty body. Keep bounded content and continue considering smaller bullets. |
| `store.py`, metadata/topic/index writes; `lifecycle.py`, queue writes | Independent hook processes could collide on shared temporary files or lose updates. Add bounded, reentrant OS/thread scope locks; retain dirty metadata until replacement succeeds. Three subprocesses append 30 distinct items without loss in the regression test. |
| `store.py`, `archive_topic`/`read_archive` | Repeated archive names could overwrite on POSIX or fail on Windows; archival discarded ranking metadata. Keep unique archive names, archive metadata and distinct archive record IDs. |
| `rag.py`, `chunk_record`: `para[:max_chars]` | Long-paragraph tails were absent from retrieval. Split every segment. |
| `rag.py`, cache freshness / `index_health`: `data['chunks']` | External deletion could leave stale hits; `{}` caused a health-check exception. Add source fingerprints and validate the required chunks field. |
| `store.py`, `MemoryStore`; hooks and `lifecycle.py` | Configured scopes/default scope were ignored in several paths; disabled capture still mined transcripts. Honor scope selection/defaults and gate transcript capture. Unavailable save scopes fail instead of silently redirecting. |
| `mcp/server.py`, `serve`: `method.startswith(...)` before guarded dispatch | A non-string method killed the server. Reject it and continue serving. Reject invalid save text/importance/tags and mark returned tool errors with `isError`. |
| CLI deletion/archive/manual consolidation; `install.ps1` | Some failed operations reported success. Check return values/exit codes; return actual filing counts and errors. |

These are functional correctness findings; no attacker-controlled remote
exploit was established, so security CVSS scores are not assigned to them.

## Verification

- `python -m unittest discover -s tests`: **167 passed**, Windows / Python 3.14.
  Seventeen regression tests added. The first eight-test reproducer exposed
  five failures and two errors before fixes; the collision case was then
  strengthened to avoid an incidental fuzzy match.
- Subprocess tests exercise hooks and MCP stdio; new tests also exercise
  concurrent storage writes and failed-write retry behavior.
- `python -m compileall -q muse_memory hooks mcp`: passed.
- `muse skills validate ./skills/memory --json` and
  `muse plugins validate . --json`: `valid: true`, empty diagnostics.
- `python -m pip wheel --no-deps --wheel-dir dist .`: built
  `dist/muse_memory-1.0.0-py3-none-any.whl` from final source.
- `python C:\Users\cbunt\.codex\skills\active-code-quality\scripts\quick-check.py --checks leftovers --json`:
  PASS. Its four possible-debug-print warnings are intentional CLI JSON
  responses, inspected individually; no suppression added.
- `git diff --check`: passed.

Initial sandbox runs failed because of temporary-directory permissions and
then Windows path-length limits in the alternate temporary root. Tests passed
outside that sandbox. The first non-isolated wheel attempt lacked setuptools;
the isolated build installed the declared build dependency and succeeded.

## Four-layer review and limits

- **Values/data:** credential leak fixed; no hardcoded production credentials
  identified. Test credential samples are explicitly fake fixtures.
- **Logic/flow:** capture gates, queue budgets, pin handling, collision behavior,
  failure counts and malformed MCP requests checked and corrected.
- **Structure:** reachable CLI/MCP/hook call paths reviewed; storage races tested
  with subprocesses. Heuristic extraction, dedupe and trimming remain lossy by
  design and cannot establish the semantic truth of captured text.
- **Architecture/dependencies:** stdlib-only runtime, no runtime dependencies
  declared. No vulnerability-database scan of Python, Muse or build tooling
  was performed; no claim is made that those dependencies are vulnerability-free.

Not verified: an installed interactive Muse session, marketplace installation,
installer execution, Linux/macOS runtime, other Python versions, power-loss
durability, hostile symlinks/filesystem permissions, exhaustive malformed-input
fuzzing, or maximum-size hook timing. Local stores are plaintext and rely on
filesystem access controls. Validators establish package validity, not runtime
acceptance. No installation, publishing, or shared-memory edits were performed.

## Follow-up round: capture precision and file sprawl

Second review round after commit `91d1a899`. Trigger: surveyed real stores held
95–312 topic files, 72–85% machine artifacts (`{"filePath":…}` payloads, patch
bodies, line-numbered dumps, mojibake), and no store had an `archive/`
directory — the file cap only ran at SessionEnd, which is rarely reached.

| Evidence / location | Defect and resulting fix |
|---|---|
| `extractor.py` `FRAGMENT_MARKERS`; `hooks/_common.py` `tool_text` | Tool input JSON and session-log text were mined as prose. Capture now reads tool outputs only (depth/length-bounded string leaves) and rejects JSON payloads, escaped text, line-numbered dumps, mojibake, and session-log paths. |
| `hooks/post_tool_use_failure.py` fallback | Raw error notes bypassed the fragment guard. The fallback now files only prose-shaped errors. |
| `consolidate.py` `retire_fragments`; `hooks/session_start.py`, `hooks/stop.py`, `lifecycle.py` | Nothing retired pre-existing fragments or enforced the cap outside SessionEnd. SessionStart and Stop now retire fragments and enforce `max_files_per_scope` (pinned and importance-5 topics protected); sweeps log real counts. |
| `hooks/_common.py`, `mcp/server.py` | `sys.stdin.read()` decoded UTF-8 as cp1252 on Windows, persisting mojibake. Both now read `sys.stdin.buffer` and decode UTF-8. |
| `store.py` `archive_topic`, `_save_meta`/`_load_meta` | Mass sweeps re-read/wrote metadata per file (2.8s for 221 files). Batched index refresh plus an mtime/size meta stamp cut that to ~1.0s; SessionStart budget raised to 15s. |

Verification: `python -m unittest discover -s tests` — **183 passed** (16 new
fragment-hygiene and UTF-8 tests). End-to-end on a copy of a real 312-file
store: 312 → 40 topics + 269
archived in 1.5s with recall intact over live and archived topics. Both muse
validators re-run clean; wheel rebuilt as
`dist/muse_memory-1.0.1-py3-none-any.whl`. The fix is forward-only: existing
user stores self-heal at the next SessionStart; no store was modified during
verification (all runs used isolated temp copies).

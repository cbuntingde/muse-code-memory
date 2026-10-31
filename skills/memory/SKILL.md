---
name: memory
description: Use the automatic cross-session memory system — recall past decisions and conventions before acting, save durable facts with memory_save, never store secrets or ephemeral task details.
---

# Muse Memory

This project uses automatic cross-session memory. It saves lasting facts on its own during the session and finds them when you ask. Cooperate with it:

## Recall first

Before answering questions about past work, conventions, decisions, or fixes — or when starting a task in unfamiliar code — recall memory:

- Prefer the `memory_recall` MCP tool with a short query ("test conventions", "deploy process", "why X was chosen"). Use `memory_list` to enumerate topics before diving into files.
- Before re-scanning the environment (toolchain versions, build/test commands), recall first ("build command", "test setup", "toolchain") — a filed memory beats another scan.
- Every recall hit and list row carries a read pointer like `personal_project/rule-conventions.md`. Open the full topic with the native `read_memory` tool using exactly that scope and path (`scope="personal_project"`, `path="rule-conventions.md"`) - the pointer is relative to the scope root, which is where the native tool looks. Plugin scopes map to native ones as `global` -> `personal`, `project-personal` -> `personal_project`, `project-shared` -> `project`.
- Time questions filter by filing date automatically: "what did we complete yesterday", "last Monday's milestone", "last month" (explicit `since`/`until` YYYY-MM-DD when precision matters).
- Auto-recalled context may already appear in your prompt; use it, and read the full topic file when a snippet matters.
- The project profile, recent commands, and key files are added to every prompt automatically — no search needed for those.
- Prompt-time recall is read-only: it queues a use signal that Stop/SessionEnd apply to access counts, which keeps useful topics alive during cleanup.

## Save deliberately

Hooks capture obvious decisions and fixes automatically, but save explicitly when the user states something durable:

- Use `memory_save` with one clear statement, a topic, scope, and importance 1-5.
- Save: decisions and why, standing rules ("always/never"), preferences, conventions, root causes and workarounds, durable project facts.
- After discovering how this project builds or what the toolchain is (versions, commands, manifests), save it in one statement so the next session recalls it instead of re-scanning.
- Never save: secrets, tokens, credentials, personal data, or ephemeral task state ("currently editing file X"). Pasted keys are dropped at capture time and remaining secret values are auto-redacted before filing.
- Scope guide: `global` for cross-project user preferences; `project-personal` (default) for this project's working agreements; `project-shared` only for facts the whole team should share (it commits to the repo). Credential-like or high-entropy content is refused outright for `project-shared`, not just redacted.

## Maintenance is automatic

Maintenance is automatic: SessionStart is read-only (digest only). Stop saves the confident captures, enforces the file cap, and records usage; less certain captures stay as proposals, expire after 7 days, and can be filed with /approve. SessionEnd runs a full cleanup pass (duplicates removed, stale items archived, oversized files trimmed). Do not hand-edit `.rag-index.db`, `.memory-meta.json`, `.pending.jsonl`, `.access.jsonl`, or `.snapshot-*` files. Edit topic Markdown files directly when the user asks to correct a memory, then let the next hook pass rebuild the index.

## Commands

- `/remember <statement>` — save a durable memory now.
- `/recall <query>` — search memory.
- `/memories [filters]` — list stored memories in a grouped table.
- `/forget <file>` — trash a memory topic (restorable; `--purge` deletes permanently).
- `/archive <file>` — retire a memory topic (still searchable on request).
- `/consolidate` — file pending captures and run a cleanup pass now.
- `/approve` — file all held capture proposals now.
- `/restore <file>` — restore a trashed memory topic.

---
name: memory
description: Use the automatic cross-session memory system — recall past decisions and conventions before acting, save durable facts with memory_save, never store secrets or ephemeral task details.
---

# Muse Memory

This project uses automatic cross-session memory. It captures durable facts on its own through lifecycle hooks and recalls them through RAG. Cooperate with it:

## Recall first

Before answering questions about past work, conventions, decisions, or fixes — or when starting a task in unfamiliar code — recall memory:

- Prefer the `memory_recall` MCP tool with a short query ("test conventions", "deploy process", "why X was chosen"). Use `memory_list` to enumerate topics before diving into files.
- Time questions filter by filing date automatically: "what did we complete yesterday", "last Monday's milestone", "last month" (explicit `since`/`until` YYYY-MM-DD when precision matters).
- Auto-recalled context may already appear in your prompt; use it, and read the full topic file when a snippet matters.
- Prompt-time recall is read-only: it queues a use signal that Stop/SessionEnd apply to access counts, which keeps useful topics alive during cleanup.

## Save deliberately

Hooks capture obvious decisions and fixes automatically, but save explicitly when the user states something durable:

- Use `memory_save` with one clear statement, a topic, scope, and importance 1-5.
- Save: decisions and why, standing rules ("always/never"), preferences, conventions, root causes and workarounds, durable project facts.
- Never save: secrets, tokens, credentials, personal data, or ephemeral task state ("currently editing file X"). Pasted keys are dropped at capture time and remaining secret values are auto-redacted before filing.
- Scope guide: `global` for cross-project user preferences; `project-personal` (default) for this project's working agreements; `project-shared` only for facts the whole team should share (it commits to the repo). Credential-like or high-entropy content is refused outright for `project-shared`, not just redacted.

## Maintenance is automatic

Maintenance is automatic: SessionStart is read-only (digest only). Stop files high-confidence captures, enforces the file cap, and applies queued use signals; low-confidence captures stay as proposals, expire after 7 days, and can be filed with /approve. SessionEnd adds fragment retirement, dedupe, merge, expiry, and trimming. Do not hand-edit `.rag-index.db`, `.memory-meta.json`, `.pending.jsonl`, `.access.jsonl`, or `.snapshot-*` files. Edit topic Markdown files directly when the user asks to correct a memory, then let the next hook pass rebuild the index.

## Commands

- `/remember <statement>` — save a durable memory now.
- `/recall <query>` — search memory with RAG.
- `/memories [filters]` — list stored memories in a grouped table.
- `/forget <file>` — trash a memory topic (restorable; `--purge` deletes permanently).
- `/archive <file>` — retire a memory topic (still searchable on request).
- `/consolidate` — file pending captures and run a cleanup pass now.
- `/approve` — file all held capture proposals now.
- `/restore <file>` — restore a trashed memory topic.

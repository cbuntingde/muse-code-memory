---
name: memory
description: Use the automatic cross-session memory system — recall past decisions and conventions before acting, save durable facts with memory_save, never store secrets or ephemeral task details.
---

# Muse Memory

This project uses automatic cross-session memory. It captures durable facts on its own through lifecycle hooks and recalls them through RAG. Cooperate with it:

## Recall first

Before answering questions about past work, conventions, decisions, or fixes — or when starting a task in unfamiliar code — recall memory:

- Prefer the `memory_recall` MCP tool with a short query ("test conventions", "deploy process", "why X was chosen"). Use `memory_list` to enumerate topics before diving into files.
- Auto-recalled context may already appear in your prompt; use it, and read the full topic file when a snippet matters.
- Recall touches files to mark them useful; that signal keeps them alive during cleanup.

## Save deliberately

Hooks capture obvious decisions and fixes automatically, but save explicitly when the user states something durable:

- Use `memory_save` with one clear statement, a topic, scope, and importance 1-5.
- Save: decisions and why, standing rules ("always/never"), preferences, conventions, root causes and workarounds, durable project facts.
- Never save: secrets, tokens, credentials, personal data, or ephemeral task state ("currently editing file X"). Pasted keys are dropped at capture time and remaining secret values are auto-redacted before filing.
- Scope guide: `global` for cross-project user preferences; `project-personal` (default) for this project's working agreements; `project-shared` only for facts the whole team should share (it commits to the repo).

## Maintenance is automatic

SessionEnd runs dedupe, merge, expiry, trimming, and cap enforcement. Do not hand-edit `.rag-index.json`, `.memory-meta.json`, `.pending.jsonl`, or `.snapshot-*` files. Edit topic Markdown files directly when the user asks to correct a memory, then let the next hook pass rebuild the index.

## Commands

- `/remember <statement>` — save a durable memory now.
- `/recall <query>` — search memory with RAG.
- `/memories [filters]` — list stored memories in a grouped table.
- `/forget <file>` — permanently delete a memory topic.
- `/archive <file>` — retire a memory topic (still searchable on request).
- `/consolidate` — file pending captures and run a cleanup pass now.

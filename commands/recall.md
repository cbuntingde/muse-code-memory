---
description: Search cross-session memory
argument-hint: <query>
---

Search memory for: $ARGUMENTS

Use the memory_recall tool (top_k 5). Date phrases in the query (yesterday, last Monday, last month, first Monday of August) filter by filing date automatically. Summarize the hits that actually answer the query with their scope/file references; say plainly when memory has nothing relevant instead of guessing. Each hit carries a read pointer (for example `personal_project/rule-conventions.md`) - pass that scope and path to the native `read_memory` tool to open the full topic file.

---
description: List stored project memories in a professional grouped table
argument-hint: [--scope global|project-personal|project-shared] [--tag TAG] [--min-importance 1-5] [--format table|markdown]
---

List stored memories: $ARGUMENTS

Use the memory_list tool (map scope/tag/min_importance/limit from the arguments), or run `python3 -m muse_memory list` from the plugin directory (or the equivalent `muse-memory` CLI) when outside agent context, and present the resulting table with scope/file references. The Read column holds the native `read_memory` scope and path for each topic (for example `personal_project/rule-conventions.md`); use it to open a full topic. Say plainly when no memories match instead of guessing. Do not narrate the commands while they run.

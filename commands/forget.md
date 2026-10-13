---
description: Permanently delete a stored memory topic
argument-hint: <file> [--scope global|project-personal|project-shared]
---

Delete this memory topic: $ARGUMENTS

Run `python3 -m muse_memory forget` from the plugin directory (or the equivalent `muse-memory` CLI) with the file reference (scope/file or file plus --scope, default project-personal), and confirm the deletion in one line. Deletion is permanent — archived recovery is not possible, so repeat the exact file back. Do not narrate the commands while they run.

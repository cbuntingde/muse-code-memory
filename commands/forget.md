---
description: Trash a stored memory topic (restorable with /restore)
argument-hint: <file> [--scope global|project-personal|project-shared] [--purge]
---

Forget this memory topic: $ARGUMENTS

Run `python3 -m muse_memory forget` from the plugin directory (or the equivalent `muse-memory` CLI) with the file reference (scope/file or file plus --scope, default project-personal), and confirm the trashed file in one line. The topic moves to the scope's trash and can be brought back with /restore; only `--purge` deletes permanently. Do not narrate the commands while they run.

---
description: File pending captures and run memory maintenance now
argument-hint: (no arguments)
---

Run one memory maintenance pass now: execute `python3 -m muse_memory consolidate` then `python3 -m muse_memory cleanup` from the plugin directory (or the equivalent `muse-memory` CLI), and report the filed counts plus the cleanup report (deduped bullets, merged topics, expired/archived, trimmed, retired-for-cap, and whether comparisons were capped for time) in a compact list. Do not narrate the commands while they run.

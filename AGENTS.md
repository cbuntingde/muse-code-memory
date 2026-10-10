# muse-memory repo conventions

- Standard library only: no third-party imports anywhere (hooks run with a cleared environment).
- One hook script per lifecycle event in `hooks/`; shared code goes in `muse_memory/` or `hooks/_common.py` (never share argv script paths between hook ids).
- Hooks must always exit 0 and stay fast (<5s typical); wrap new hook work in try/except via `_common.run`. Log real hook errors with `_common.log_diagnostic` (rotated log) instead of swallowing them; only routine empty-input paths stay silent.
- Secrets never persist: new capture or filing paths must scrub with `extractor.redact_secrets` (drop pasted keys, redact values, derive topics from redacted text). `consolidate.file_candidate` is the last-chance scrub — keep it.
- Storage contract: topic body bytes roundtrip exactly through `read_record`; frontmatter is documentary only, machine truth lives in `.memory-meta.json`. Raw rewrites (dedupe/trim) must preserve the header block; never add body sections that would change `content`.
- One implementation serves CLI + MCP (e.g. `muse_memory/listing.py`); never duplicate filtering or rendering between surfaces.
- Keep surfaces in sync when adding a command or tool: CLI parser + `mcp/server.py` + `commands/*.md` + `.muse-plugin/plugin.json` + `skills/memory/SKILL.md` + `README.md` (including test counts). If the plugin name or root moves, also update `.agents/plugins/marketplace.json`. Invalid config values fall back to defaults — see `config._CONSTRAINTS`.
- Tests: `python -m unittest discover -s tests` — keep contract tests (hook stdin/stdout shape, MCP roundtrip) passing.
- Validate before handoff: `muse skills validate ./skills/memory --json` then `muse plugins validate . --json`; both must be `valid: true` with empty diagnostics.
- Never write to the user's real `~/.config/muse` or `~/.local/share/muse` from tests; use the `IsolatedCase` XDG temp dirs.

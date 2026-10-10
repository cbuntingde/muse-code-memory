# Hook & Plugin Research Notes

Verified October 2026 against official docs, the installed Muse Code 1.4.3 binary,
and community plugin sources. This corrects and extends the brief the user supplied.

## Sources

- Official: [Extending and automating](https://dev.meta.ai/docs/muse-code/extending),
  [Configuration and context](https://dev.meta.ai/docs/muse-code/configuration)
- Installed binary contract: `~/.local/share/muse/plugins/cache/builtin/muse-core/create-plugin/*/references/native-plugin-contract.md`
  and `capability-examples.json` (authoritative for `plugin.json` shape)
- Community: `agentic-control-plane/muse-code-acp-plugin` README (hook wire contract),
  `currentbits/solenta` CAPTURE.md (paths, `settings.json` rules)

## Hook events: supplied brief vs verified reality

Supplied brief was largely correct with these corrections:

| # | Supplied claim | Verdict |
|---|---|---|
| 1 | `SessionStart`, `SessionEnd` exist; `SessionEnd` observational, cannot block/inject | Correct, matches official docs |
| 2 | `UserPromptSubmit`, `Stop` exist | Correct |
| 3 | `PreToolUse`, `PostToolUse` exist | Correct |
| 4 | Failure event named `ToolUseFailure` | **Wrong name**: official event is `PostToolUseFailure` |
| 5 | `PermissionRequest`, `Notification` for approvals | Correct that both exist. Official wording: `Notification` fires when the agent needs approval so a hook can alert externally |
| 6 | Hooks configured in `.muse/hooks.json`, JSON events via stdin | Correct for project scope. Three sources exist: project `<root>/.muse/hooks.json`, user `hooks` block in `settings.json`, managed file via `managed_hooks_path` |
| 7 | Six listed events are all | **Incomplete**: also `PreLLMCall`, `PostLLMCall`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop` (15 total) |

Full official event list: `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `PreLLMCall`, `PostLLMCall`,
`PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Notification`, `Stop`,
`SessionEnd`.

## Hook wire contract (verified by ACP project against Muse's own runner)

- stdin: snake_case JSON (`hook_event_name`, `session_id`, `cwd`, `tool_name`, `tool_input`, `tool_result`, `permission_mode`, `prompt`, ...)
- stdout: JSON with camelCase `hookSpecificOutput` (`additionalContext` injects model-visible context; `permissionDecision` allow/deny for gating events)
- exit 2 + stderr: fallback block channel (gating events only)
- Hooks run **outside** the sandbox with a **cleared environment** — hence this project's stdlib-only, zero-dependency hooks
- No `muse hooks` command family exists; malformed project/managed hook files warn at startup, malformed user settings fail validation
- Docs publish **no** project `hooks.json` schema example; plugin manifests use structured `argv` bound to one `event` each. The `examples/` files here are Claude-compatible best effort; the plugin install is the supported path

## Plugins: supplied brief vs verified reality

| # | Supplied claim | Verdict |
|---|---|---|
| 1 | Plugins gated behind `MUSE_EXPERIMENTAL_PLUGINS=1` | **Outdated**: true for 0.2.x, but Muse 1.4.3 exposes `muse plugins ...` with no flag (`install`, `approve`, `enable`, `validate`, `hook test`, marketplaces all present in `--help`) |
| 2 | Manifest at `.muse-plugin/plugin.json` | Correct, confirmed by native-plugin-contract.md |
| 3 | Plugins bundle tools, skills, hooks | Partially: supported families are exactly `skills`, `commands`, `hooks`, `mcpServers`, `reminders`. Direct `tools` is **rejected** — custom tools go behind an `mcpServers` stdio entry |
| 4 | Hooks map lifecycle events to argv command strings | Correct in spirit: structured argv arrays (`["python", "hooks/x.py"]`), one event per hook id, each hook source path unique |
| 5 | Flag only needed for management; installed hooks fire normally | Correct for 0.2.x; moot now that no flag is needed |

## Memory paths (observed on this machine)

- Config: `~/.config/muse/settings.json` (requires `"schema_version": 1`)
- Data: `~/.local/share/muse/`
- Native personal-project memory: `<data>/memory/projects/<slug>/MEMORY.md` with `---\ntype: project\n---` frontmatter, e.g. `C--ai-development-vscode-495a58d059b674a3`
- Native slug algorithm is undocumented; this project **adopts** an existing native slug dir when its name starts with the sanitized workspace stem, else creates `<stem>-<sha256[:12]>`
- Shared project memory: `<repo>/.agents/memory/` (loaded even in untrusted workspaces — treat as prompt-injection surface on foreign checkouts)
- Global scope `<data>/memory/global/` is this project's addition (no native equivalent observed); topic files + `MEMORY.md` index follow the same convention
- Context injection cap: index of `MEMORY.md` + up to 48 topic files, paths only, agent reads on demand + observer recall. This project's default cap is 40 files/scope to stay under it

# Design: `clink` Safe-by-Default CLI Execution

> Status: draft
> Created: 2026-04-22
> Related: upstream issue [BeehiveInnovations/pal-mcp-server#417](https://github.com/BeehiveInnovations/pal-mcp-server/issues/417), upstream PR [#418](https://github.com/BeehiveInnovations/pal-mcp-server/pull/418)

## Problem

The `clink` tool (`tools/clink.py`) is an MCP bridge that forwards prompts from a remote MCP client to a locally running AI CLI (Claude / Gemini / Codex). Each CLI is configured, by default, with a flag that authorizes unrestricted local filesystem edits:

| CLI    | Config file                     | Write-enabling flag                          |
| ------ | ------------------------------- | -------------------------------------------- |
| Claude | `conf/cli_clients/claude.json`  | `--permission-mode acceptEdits`              |
| Gemini | `conf/cli_clients/gemini.json`  | `--yolo`                                     |
| Codex  | `conf/cli_clients/codex.json`   | `--dangerously-bypass-approvals-and-sandbox` |

A remote MCP client's prompt can therefore instruct the local CLI to create, modify, or overwrite arbitrary files on the host, with no trust boundary between the untrusted input and the privileged CLI process. This violates least-privilege and enables arbitrary file write via a fully remote input channel.

## Goals

- **Safe by default.** The tool MUST NOT grant the CLI filesystem-write capability unless the caller explicitly opts in.
- **Generic across all configured CLIs.** The mechanism MUST cover Claude, Gemini, and Codex — not just Claude (which is the gap in upstream PR #418).
- **Config-driven, not hard-coded.** New CLIs should be protectable by editing their JSON config, not by editing agent code.
- **Opt-in path allow-listing** for callers that need edits but want them scoped (Claude supports this natively via `--allowedTools`).
- **Defense in depth.** Even when safe-mode strips dangerous flags, the forwarded prompt should explicitly tell the CLI not to perform filesystem modifications (belt-and-braces against CLI defaults we don't control).

## Non-goals

- Sandboxing the CLI at the OS level (containers, seccomp, chroot). Out of scope for this fix.
- A full content-injection firewall on the prompt. The hardening here is limited to trust-boundary labelling + policy hints.
- Replacing the existing Claude-CLI-specific `--append-system-prompt` injection path.

## Design

### Configuration model

Split the CLI client's `additional_args` into three disjoint buckets:

| Bucket             | Semantics                                                  |
| ------------------ | ---------------------------------------------------------- |
| `additional_args`  | Always applied. Must not contain write-enabling flags.     |
| `safe_args`        | Applied when `allow_edits=false` (default).                |
| `edit_args`        | Applied when `allow_edits=true`.                           |

Both `safe_args` and `edit_args` are optional and default to `[]` (backwards-compatible with existing configs that need neither). For CLIs whose default behavior (no flag) is already read-only, `safe_args` is empty and only `edit_args` is populated.

Concrete post-migration configs:

```jsonc
// claude.json
"additional_args": ["--model", "sonnet"],
"safe_args":       ["--permission-mode", "default"],
"edit_args":       ["--permission-mode", "acceptEdits"]

// gemini.json
"additional_args": [],
"edit_args":       ["--yolo"]

// codex.json
"additional_args": ["--json", "--enable", "web_search_request"],
"edit_args":       ["--dangerously-bypass-approvals-and-sandbox"]
```

The dangerous flag no longer lives in `additional_args`, so "forget to sanitize" bugs are structurally impossible — safe mode omits it by construction.

### Request model

`CLinkRequest` (in `tools/clink.py`) gains two optional fields:

- `allow_edits: bool = False` — explicit opt-in for filesystem edits.
- `editable_paths: list[str] = []` — optional absolute-path allow-list, only valid with `allow_edits=true`. Path values are enforced to be absolute in `execute()`.

Schema fields are added to `get_input_schema()` so MCP clients can see them.

### Execution plumbing

1. `BaseCLIAgent.run()` accepts `allow_edits: bool = False` and `editable_paths: Sequence[str] = ()` and forwards them to `_build_command`.
2. `BaseCLIAgent._build_command()` constructs:
   ```
   executable + internal_args + config_args + (edit_args if allow_edits else safe_args)
     + <agent-specific path-restriction args> + role.role_args
   ```
3. A new hook `_build_path_restriction_args(editable_paths, allow_edits)` on `BaseCLIAgent` defaults to `[]`. Only `ClaudeAgent` overrides it, emitting `--allowedTools Edit(path)` and `--allowedTools Write(path)` per path.

### Per-agent path-restriction support

`editable_paths` is Claude-specific today (Claude CLI has first-class `--allowedTools Edit/Write` semantics). For Gemini and Codex, there is no direct equivalent. Rather than silently ignore, `tools/clink.py` validates: if `editable_paths` is non-empty and the selected agent doesn't support them, the tool returns a clear error. A small registry on the agent class (`supports_path_restrictions: bool`) makes this introspectable without `isinstance`.

### Prompt hardening

In `_prepare_prompt_for_role`:

- Relabel the user-content section from `=== USER REQUEST ===` to `=== UNTRUSTED USER REQUEST ===`. This gives the downstream LLM an explicit trust-boundary signal.
- When `allow_edits=false`, append an `=== EXECUTION POLICY ===` section instructing the CLI not to perform filesystem modifications or apply edits.

This is defense-in-depth; the real guarantee comes from the flag removal, not the prompt wording.

### Backwards compatibility

- Existing callers that pass neither `allow_edits` nor `editable_paths` get safe behavior — a strict reduction in privilege from today.
- Existing CLI configs with dangerous flags inside `additional_args` continue to work *functionally*, but the shipped configs are migrated in this PR so they no longer contain the dangerous flag by default.
- The `safe_args` / `edit_args` fields default to `[]`, so third-party configs (e.g. in `~/.pal/cli_clients/`) need not be updated unless they want edit-gating.

## Architectural trade-offs

- **Two arg buckets vs. single `edit_args` with override logic.** Single-bucket avoids new config surface, but requires runtime sanitization of `additional_args` (stripping/rewriting `--permission-mode`). That's the upstream PR's approach and is inherently per-CLI fragile. Two buckets make the data model match the policy: one list per mode, no sanitization pass needed.
- **Config-driven vs. agent-overridden `_build_command`.** Upstream PR pushes sanitization into `ClaudeAgent._build_command`. That leaves Gemini/Codex uncovered. Config-driven covers all current CLIs and any future one that ships with a write-enabling flag, so long as the config migration puts the flag into `edit_args`.
- **`editable_paths` on non-Claude agents.** Erroring early is safer than silently granting unrestricted access because the allow-list wasn't honored.

## Threat model (post-fix)

- Untrusted remote prompt → `clink` with no `allow_edits`: **CLI subprocess runs without write-enabling flag and is told (via prompt) not to modify files.** No filesystem modification path remains except via CLI bugs / built-in defaults outside our control.
- Untrusted remote prompt → `clink` with `allow_edits=true` but no `editable_paths`: caller has explicitly accepted the risk of arbitrary writes. Behavior matches today's default.
- Trusted local caller wants scoped edits (Claude only): `allow_edits=true` + `editable_paths=[abspath]` limits writes to listed paths via Claude's native `--allowedTools` enforcement.

## Open questions

- Does Claude's default `--permission-mode default` behavior fully block writes, or does it prompt interactively? PR author assumed it's safe; we adopt that assumption — if wrong, Claude may block indefinitely on an interactive prompt rather than write, which is fail-safe but worth a follow-up.
- Should `editable_paths` also be supported on Gemini/Codex eventually? Out of scope for this fix; each CLI would need its own mechanism.

# Clink Tool - CLI-to-CLI Bridge

`clink` sends a request to an installed Codex, Claude Code, or Gemini CLI and returns its answer to your MCP client. You can hold an ongoing conversation without configuring a PAL model provider. PAL forwards bounded conversation history to a fresh CLI process on each call; the selected CLI handles authentication and inference.

Use this path for local, single-user workflows. PAL does not install CLIs, sign you in, enforce subscription billing, or fall back to an API provider when a CLI request fails.

## Setup Requirements

1. Install your chosen vendor CLI and authenticate it outside PAL, using the same OS account that launches PAL. Follow the vendor links below.
2. Ensure the MCP server process can find the executable on its `PATH`, or configure an explicit command path in the client manifest. A terminal and a desktop MCP client may have different environments.
3. Follow [Getting Started: CLI conversations](../getting-started.md#cli-conversations-without-pal-provider-keys). Leave PAL model-provider keys and endpoints unconfigured for provider-free startup. No mode switch is needed.
4. Restart PAL after changing configuration. Ask `listmodels` for the startup provider diagnostics, available independent tools, and configured CLI executable status. Login is **unverified**, even when an executable is found; invocation checks the executable again.

Provider-free mode exposes the enabled subset of `clink`, `version`, `listmodels`, `planner`, `tracer`, `docgen`, `challenge`, `apilookup`, and `jules`. Jules needs its own service credentials. PAL starts even if no CLI is installed or clink is disabled. API-backed tools such as `chat`, `consensus`, and `codereview` require a configured PAL model provider; they are omitted from provider-free listings and rejected if called directly. Enabling a tool in `DISABLED_TOOLS` settings does not satisfy its provider requirements.

### Authentication and version assumptions

Primary vendor guidance was checked on **2026-10-13**. It describes current vendor behavior, not a tested minimum CLI version or a guarantee that every installed release accepts PAL's shipped arguments.

| CLI | Local authentication before using clink | Noninteractive use |
| --- | --- | --- |
| Codex | Run `codex login` and follow the browser flow for ChatGPT sign-in; inspect the selected method with `codex login status`. API-key authentication is also supported. | `codex exec` reuses saved CLI authentication by default. See [authentication](https://learn.chatgpt.com/docs/auth) and [noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode). |
| Claude Code | Run `claude` and complete sign-in for the intended account. | PAL uses `--print`. Review [authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence): in print mode, an inherited `ANTHROPIC_API_KEY` takes precedence over subscription login, subject to higher-priority routing/credentials. See [programmatic use](https://code.claude.com/docs/en/headless). |
| Gemini | Run `gemini` and choose the intended authentication method, such as Google sign-in. Some account types require a Cloud project. | Headless mode can reuse cached authentication; without it, configure an API-key or Vertex AI method. See [authentication](https://geminicli.com/docs/get-started/authentication/#running-in-headless-mode). |

PAL inherits only shared runtime variables and the selected CLI's authentication allowlist, then applies operator overrides. The CLI can also read its own saved configuration and credentials. Review the [child environment](#child-environment-and-working-directory) and the vendor's active authentication method before relying on account-specific usage. A successful answer establishes execution, **not** which subscription or billing account paid for it. PAL provider-free mode does not mean the child CLI is offline or free.

When validating an installation, record the chosen CLI's `--version`, intended authentication method, and first-request/follow-up outcomes. PAL discovery runs no version, login, or inference probes. No live version compatibility claim follows from this documentation check.

## First Request and Follow-up

Ask your MCP client to call the **clink tool** with these arguments. These JSON objects are tool arguments, not shell commands. Replace `codex` with `claude` or `gemini` if that is your installed CLI.

```json
{
  "cli_name": "codex",
  "role": "default",
  "prompt": "Remember the project codeword ORCHID-42 and suggest one short release checklist."
}
```

After a successful, recorded reply with room for another exchange, copy the returned `continuation_offer.continuation_id` into the next request:

```json
{
  "continuation_id": "<continuation_offer.continuation_id from the previous reply>",
  "prompt": "What was the project codeword, and which checklist item should we do first?"
}
```

The follow-up inherits the last successfully recorded CLI and role. Explicit `cli_name` or `role` values override them. New conversations require `cli_name` when multiple clients are configured, as in the shipped configuration. If exactly one is configured, it can be selected implicitly. There is no implicit Gemini choice among multiple clients. Invalid selections fail; changing CLI while retaining a role the new CLI lacks also fails until you supply a supported role.

`/pal:continue` in provider-free mode instructs your client to reuse the previous available conversation tool and latest PAL continuation ID. A CLI conversation stays on clink. These IDs belong to PAL; native vendor session IDs are diagnostic metadata, not resume handles used by clink.

## Tool Parameters

| Parameter | Behavior |
| --- | --- |
| `prompt` | Required current question or task. |
| `cli_name` | Configured client name, such as `codex`, `claude`, or `gemini`; selection rules are above. CLI names are not PAL API model names. |
| `role` | Shipped roles: `default`, `planner`, `codereviewer`. Defaults to `default` on a new conversation, or the saved role on a continuation. |
| `absolute_file_paths` | Optional absolute file paths. PAL forwards references; the CLI opens files using its own capabilities and permissions. |
| `images` | Accepted image-path references. This does not guarantee image transport or multimodal support in the selected runner. |
| `continuation_id` | Existing PAL conversation UUID. Expired or invalid IDs fail before CLI execution. |
| `allow_edits` | Defaults to `false` on **every request**, including follow-ups. Set to `true` to apply the configured edit arguments. |
| `editable_paths` | Optional absolute-path scope requiring `allow_edits=true`. Supported only by Claude through `--allowedTools Edit(path)/Write(path)`; Codex and Gemini reject it. |

### Edit permissions

By default, clink omits the shipped write-enabling arguments and adds an execution-policy prompt asking the CLI not to modify files. This is not an OS sandbox or a guarantee against writes: native CLI settings, permissions, and tools still apply. Codex's shipped edit opt-in bypasses its approvals and sandbox; use it only when you intend that access.

For a scoped Claude edit, replace the example path with an existing absolute path:

```json
{
  "cli_name": "claude",
  "prompt": "Fix the typo in this file.",
  "absolute_file_paths": ["/absolute/path/to/project/README.md"],
  "allow_edits": true,
  "editable_paths": ["/absolute/path/to/project/README.md"]
}
```

If a later request also needs edits, supply `allow_edits` and any `editable_paths` again. PAL never inherits edit permission from the original call. Omitting them returns to the safe argument bucket; authorizing edits without a path list does not retain an earlier scope.

## Conversation Limits

- **Process-local lifetime:** default three-hour sliding TTL, refreshed only after a successfully recorded CLI exchange. Restarting PAL loses all continuation IDs. Optional audit transcripts do not restore resumable history.
- **Storage capacity:** default 50 messages; user and assistant messages count separately, so a new clink-only thread holds up to 25 exchanges. Each call requires two free slots. A continuation offer requires another two slots after recording; `remaining_turns` counts messages and the offer's note states remaining exchanges.
- **Bounded history:** default **16,384 estimated tokens** per client. PAL retains the newest contiguous suffix of complete exchanges in chronological order, with notices for omitted older or incomplete legacy messages. It never splits a message or retains half an exchange.
- **Accounting:** the character-based estimate covers history text, labels, path references, notices, and delimiters. It excludes the current request, CLI system prompts, tool results, and the CLI's own context use. It is not a model context-window guarantee.
- **Attachments and output:** historical attachments stay path references; PAL does not reread their contents for history. If PAL reduces a long reply for display, the next clink call sees that displayed text. Stored raw replies and optional audit transcripts retain the original content.

Response `metadata.history` reports `estimated_tokens`, `token_budget`, `retained_messages`, `omitted_messages`, `incomplete_messages`, and `incomplete_legacy_omitted`. If the newest complete exchange cannot fit, clink fails before launching the CLI. Start a shorter new conversation, or increase the budget for future server sessions. See [history budget configuration](../configuration.md#cli-conversation-history-budgets) for strict positive-integer settings, manifest precedence, and restart effects.

You can explicitly continue an API-origin PAL conversation through clink, supplying a CLI selection when needed. It uses the same bounded history rules. Returning to an API-backed tool requires a configured PAL provider; there is no automatic cross-backend retry.

## Diagnostics and Failures

| Result | What to do / history effect |
| --- | --- |
| `listmodels` reports provider-free mode | Expected without registered PAL model providers. It distinguishes absent/incomplete settings and reports startup CLI executable observations; login remains unverified. Complete inputs alone do not prove registration or valid authentication. |
| Selected executable missing | Check the PAL process's `PATH` or configured command. A different installed CLI is never substituted. No history is written. |
| Authentication, quota, timeout, nonzero exit, or terminal CLI error | Inspect the returned diagnostic and the selected vendor's setup. The failed request creates/appends nothing, does not refresh TTL, and carries no continuation offer. There is no API fallback. |
| `ConversationBusyError`, `retryable=true` | Another holder or queued request owns this ID. Clink rejects immediately; retry the same request/ID after it finishes. Different conversation IDs can proceed independently. |
| Missing/expired ID, insufficient message slots, or oversized history | Start a new conversation or address the indicated limit. The error occurs before CLI launch. |
| Reply with `metadata.history_saved=false` | Generation succeeded but recording failed, conflicted, or expired. Keep the answer; no continuation is offered for it. PAL does not rerun the CLI or overwrite changed history. |
| Optional transcript write warning | The memory commit and normal continuation eligibility remain valid; audit recording is best effort. |

A non-terminal subtool warning alongside a successful final answer can remain successful. An explicit terminal failure is an error even if partial answer text is readable.

For Gemini `UNSUPPORTED_CLIENT`, check the vendor's [installation/update instructions](https://geminicli.com/docs/get-started/installation/) and [troubleshooting guide](https://geminicli.com/docs/resources/troubleshooting/). The marker alone does not establish an account-policy cause.

## Configuration

The shipped manifests live in [`conf/cli_clients/`](../../conf/cli_clients/); role prompts live in [`systemprompts/clink/`](../../systemprompts/clink/). Copy a **complete** manifest to `~/.pal/cli_clients/` to customize it. Later manifests replace a client's full definition, rather than patching individual fields. See [configuration precedence](../configuration.md#cli-conversation-history-budgets).

Arguments are assembled from internal defaults, `additional_args` (always), one of `safe_args` / `edit_args`, runner-generated arguments, and `role_args`. Keep write-enabling flags out of always-applied arguments.

These are the current base commands for the shipped presets. PAL sends the prompt over stdin; Claude also receives a request-local `--append-system-prompt` argument, and scoped edits add `--allowedTools` arguments.

| CLI | Default command (`allow_edits=false`) | Change when `allow_edits=true` |
| --- | --- | --- |
| Gemini | `gemini -o json` | Append `--yolo`. No telemetry flag is shipped. |
| Claude | `claude --print --output-format json --model sonnet --permission-mode default` | Replace the safe permission bucket with `--permission-mode acceptEdits`. |
| Codex | `codex exec --json --enable web_search_request --skip-git-repo-check` | Add `--dangerously-bypass-approvals-and-sandbox` before the scratch-directory flag. |

Codex receives `--skip-git-repo-check` only for PAL-created scratch directories. Configured working directories retain the CLI's repository policy. The shipped `--enable web_search_request` flag is version-sensitive: the current [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference) does not list that feature name. Check support in your installed release before relying on this preset; the table records PAL's configuration, not universal vendor compatibility. For the other command formats, see [Claude programmatic use](https://code.claude.com/docs/en/headless) and [Gemini headless output](https://geminicli.com/docs/cli/headless/).

### Child environment and working directory

Clink starts each child with shared runtime variables (executable lookup, home/configuration locations, temporary directories, locale, proxies, TLS certificates, and Windows process prerequisites), plus that CLI's declared authentication variables. Unrelated provider keys and other inherited variables are omitted. The exact lists live in [`clink/constants.py`](../../clink/constants.py).

Operator configuration can add inherited names using `env_passthrough`, then supply final values in `env`. Add these fields to a complete CLI manifest, replacing the example directory:

```json
{
  "working_dir": "/absolute/path/to/project",
  "env_passthrough": ["MY_COMPANY_AUTH_TOKEN"],
  "env": {"HTTPS_PROXY": "http://localhost:8080"}
}
```

Explicit values take precedence, including Windows case-insensitive names. Executable lookup uses the child's configured `PATH`. Keep credentials in the launch environment when possible; `env_passthrough` contains names, not values. These are operator settings, not tool parameters.

A configured `working_dir` must exist and is preserved. Without it, each call uses a private temporary directory, removed after success, failure, timeout, or cancellation. Immediate-child termination/reaping precedes scratch cleanup; draining output pipes is bounded. Concurrent calls use different directories. Supply absolute file references, or configure `working_dir` when the CLI needs project-local settings and repository context. Relative configured paths retain the registry's configuration/project resolution rules.

Home/auth locations remain available, so a scratch directory does not prevent access elsewhere. File reads and edits still depend on the selected CLI's native controls; this feature adds no filesystem sandbox or process-tree supervision guarantee.

## Related Guides

- [Getting Started](../getting-started.md#cli-conversations-without-pal-provider-keys) - Local CLI and API setup paths
- [Configuration](../configuration.md#cli-conversation-history-budgets) - History settings and restart effects
- [Planner Tool](planner.md) - Provider-independent planning workflow
- [Chat Tool](chat.md) and [CodeReview Tool](codereview.md) - API-backed workflows
- [Context Revival](../context-revival.md) - PAL conversation threading
- [Logging and transcripts](../logging.md) - Optional audit recording

# Clink Tool - CLI-to-CLI Bridge

`clink` sends a request to an installed Codex, Claude Code, Gemini, or Antigravity (`agy`) CLI and returns its answer to your MCP client. You can hold an ongoing conversation without configuring a PAL model provider. PAL forwards bounded conversation history to a fresh CLI process on each call; the selected CLI handles authentication and inference.

Use this path for local, single-user workflows. PAL does not install CLIs, sign you in, enforce subscription billing, or fall back to an API provider when a CLI request fails.

## Setup Requirements

1. Install your chosen vendor CLI and authenticate it outside PAL, using the same OS account that launches PAL. Follow the vendor links below.
2. Ensure the MCP server process can find the executable on its `PATH`, or configure an explicit command path in the client manifest. A terminal and a desktop MCP client may have different environments. For npm CLIs installed through nvm, see the [nvm PATH setup](#clis-installed-with-nvm).
3. Follow [Getting Started: CLI conversations](../getting-started.md#cli-conversations-without-pal-provider-keys). Leave PAL model-provider keys and endpoints unconfigured for provider-free startup. No mode switch is needed.
4. Restart PAL after changing configuration. Ask `listmodels` for the startup provider diagnostics, available independent tools, and configured CLI executable status. Login is **unverified**, even when an executable is found; invocation checks the executable again.

Provider-free mode exposes the enabled subset of `clink`, `version`, `listmodels`, `planner`, `tracer`, `docgen`, `challenge`, `apilookup`, and `jules`. Jules needs its own service credentials. PAL starts even if no CLI is installed or clink is disabled. API-backed tools such as `chat`, `consensus`, and `codereview` require a configured PAL model provider; they are omitted from provider-free listings and rejected if called directly. Enabling a tool in `DISABLED_TOOLS` settings does not satisfy its provider requirements.

### Authentication and version assumptions

Primary vendor guidance was checked on **2026-10-13**, with agy guidance checked on **2026-10-14**. It describes current vendor behavior, not a tested minimum CLI version or a guarantee that every installed release accepts PAL's shipped arguments.

| CLI | Local authentication before using clink | Noninteractive use |
| --- | --- | --- |
| Codex | Run `codex login` and follow the browser flow for ChatGPT sign-in; inspect the selected method with `codex login status`. API-key authentication is also supported. | `codex exec` reuses saved CLI authentication by default. See [authentication](https://learn.chatgpt.com/docs/auth) and [noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode). |
| Claude Code | Run `claude` and complete sign-in for the intended account. | PAL uses `--print`. Review [authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence): in print mode, an inherited `ANTHROPIC_API_KEY` takes precedence over subscription login, subject to higher-priority routing/credentials. See [programmatic use](https://code.claude.com/docs/en/headless). |
| Gemini | Run `gemini` and choose the intended authentication method, such as Google sign-in. Some account types require a Cloud project. | Headless mode can reuse cached authentication; without it, configure an API-key or Vertex AI method. See [authentication](https://geminicli.com/docs/get-started/authentication/#running-in-headless-mode). |
| Antigravity (`agy`) | Install the native CLI, check `agy --version`, and authenticate interactively with `agy` outside PAL. Existing enterprise authentication stays configured in agy. Add `command(...)` allow-rules to `~/.gemini/antigravity-cli/settings.json` for any shell commands the roles should be able to run (see [headless permissions below](#agy-headless-permissions)). | Reference version: **1.3.5**. Uses the existing login without a mandatory PAL API key; see [setup and limits below](#antigravity-agy) and [official installation/authentication](https://antigravity.google/docs/cli/install). |

PAL inherits only shared runtime variables and the selected CLI's authentication allowlist, then applies operator overrides. The CLI can also read its own saved configuration and credentials. Review the [child environment](#child-environment-and-working-directory) and the vendor's active authentication method before relying on account-specific usage. A successful answer establishes execution, **not** which subscription or billing account paid for it. PAL provider-free mode does not mean the child CLI is offline or free.

When validating an installation, record the chosen CLI's `--version`, intended authentication method, and first-request/follow-up outcomes. PAL discovery runs no version, login, or inference probes. No live version compatibility claim follows from this documentation check.

### Antigravity (agy)

The agy adapter uses the native **1.3.5** CLI, not an older IDE launcher named `agy`. Restart PAL after configuring the executable or its manifest. It supports the shared `default`, `planner`, and `codereviewer` roles and PAL continuation IDs. Existing-login operation is intentional; PAL does not initiate sign-in, copy tokens, change providers, or require `GEMINI_API_KEY` for agy.

For an existing enterprise setup, the child environment preserves operator-supplied `AGY_ADC_AUTH`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_QUOTA_PROJECT`, and `GOOGLE_CLOUD_LOCATION`. PAL sets none of these on your behalf. Operator `env_passthrough` additions and explicit `env` overrides retain the normal precedence. Forwarding a variable does not by itself prove successful authentication or routing; configure those outside PAL using the [enterprise instructions](https://www.antigravity.google/docs/enterprise).

Google documents that wrapping consumer-plan agy in third-party agents or applications is unsupported. Existing-login functionality does not imply upstream endorsement or subscription eligibility. See the [headless-mode FAQ](https://antigravity.google/docs/faq#can-i-use-antigravity-cli-headless-mode-in-third-party-coding-agents-or-automate-account-switching). Recorded live evidence covers the available enterprise account on macOS with 1.3.5. Personal login, successful ADC routing, and missing/expired cached-session cases remain unverified release requirements; other platforms and versions have not been validated. See the [acceptance evidence](../feat/wip/antigravity-clink/capability-evidence.md).

Having gcloud ADC credentials alone does not establish that agy's ADC mode is ready. In the 2026-10-15 check, test-only `AGY_ADC_AUTH=1` reached agy but failed with a missing plan-model error. Configure and verify the intended native model/project/location outside PAL before treating ADC as working; PAL does not select them or change saved settings. See the [failed attempt and remaining checks](../feat/wip/antigravity-clink/task-6-verification.md#authentication-coverage).

#### agy headless permissions

Headless agy cannot prompt, so any tool that its saved policy would normally **ask** about is auto-denied. A fresh install has no `permissions` block, which means every shell command is denied, and both the `default` and `codereviewer` role prompts tell the CLI to use terminal tools. In that state agy typically gives up after the first denial and returns an empty answer; PAL fails the request and names the denied actions, for example `command (RunCommand)`. File reads inside the selected project do not need extra rules. PAL does not pass `--dangerously-skip-permissions` or `--mode auto`, and the adapter rejects them in configuration, so the fix is native, in `~/.gemini/antigravity-cli/settings.json`. The narrow option is allow-rules scoped to the commands you intend the roles to run. The broad option, observed to unblock a full `codereviewer` run, is `"agentMode": "auto"` together with the project listed under `trustedWorkspaces`; that auto-approves tools generally rather than per command, so prefer explicit rules where you can.

```json
{
  "permissions": {
    "allow": ["command(git)", "command(regex:npm run (build|lint|test))"]
  }
}
```

`deny` and `ask` rules take precedence over `allow`, and an `ask` rule still blocks headless runs. Observed with agy 1.4.3 on Linux on 2026-10-20; see the [CLI permissions guide](https://antigravity.google/docs/permissions?tab=cli) for the full rule syntax.

Set `working_directory_absolute_path` to launch agy in a project. Without a request or saved choice, PAL uses the manifest's `working_dir`, then a fresh scratch directory. Follow-ups inherit the saved project or scratch mode. Both modes retain native permission checks. In the recorded 1.3.5 environment, scratch-mode reads required the operator to enable **Non-Workspace Access** (`allowNonWorkspaceAccess`). PAL does not enable it. It affects outside-workspace reads and writes and is not a read-only control. Use the [native permission settings](https://antigravity.google/docs/permissions?tab=cli) appropriate to your project. A completed answer alone does not prove a file was read: verify file-specific content and inspect permission notices.

For agy, **false inherits saved permissions**: PAL adds no edit-enabling mode flag, and saved settings may still permit file or command writes. True requests exactly `--mode accept-edits`; other tool permissions remain agy's responsibility. Neither value supplies per-path enforcement; nonempty `editable_paths` is rejected before role loading or execution. A follow-up with false or omitted `allow_edits` does not downgrade saved permissions. Stronger permission downgrades are deferred under [agy #1233](https://github.com/google-antigravity/antigravity-cli/issues/1233). Before claiming stronger enforcement, a released mechanism must pass saved-policy precedence, direct/command/MCP-write, both-cwd, argument-conflict, and concurrency checks; acceptance of a new mode flag alone is insufficient.

Example tool arguments for a review (replace the absolute path):

```json
{
  "cli_name": "agy",
  "role": "codereviewer",
  "working_directory_absolute_path": "/absolute/path/to/project",
  "prompt": "Review this file for correctness. Cite specific symbols and findings.",
  "absolute_file_paths": ["/absolute/path/to/project/module.py"]
}
```

Use the returned PAL `continuation_offer.continuation_id` for follow-ups; each still launches a new agy process. Native `agy_conversation_id` metadata is diagnostic only. Prompts travel in one JSON stdin event followed by EOF. Configuration cannot override the protocol, pass prompt arguments, select native continuation, supply mode/bypass flags, or capture results to an output file. Model and CLI timeout options remain configurable.

Agy stderr notices and error content are limited to 2,048 characters, with truncation indicators. Raw event arrays and stdout are not returned in agy error metadata. Nonzero exits, unsuccessful results, malformed/incomplete output, and empty answers fail without saving an exchange or replaying the invocation. Headless agy cannot prompt for permissions: a tool it would normally ask about (most often `command`, which the `default` and `codereviewer` roles encourage) is auto-denied and listed in the native result's `denied_actions`. When that leaves the answer empty, PAL reports the denied actions and the required allow-rule instead of a generic parse failure; when an answer still arrives, PAL returns the list as `agy_denied_actions` metadata so you can treat the reply as potentially incomplete. PAL never adds `--dangerously-skip-permissions` or a mode flag for this; add rules such as `command(git)` under `permissions.allow` in `~/.gemini/antigravity-cli/settings.json` per the [CLI permissions guide](https://antigravity.google/docs/permissions?tab=cli). For missing/expired login or locked-keyring diagnostics, sign in or restore native credential access outside PAL using the [CLI troubleshooting guide](https://antigravity.google/docs/cli/troubleshooting); retrying is a new request and can repeat effects completed before a failure.

From a development checkout, run the opt-in checks in each prepared personal/enterprise environment and record the account class externally:

```bash
PAL_RUN_AGY_INTEGRATION=1 python -m pytest tests/test_clink_agy_continuation.py -m integration -v
PAL_RUN_AGY_INTEGRATION=1 python communication_simulator_test.py --individual clink_agy_validation
```

The tests use disposable fixtures, the actual filtered environment, and isolated PAL manifests/state while retaining native login access. They verify roles with scratch, manifest-selected and request-selected directories, then replace the source value before a history-dependent follow-up. Request mode uses a symlinked launch path and a relative fixture read; its follow-up omits the directory. No opt-in means a reported skip; an opted-in missing executable/session, incompatible version, or failed required operation fails the run. The simulator uses `codereviewer` with a request-selected project and is excluded from `--quick` and `--ci`. Evidence includes native policy, selected-directory metadata and literal launch paths; Linux `/proc` observations record actual child cwd when available and explicitly identify unavailable coverage elsewhere. Tests do not manufacture expired sessions or inspect tokens; missing account coverage stays unverified.

### Optional live conversation check

From a development checkout with its virtual environment active, explicitly select an installed, authenticated CLI:

```bash
PAL_CLINK_LIVE_TEST=claude python -m pytest tests/test_clink_integration.py -q
PAL_CLINK_LIVE_TEST=codex python communication_simulator_test.py --individual clink_cli_only
```

The accepted selectors are `claude`, `codex`, and `gemini`; use `$env:PAL_CLINK_LIVE_TEST = 'claude'` before the Python command in PowerShell. The check records the installed version, uses shipped clink arguments with safe default permissions, and requires a first reply plus a follow-up that recalls both earlier input and the CLI's reply. Role summaries may accompany the expected answer.

Each check owns a temporary workspace and MCP session. It excludes PAL provider settings, checkout dotenv files, and personal PAL client manifests. It preserves home/configuration locations and the selected CLI's remaining environment allowlist, so existing CLI authentication remains accessible. Shared PAL credentials such as `OPENAI_API_KEY` and `GEMINI_API_KEY` are excluded; configure the intended CLI account outside PAL before testing. The harness does not install or sign in to a CLI, and successful output does not establish billing mode.

To check explicit model selection and inheritance with the shipped Codex or Claude manifest, also set `PAL_CLINK_LIVE_MODEL` to an identifier supported by that CLI/account, for example `PAL_CLINK_LIVE_TEST=claude PAL_CLINK_LIVE_MODEL=opus python -m pytest tests/test_clink_integration.py -q`. The check sends the model on the first request, omits it on the follow-up, verifies the selected model and CLI argv on both replies, and logs any CLI-reported model identity separately. The shipped Gemini manifest has no model template, so an explicit model fails that check.

With no selector, pytest skips and the simulator reports `SKIPPED` before setup or live execution. A skip-only invocation exits successfully with “No tests executed”; it does not establish coverage. Invalid selectors, missing executables, authentication errors, incompatible arguments, and failed requests fail an opted-in check. The scenario is excluded from `--quick` and `--ci`.

## First Request and Follow-up

Ask your MCP client to call the **clink tool** with these arguments. These JSON objects are tool arguments, not shell commands. Replace the project path with an existing directory on the PAL host, and `codex` with `claude`, `gemini`, or `agy` if that is your installed CLI. Codex normally requires a Git repository; see [its repository check](#codex-repository-check).

```json
{
  "cli_name": "codex",
  "role": "default",
  "working_directory_absolute_path": "/absolute/path/to/project",
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

The follow-up inherits the last successfully recorded CLI and role. Explicit `cli_name` or `role` values override them. New conversations require `cli_name` when multiple clients are configured, as in the shipped configuration. If exactly one is configured, it can be selected implicitly. The schema states this rule in the `cli_name` description and enforces it at runtime rather than through a top-level `anyOf`, which the Anthropic API rejects and which caused Claude Code to drop the tool. There is no implicit Gemini choice among multiple clients. Invalid selections fail; changing CLI while retaining a role the new CLI lacks also fails until you supply a supported role.

The follow-up also inherits the saved directory choice, including across CLI or role changes. Changing an established project requires a new conversation: omit `continuation_id` and supply the new directory. A conversation that began in scratch mode may select its first project while retaining its history.

### Choosing the CLI model

Use `model` to select a model in a client whose manifest declares `model_args`. The shipped Codex and Claude manifests enable this; the shipped Gemini and agy manifests do not. For example:

```json
{
  "cli_name": "claude",
  "model": "opus",
  "prompt": "Review this approach and give three concise risks."
}
```

The value goes to the CLI as one argument. PAL does not translate its own model aliases, resolve an API provider, or check account availability. Use an identifier or alias supported by your CLI and account. Values are limited to 512 characters so the requested selection can always be retained in response metadata. Empty values, surrounding whitespace, control characters, and names beginning with `-` are rejected.

On Windows, explicit or inherited model overrides reject `.bat` and `.cmd` launchers before execution. Windows can interpret batch-file arguments through a shell despite a direct subprocess call ([Python subprocess security considerations](https://docs.python.org/3.10/library/subprocess.html#security-considerations)). Configure a native CLI executable, or invoke the installed CLI script with a native `node.exe` command, instead of the npm batch shim. Do not use shell command launchers such as `cmd /c` or `powershell -Command` with caller-selected models: these operator-defined wrappers can interpret argument text. Existing launches without a request override retain their behavior.

After a successful, saved exchange, omission or `null` reuses that model for the **same CLI**, including role changes. An explicit model replaces it only after the next exchange is saved. Switching to a different CLI clears the selection unless that call supplies a new model; switching back does not resurrect an earlier selection. Failures and unsuccessful history writes leave the previous selection unchanged. Start a new conversation without `model` to return to the configured default. Existing conversations without a saved model keep their previous default behavior.

`metadata.model_requested` records the selected override; `metadata.model_used`, when the CLI reports it, is separate evidence of the model used. The CLI may resolve an alias to a different full model name. A reported model is never used as an implicit selection for later calls.

`/pal:continue` in provider-free mode instructs your client to reuse the previous available conversation tool and latest PAL continuation ID. A CLI conversation stays on clink. These IDs belong to PAL; native vendor session IDs are diagnostic metadata, not resume handles used by clink.

## Observed CLI activity and response limits

Successful Codex responses include `metadata.observed_activity`, computed by PAL from
`item.completed` events for `command_execution` items. It contains `source: "codex_jsonl"`,
a `commands` sample (command text, exit code, status), `command_count`, `failed_count`,
`denied_count`, and `truncated`. Repeated completion IDs are counted once; start/update
events are not counted. Counts cover all observed command completions, including those
omitted from the sample. Failed or declined status, or a nonzero exit code, counts as a
failure; `denied_count` counts explicit `declined` status only. PAL does not infer sandbox
denials from command output.

The sample keeps at most the first ten and last ten completions, with each command
limited to 256 characters. Shortened commands have `command_truncated: true`. A separate
6,000-character serialized JSON budget can remove additional middle entries, accounting
for JSON escaping. Either limit sets `truncated: true`. Missing or invalid command/exit-code
fields are `null`; unrecognized completion status is `"unknown"`.

```json
{
  "source": "codex_jsonl",
  "commands": [{"command": "sed -n 1,120p src/main.py", "exit_code": 0, "status": "completed"}],
  "command_count": 1,
  "failed_count": 0,
  "denied_count": 0,
  "truncated": false
}
```

These are CLI-reported command receipts, not filesystem auditing or proof the model
understood a file. Only completed shell-command items are covered: pending commands,
MCP tools, file changes, and other activity are outside this summary. An empty Codex
sample means no matching completions were observed. Claude, Gemini, and agy currently
omit this field, meaning activity coverage is unavailable. Their existing diagnostic
fields, including agy's denied-action labels, retain their separate meanings. Extraction
for those clients is deferred until their configured output exposes a verified activity
schema; add parser fixtures and equivalent bounded extraction before claiming coverage.

**Disclosure policy:** command text is returned as supplied by the CLI, apart from
truncation. It may contain sensitive arguments, inline scripts, or literal data; PAL
does not promise secret redaction. Command output and file contents from event payloads
are never copied into the activity summary. The CLI's answer can still contain anything
the CLI chooses to report.

All successful clients discard `events`, `raw_events`, `raw`, and `raw_output_file` copies
before presentation, including summarized or truncated answers. This removes Claude's
duplicate event arrays and Gemini's duplicate result envelope. Success diagnostics have a
separate 10,000-character serialized JSON budget: oversized fields are omitted whole,
largest first, while preserving the bounded activity summary. `metadata_truncated` and
`metadata_omitted_keys` report those omissions. PAL's exact `working_directory` selection,
tool/provider identity, history counters and persistence state/warnings are retained
outside that budget. The existing 20,000-character answer-text limit remains
separate; these are not limits on the entire MCP envelope. Failed-call
diagnostics retain their existing policy; parsed Codex failures can also include the
activity summary alongside raw error diagnostics. Activity covers the current invocation only;
it is not accumulated into conversation history.

## Tool Parameters

| Parameter | Behavior |
| --- | --- |
| `prompt` | Required current question or task. |
| `cli_name` | Configured client name: `codex`, `claude`, `gemini`, or `agy`; selection rules are above. CLI names are not PAL API model names. |
| `role` | Shipped roles: `default`, `planner`, `codereviewer`. Defaults to `default` on a new conversation, or the saved role on a continuation. |
| `model` | Optional CLI-native model. Requires the selected manifest's `model_args`. Omission/null inherits a successfully saved model only for the same CLI; a CLI switch resets it. |
| `absolute_file_paths` | Optional absolute file paths. PAL forwards references; the CLI opens files using its own capabilities and permissions. |
| `images` | Accepted image-path references. This does not guarantee image transport or multimodal support in the selected runner. |
| `continuation_id` | Existing PAL conversation UUID. Expired or invalid IDs fail before CLI execution. |
| `working_directory_absolute_path` | Optional project directory on the PAL host. Expands host-side `~`, then requires an absolute existing directory. Omission/null inherits the saved choice; without one, uses manifest `working_dir`, then scratch. |
| `allow_edits` | Defaults to `false` on **every request**, including follow-ups. True applies edit arguments; for agy it requests `--mode accept-edits`. Agy false inherits saved permissions and cannot guarantee read-only execution. |
| `editable_paths` | Optional absolute-path scope requiring `allow_edits=true`. Supported only by Claude through `--allowedTools Edit(path)/Write(path)`; Codex, Gemini, and agy reject it. |

## Project directory selection

The field selects the child's **launch directory**. PAL does not discover your
project from MCP roots, the prompt, attachments, or the client machine's cwd.
Remote callers must supply a path meaningful on the PAL server. `~/work/app`
expands using that server account's home directory; environment variables are
not expanded. Empty/relative paths, files, missing directories, broken links and
inaccessible paths fail explicitly. PAL does not create the requested directory.

| Saved choice | Current request | Result |
| --- | --- | --- |
| None | Explicit valid directory | Use it, even if the manifest directory is missing. |
| None | Omitted/null | Use manifest `working_dir`, otherwise scratch. |
| Project A | Omitted/null or a filesystem-equivalent alias | Reuse A's saved launch spelling. |
| Project A | Different project B | Reject before spawn; start a new conversation for B. |
| Scratch | Omitted/null | Use a fresh scratch directory, even if the newly selected CLI has a configured project. |
| Scratch | Explicit valid directory A | Run in A with existing history; bind A only after the exchange is successfully saved. |

The first choice is recorded atomically with the successful user/reply pair.
Execution failure, cancellation or a failed memory commit cannot establish or
replace a binding. A failed scratch-to-project attempt leaves the conversation
in scratch mode. Omission/null never clears a project; a new conversation is
required to return to scratch or choose another project.

PAL preserves two paths: `launch_path` keeps the normalized request's symlink
spelling, while `canonical_path` records its resolved identity. Manifest paths
retain the registry's resolved spelling. Equivalent aliases are compared by
filesystem identity and do not replace the original launch path. Both saved
paths are revalidated on every call; deletion, access loss or symlink retargeting
fails without falling back to another path. This is not an inode/content lock,
and the OS or native CLI may report a different spelling from `getcwd()`.

| Name | Role |
| --- | --- |
| `working_directory_absolute_path` | Public clink request field; no public `working_dir` alias. |
| Manifest `working_dir` | Operator default for a conversation without a saved choice or explicit request. |
| `initial_context.clink_working_directory` | Stored `mode` and, for a project, both paths; contains no temporary pathname or per-call source. |
| `metadata.working_directory` | Selected `mode`, `launch_path`, `canonical_path`, and `source`: `request`, `configuration`, `conversation`, or `scratch`. Scratch paths are null. |

Selection metadata survives output reduction and post-selection execution errors;
its presence on an error does not imply execution succeeded or the choice was
saved. An equivalent explicit repeat still reports source `conversation`.
Legacy or API-origin threads without the namespaced binding choose a directory
on their first successful clink call. Historical chat directory fields, old raw
`working_dir` values, attachments and parent bindings are not inherited as cwd.
API/local-tool interleaving preserves the clink binding without adopting it.
Bindings expire with process-local conversation memory and are lost on restart.

### Project configuration and headless execution

Selecting a directory can activate native project instructions, hooks, plugins
or MCP-server definitions. Those may affect behavior or start processes under
the CLI's policy. For example, Claude documents project-scoped
[settings and hooks](https://code.claude.com/docs/en/settings) and
[MCP configuration](https://code.claude.com/docs/en/mcp#project-scope).
Review the selected project's configuration and the installed CLI's headless
behavior; do not rely on an interactive trust or permission prompt protecting a
noninteractive call. PAL neither approves those prompts nor retries with bypass
flags. CLI options can also change the native workspace after launch.

Clink retains trusted local delegation. Giving a restricted MCP caller arbitrary
cwd selection can add access to project configuration it could not otherwise
activate. There is no operator directory allowlist in this version;
`PAL_WORKSPACE_ROOT` controls chat artifacts, not clink cwd. An operator-owned
allowed-project-roots policy is deferred as
[D1](../feat/done/clink-project-directories/implementation.md#deferred-follow-up-operator-directory-policy).
That work needs canonical containment, configuration-discovery and pre-spawn
denial rules before restricted-client deployments can rely on it. Even an
allowlisted launch directory would not confine subsequent reads or writes.

### Edit permissions

By default, clink omits the shipped write-enabling arguments and adds an execution-policy prompt asking the CLI not to modify files. This is not an OS sandbox or a guarantee against writes: native CLI settings, permissions, and tools still apply. Codex's shipped edit opt-in bypasses its approvals and sandbox; use it only when you intend that access.

For a scoped Claude edit, replace the example path with an existing absolute path:

```json
{
  "cli_name": "claude",
  "prompt": "Fix the typo in this file.",
  "working_directory_absolute_path": "/absolute/path/to/project",
  "absolute_file_paths": ["/absolute/path/to/project/README.md"],
  "allow_edits": true,
  "editable_paths": ["/absolute/path/to/project/README.md"]
}
```

If a later request also needs edits, supply `allow_edits` and any supported `editable_paths` again. PAL never inherits the edit opt-in from the original call. Omitting it selects the default argument behavior; for agy this leaves saved permissions in effect, without downgrading them. Authorizing edits without a path list does not retain an earlier scope.

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
| No `model_args` template | Model selection is unsupported for the current manifest. Update the complete manifest and restart PAL; existing user manifests are not automatically upgraded. No history is written. |
| Model flags conflict with a requested model | Move a configured model default into `default_model` and remove model flags from argument lists. No CLI process is started. |
| Authentication, quota, timeout, nonzero exit, or terminal CLI error | Inspect the returned diagnostic and the selected vendor's setup. The failed request creates/appends nothing, does not refresh TTL, and carries no continuation offer. There is no API fallback. |
| `ConversationBusyError`, `retryable=true` | Another holder or queued request owns this ID. Clink rejects immediately; retry the same request/ID after it finishes. Different conversation IDs can proceed independently. |
| Missing/expired ID, insufficient message slots, or oversized history | Start a new conversation or address the indicated limit. The error occurs before CLI launch. |
| Reply with `metadata.history_saved=false` | Generation succeeded but recording failed, conflicted, or expired. Keep the answer; no continuation is offered for it. PAL does not rerun the CLI or overwrite changed history. |
| Optional transcript write warning | The memory commit and normal continuation eligibility remain valid; audit recording is best effort. |

A non-terminal subtool warning alongside a successful final answer can remain successful. An explicit terminal failure is an error even if partial answer text is readable.

For Gemini `UNSUPPORTED_CLIENT`, check the vendor's [installation/update instructions](https://geminicli.com/docs/get-started/installation/) and [troubleshooting guide](https://geminicli.com/docs/resources/troubleshooting/). The marker alone does not establish an account-policy cause.

### Directory errors

Path and binding rejections return `status="error"`, no continuation offer,
`stage="validation"`, `execution_started=false`, and `retryable=false`.
They leave history, defaults, binding and TTL unchanged. `retryable=false` means
not to blindly repeat unchanged input; restoring a directory can make a later
call valid.

| `error_type` | Action | `new_conversation_required` |
| --- | --- | --- |
| `InvalidWorkingDirectoryError` | Correct the request or selected manifest path. | false |
| `WorkingDirectoryConflictError` | Start a new conversation for the different project. | true |
| `BoundWorkingDirectoryUnavailableError` | Restore access and the saved launch target, or start a new conversation. | false |
| `InvalidWorkingDirectoryBindingError` | Saved state is malformed; start a new conversation. | true |

Metadata includes `bound_working_directory` (the saved directory record, or null
for absent/scratch/undecodable state), the original `requested_working_directory`,
and `normalized_requested_path` / `canonical_requested_path` when those stages
were reached. These errors have no success-shaped `working_directory` selection.
Wrong JSON types are rejected earlier by the MCP SDK and retain its ordinary
input-validation error without this directory-specific envelope.

A directory preparation or spawn race returns `WorkingDirectoryLaunchError`,
`stage="spawn"`, `execution_started=false`, the selected `working_directory`
metadata and the OS diagnostic. It saves nothing. Executable failures and
cancellation are not relabeled as cwd errors.

### Codex repository check

Codex can reject a selected non-repository directory with
`Not inside a trusted directory and --skip-git-repo-check was not specified.`
PAL preserves that diagnostic and adds guidance: select an existing repository,
or deliberately append `--skip-git-repo-check` to the existing `additional_args`
in a complete copied Codex manifest, then restart PAL. Preserve the other
manifest fields and arguments. This is an operator choice; PAL does not add the
flag for a selected directory or automatically retry the failed invocation.
PAL itself does not require a `.git` directory; worktrees may use `.git` files.

## Configuration

The shipped manifests live in [`conf/cli_clients/`](../../conf/cli_clients/); role prompts live in [`systemprompts/clink/`](../../systemprompts/clink/). Copy a **complete** manifest to `~/.pal/cli_clients/` to customize it. Later manifests replace a client's full definition, rather than patching individual fields. See [configuration precedence](../configuration.md#cli-conversation-history-budgets).

Arguments are assembled from internal defaults, `additional_args` (always), model selection, one of `safe_args` / `edit_args`, runner-generated arguments, and `role_args`. Keep write-enabling flags out of always-applied arguments.

To enable model selection, add these fields to a complete manifest after checking the CLI's model flag:

```json
{
  "model_args": ["--model", "{model}"],
  "default_model": "sonnet"
}
```

`model_args` accepts `[]` (unsupported), `["--model", "{model}"]`, or `["-m", "{model}"]`. The placeholder must be a whole argument; templates cannot expose permission or other options as model selectors. `default_model` is optional and requires a template. Claude's shipped default remains `sonnet`; Codex uses its own configured default when no override is selected.

An explicit or inherited request model takes precedence over `default_model`. For such an override, model flags in `additional_args`, the active permission argument list, or the selected `role_args` are rejected as conflicts rather than rewritten, including `--model=value` and `-mVALUE` forms. Migrate a general default to `default_model`; use the request field to choose other models. Without a request override, existing manually configured argument lists retain their behavior. The executable `command` and its launcher arguments are opaque (for example, `python -m` may launch a module); PAL does not rewrite or check them for model conflicts. Vendor configuration overrides such as Codex's `-c model=...` are also opaque. Keep model settings in the dedicated fields rather than combining these mechanisms.

Custom manifests replace the full shipped definition. To use `model` with an existing override, add `model_args` and migrate any model flags there too. Restart PAL after changing manifests. The model flag was checked against installed Codex CLI **0.171.0** (`exec --help`) and Claude Code **2.1.296** (`--help`); see the official [Codex CLI source](https://github.com/openai/codex/blob/main/codex-rs/utils/cli/src/shared_options.rs) and [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).

These are the current base commands for the shipped presets. PAL sends the prompt over stdin; Claude also receives a request-local `--append-system-prompt` argument, and scoped edits add `--allowedTools` arguments.

| CLI | Default command (`allow_edits=false`) | Change when `allow_edits=true` |
| --- | --- | --- |
| Gemini | `gemini -o json` | Append `--yolo`. No telemetry flag is shipped. |
| Claude | `claude --print --output-format json --model sonnet --permission-mode default` | Replace the safe permission bucket with `--permission-mode acceptEdits`. |
| Codex | `codex exec --json --enable web_search_request --skip-git-repo-check` | Add `--dangerously-bypass-approvals-and-sandbox` before the scratch-directory flag. |
| Antigravity | `agy --input-format stream-json --output-format stream-json` | Append exactly `--mode accept-edits`. False inherits native policy; no read-only guarantee. |

PAL automatically adds Codex's `--skip-git-repo-check` only for scratch directories. Request-selected and configured directories retain the native repository policy unless the operator explicitly supplied the flag in the manifest. The shipped `--enable web_search_request` flag is version-sensitive: the current [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference) does not list that feature name. Check support in your installed release before relying on this preset; the table records PAL's configuration, not universal vendor compatibility. For the other command formats, see [Claude programmatic use](https://code.claude.com/docs/en/headless) and [Gemini headless output](https://geminicli.com/docs/cli/headless/).

### Flag prompts and JSON replies

For a config-only client, `custom.prompt` can specify `{"mode": "flag", "flag": "--question"}`. PAL appends the flag and the complete prepared prompt as two arguments, then closes stdin. Prompt text, role instructions and retained conversation history can appear in process listings. Use a launcher that preserves argv boundaries all the way to the CLI. A native interpreter running an absolute script path can do this. Command-string wrappers such as `sh -c`, `cmd /c` and `powershell -Command` are unsupported. On Windows, PAL rejects batch launchers for flag prompts. [Python subprocess security considerations](https://docs.python.org/3/library/subprocess.html#security-considerations)

The assembled prompt must fit the OS launch limits. Linux limits each argument to 32 pages (128 KiB with 4 KiB pages, including its terminator), and also limits the combined arguments and environment. Windows limits the complete quoted command line to 32,767 UTF-16 code units including its terminator. If launch fails because of length, shorten the request or start a new conversation with a summary. To retain less history in future sessions, lower `history_token_budget` in the manifest and restart PAL. A restart discards existing continuation IDs. [Linux execve(2)](https://man7.org/linux/man-pages/man2/execve.2.html) · [Windows CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)

Either prompt mode works with `custom.output` set to `{"format": "json", "path": ["reply", "text"]}`. The CLI must exit successfully and emit one UTF-8 JSON document on stdout. Each path element is a literal object key, so `["reply.text"]` selects a single key containing a dot. The selected value must be a nonblank UTF-8 string. PAL preserves its whitespace and applies the usual reply-size limit.

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

An explicit request directory takes precedence over `working_dir` for an unbound conversation; follow-ups inherit the saved choice. The selected directory must exist and is preserved. Scratch mode uses a fresh private temporary directory per call, removed after success, failure, timeout, or cancellation. Immediate-child termination/reaping precedes scratch cleanup; draining output pipes is bounded. Different conversations can overlap, including in the same project; PAL adds no project lock. Relative manifest paths retain the registry's configuration/project resolution rules. See [project selection](#project-directory-selection) and [operator configuration](../configuration.md#cli-project-directories).

Home/auth locations remain available, so a scratch directory does not prevent access elsewhere. File reads and edits still depend on the selected CLI's native controls; this feature adds no filesystem sandbox or process-tree supervision guarantee.

### CLIs installed with nvm

A desktop MCP client may start PAL without the interactive shell setup that adds nvm's bin directory to `PATH`. A CLI that works in your terminal can then fail with `Executable 'codex' not found in PATH`. An absolute `command` alone can still fail with an error such as `/usr/bin/env: ‘node’: No such file or directory`: npm launchers commonly use `#!/usr/bin/env node`, which needs Node on the child's `PATH`.

1. In a terminal where the intended nvm version is active, locate both executables:

   ```sh
   command -v codex
   command -v node
   ```

2. Copy the complete [`conf/cli_clients/codex.json`](../../conf/cli_clients/codex.json) manifest to `~/.pal/cli_clients/codex.json`, or edit that override if it already exists. Preserve its argument, permission, and role settings. Add `PATH` to its existing `env` object. This is sufficient if the path includes both the CLI and Node; optionally set an absolute `command` to pin the executable. This example shows both fields, **not a complete manifest**:

   ```json
   {
     "command": "/home/alice/.nvm/versions/node/v24.21.0/bin/codex",
     "env": {
       "PATH": "/home/alice/.nvm/versions/node/v24.21.0/bin:/usr/local/bin:/usr/bin:/bin"
     }
   }
   ```

   Replace the example with the actual executable path and the directory containing your `node`. `env.PATH` replaces the inherited value: include every other directory the CLI needs, such as the one containing `git`. Values are literal; PAL does not expand `$PATH`, `$HOME`, or `~` in `command` or `env.PATH`. If the executable path contains spaces, quote it inside the JSON string, for example `"command": "\"/home/Alice Smith/.nvm/versions/node/v24.21.0/bin/codex\""`.

   Alternatively, place the complete manifest elsewhere and set `CLI_CLIENTS_CONFIG_PATH` to its absolute file or directory path in PAL's launch environment. Definitions in `~/.pal/cli_clients/` load last and replace earlier definitions for the same CLI; edit or reconcile an existing user override if it would supersede this file.

3. Restart PAL, check the executable status in `listmodels`, then make a small `clink` request. Discovery verifies the CLI executable only; the request also exercises its interpreter and authentication. Update both paths after switching or removing the pinned nvm version. For another npm-installed CLI, use its own manifest and executable name.

The command and PATH workaround was checked with nvm-installed Codex CLI 0.171.0 on Linux: a missing nvm path failed lookup, an absolute command without Node exited 127, and the absolute command with Node's bin directory returned the CLI version successfully. That launch check did not make a model request.

## Related Guides

- [Getting Started](../getting-started.md#cli-conversations-without-pal-provider-keys) - Local CLI and API setup paths
- [Configuration](../configuration.md#cli-conversation-history-budgets) - History settings and restart effects
- [Planner Tool](planner.md) - Provider-independent planning workflow
- [Chat Tool](chat.md) and [CodeReview Tool](codereview.md) - API-backed workflows
- [Context Revival](../context-revival.md) - PAL conversation threading
- [Logging and transcripts](../logging.md) - Optional audit recording

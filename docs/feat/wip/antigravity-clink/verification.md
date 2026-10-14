# Antigravity clink: isolated verification

> Status: historical research complete; design review findings addressed; implementation pending
> Verified: 2026-10-10
> Checkout: `c20d384a9c5a38ab8d58c780d2e269c537e409fa`
> Request: [PAL issue #465](https://github.com/BeehiveInnovations/pal-mcp-server/issues/465), originally supplied as a pull-request URL
> Method: `kk:chain-of-verification:isolated`; five independent default agents without the initial answer or conversation history
> Scope: repository inspection and documentary verification; no CLI execution, authentication, installation, or live model requests
> Final artifacts: [design.md](./design.md), [implementation.md](./implementation.md), [tasks.md](./tasks.md)

This is a research record for the ongoing `kk:design` workflow. It is not an approved design or an implementation plan. All five verification agents completed. No fallback to non-isolated verification was needed. Public documentation was retrieved on the verification date and may change; release notes supply the version distinctions below. The official `agy` executable was not found on the inspection shell's PATH.

**Superseding decision, 2026-10-14:** the user selected installed 1.3.5 and approved inheriting agy's permissions. For agy, false omits PAL's edit-mode flag without guaranteeing read-only execution; the final design maps true to `--mode accept-edits` (implementation remains Task 4). The original no-edit prerequisite discussed below is historical and no longer gates implementation/release. Track stronger enforcement under [agy #1233](https://github.com/google-antigravity/antigravity-cli/issues/1233); use the current [permission contract](./design.md#permission-checkpoint), [tasks](./tasks.md), and [live evidence](./capability-evidence.md) for current state. Preserve the original research below as a dated record, not current requirements. The original historical review is unavailable; the current `review-design.md` is a fresh 2026-10-14 report.

**Task-boundary update, 2026-10-14:** the user approved finishing Task 1 on verified enterprise evidence and moving unavailable personal-login, successful ADC routing, and missing/expired cached-session checks to Task 6 as explicit release requirements. The [authentication follow-up](./capability-evidence.md#authentication-follow-up-2026-10-14) records synthetic forwarding, an explicit missing-ADC error, and continued enterprise-login success. It does not claim the moved checks passed.

## Current baseline after design review

The historical 2026-10-12 review reportedly checked HEAD `2d821f14cb35869added0838827177dc957f6da6`; its original report is unavailable. The original Q1–Q5 answers below retain their historical checkout context. A [fresh 2026-10-14 review](./review-design.md) now supersedes that baseline for implementation: public clink execution owns `CLIConversation` preparation, bounded history, and atomic success-only exchange recording. Use the current [design](./design.md) and [plan](./implementation.md); the older decorator and persistence descriptions below are historical, not implementation instructions. No review alone establishes agy runtime capability.

Task 1 owns verification of authentication passthrough and useful referenced-file reads plus safe/edit behavior in both cwd modes. The existing no-edit gate remains unresolved. Shared prompt-file admission may precede an unsupported-path rejection; the accepted guarantee is no agent creation or subprocess launch. Lifecycle cleanup findings from the old checkout were fixed before this review and need regression coverage, not a duplicate repair task.

## Confirmed requirements entering verification

- Serve individuals and team/enterprise users with a locally installed, authenticated CLI.
- Follow-up requirement confirmed: preserve existing Google login/subscription use without requiring an API key.
- Support default, planner, and codereviewer roles, PAL continuation, prompt omission from command logs, and existing-client regression coverage.
- Preserve the clink API, default client, and Gemini support.
- Reuse the current configuration/subprocess architecture without new Python dependencies.
- Preserve opt-in edits and reject path restrictions that cannot be honored.
- Evaluate both configuration-driven prompt arguments and an Antigravity-specific adapter before selecting a direction.

The user approved these requirements, selected the thin agy adapter with structured stdin, and approved all three walkthrough sections: request flow/scope, results/failure/safety behavior, and implementation/validation order. The linked design and plan record those decisions and the subsequent review corrections. Do not treat untested capabilities as already fulfilled.

## Follow-up clarification: version and authentication

The user clarified that the current CLI is **1.3.3**, confirmed as the latest entry in the [official changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md#133). Use 1.3.3 as the reference version for the continuing design. Version 1.0.16 is the issue's historical context, not the requested implementation target. A backward-compatibility floor has not been selected.

The user subsequently confirmed that reusing the existing Google login/subscription **without an API key is essential**. The proposed mandatory API-key route for individuals is therefore rejected. Current `agy` can reuse a cached Google login; API-key mode is an alternative that sends requests directly to the Gemini API. Enterprise authentication can use its own documented routes. [Authentication documentation](https://antigravity.google/docs/cli/install)

The consumer-wrapper restriction is an upstream support/use restriction, not evidence that headless mode technically requires an API key. Document this restriction accurately while preserving the user's authentication requirement. PAL should launch the configured CLI using its existing authentication; do not add an API-key requirement, inspect stored credentials, or silently switch authentication providers. Successful subprocess authentication must not be represented as proof of upstream support for consumer-plan wrappers.

## Supplemental transport evaluation for 1.3.3

This section records follow-up research by the main agent after isolated verification; it is not an additional independent verification run. Existing permission uncertainty remains unresolved.

Official documentation explicitly says a streaming session finishes its current turn and emits the final result after stdin closes, including when the final prompt is immediately followed by closure. It specifies one result event per input turn. That makes a single structured input followed by `communicate()` completion a documented candidate for clink's existing lifecycle. This has not been tested against a local 1.3.3 binary. [Stream session completion](https://antigravity.google/docs/cli/headless#end-a-session)

Re-evaluating the already-authorized configuration-driven versus adapter approaches:

| Approach | Benefit | Cost |
| --- | --- | --- |
| Generic configuration-driven input encoding | Reusable across future clients with declarative transport settings | Introduces configuration and schema validation for an agy-specific message envelope without another established consumer |
| Thin agy adapter plus shared stdin-encoding hook | Keeps the agy input envelope and response parser in client-specific code while retaining shared subprocess lifecycle | Requires one shared hook, its compatibility coverage, and agy-specific protocol tests |

The user selected the thin adapter. This revises the initial comparison: a small encoding hook avoids duplicating `run()`, and the documented stdin path removes the original reason to introduce `prompt_to_arg`. Existing clients retain their current raw UTF-8 stdin behavior. The agy output parser validates its result rather than treating arbitrary stdout as a successful answer. The completed walkthrough authorizes the documented design, not a claim that safe-default behavior has been verified.

The follow-up documentation lookup did not establish an invocation-specific permissions override that reliably preserves the no-write default independently of saved user settings. Do not invent a settings flag, silently rewrite the user's configuration, or claim planning instructions enforce a filesystem boundary. The implementation plan must start with a focused capability check and retain the confirmed safe-default requirement if that check fails.

## Assumptions and scope for the selected direction

The following assumptions are testable requirements for the proposed design, not confirmed runtime behavior:

- A pre-authenticated 1.3.3 CLI launched by PAL can reuse the same account session without PAL handling credentials. Validate both personal and configured enterprise sessions and missing/expired-login errors.
- One encoded user message followed by stdin closure produces exactly one complete result. Validate that contract against 1.3.3, including malformed output and errors.
- A permission mechanism can honor `allow_edits=false` independently of permissive saved settings without rewriting the user's configuration. This is the critical unresolved assumption; validate it before implementing or releasing an enabled integration. Failure requires a design decision rather than weakening the accepted requirement.
- Existing role prompts and PAL's assembled conversation context suffice for the new client. Validate each role and a continued request; native agy sessions need not be reused across calls.

Approved exclusions from the walkthrough: PAL-managed authentication or mandatory API keys (authentication stays with agy); v1.0.16 compatibility (the reference is 1.3.3); long-lived agy processes/native session management (PAL owns continuation); generic prompt-template configuration (only one concrete new protocol is needed); per-path edit support (reject until an enforceable mechanism is established).

Rejected directions: API-key-only access conflicts with the confirmed requirement; argv prompt transport adds exposure and argument-size limits when structured stdin is available; a generic message-template configuration expands the public configuration surface for one client; duplicating subprocess execution in a separate runner would duplicate lifecycle responsibilities. The selected adapter specializes encoding/parsing while sharing execution.

## Initial answer, before verification

The initial recommendation was a narrowly scoped, configuration-driven `prompt_to_arg` setting. Existing clients would continue using stdin when the setting was absent. Antigravity would receive the prompt as one argument and use a plain-text parser. The runner would maintain a separate redacted command for logging and metadata.

| Criterion | Configuration-driven approach | Antigravity-specific adapter |
| --- | --- | --- |
| Intended value | Both account types, roles, and continuation, subject to verification | Same intended outcome |
| Feasibility | Optional configuration field plus shared argument rendering and redaction | Dedicated agent plus a shared transport hook or duplicated execution logic |
| Compatibility | Existing configurations retain stdin | Client-specific behavior, with shared-hook regression risk where applicable |
| Maintenance | One subprocess lifecycle and a reusable capability | Separate behavior to maintain; duplication can diverge in timeout/error handling |
| Principal risk | Prompt disclosure or incorrect argument boundaries | A second execution path for a small transport difference |

The rationale for preferring the first approach was that transport and parsing appeared to be the only required differences. The existing command hook cannot change stdin delivery. Account compatibility and safe edit flags were explicitly unverified. The issue author's reported v1.0.16 behavior was the historical input, not a verified description of current releases.

## Verification questions and independent answers

### Q1: What owns execution and which transport extension points exist?

**Agent:** `cove_runner` (default). **Status:** completed. **Verdict:** confirmed, with a redaction correction.

[`BaseCLIAgent.run()`](../../../../clink/agents/base.py) owns process creation, stdin delivery, timeout handling, error recovery, parser invocation, output-file capture, and result construction. Command hooks receive role/system-prompt information but not the assembled user prompt. A separate transport therefore needs a shared hook/refactor or an override of `run()` that preserves its responsibilities.

`sanitized_command` currently means an argv copy, not an actively redacted representation. The output-file branch replaces that copy again before logging. [`CLinkTool._build_success_metadata()`](../../../../tools/clink.py) publishes it as command metadata.

Client names must be registered in [`INTERNAL_DEFAULTS`](../../../../clink/constants.py); a JSON manifest alone cannot introduce an arbitrary client. [`ClinkRegistry`](../../../../clink/registry.py) resolves roles, runner, and parser. Agent and parser factories have separate registries.

Roles and prompt assembly belong to the tool. PAL reconstructs continuation history in [`server.py`](../../../../server.py); native `agy` session persistence is not required for PAL continuation. This is structural evidence, not proof that new-client continuation works at runtime.

Regression anchors: [`test_clink_claude_agent.py`](../../../../tests/test_clink_claude_agent.py), [`test_clink_safe_default.py`](../../../../tests/test_clink_safe_default.py), and [`test_clink_tool.py`](../../../../tests/test_clink_tool.py).

### Q2: What prompt and output interfaces does official agy support?

**Agent:** `cove_agy_interface` (default). **Status:** completed. **Verdict:** historical assumptions do not describe current releases.

Current documentation describes a prompt-valued print option, separate stdout responses and stderr diagnostics, text/JSON/NDJSON output, and NDJSON user-message input on stdin. Structured stdin mode excludes a simultaneous print prompt. Exact plain-text stdin behavior in v1.0.16 remains unverified. Neither “agy never supports stdin” nor a portable bare print flag with stdin is established. [Headless interface](https://antigravity.google/docs/cli/headless)

The official CLI is a terminal executable named `agy`; installation documentation warns about old launcher aliases. A command name alone is insufficient to identify the installed product. [Installation and authentication](https://antigravity.google/docs/cli/install)

Release evidence is consolidated in the version table below. Current structured stdin creates an additional design candidate; this is an inference from the documented interface, not a selected implementation.

### Q3: Which accounts can use the CLI and this integration?

**Agent:** `cove_agy_accounts` (default). **Status:** completed. **Verdict:** technical authentication and upstream-supported integration must be distinguished.

Eligible personal accounts can sign into the CLI. Organization users have a provisioned Business/enterprise path; a Workspace address alone does not establish eligibility. Enterprise authentication includes Cloud sign-in, Workforce Identity Federation, and Application Default Credentials. [CLI authentication](https://antigravity.google/docs/cli/install), [Enterprise documentation](https://www.antigravity.google/docs/enterprise)

Crucially, Google's FAQ says consumer-plan headless use is intended for direct scripts/CI and does not support embedding `agy` in third-party coding agents/applications. Successful personal authentication therefore does not establish supported use through clink. The FAQ points third-party Gemini integrations toward enterprise or AI Studio API access. [Official FAQ](https://antigravity.google/docs/faq#can-i-use-antigravity-cli-headless-mode-in-third-party-coding-agents-or-automate-account-switching)

Current CLI documentation also provides a Gemini API-key path. That is direct Gemini API access, not proof that a consumer Antigravity subscription or every Workspace account supports this wrapper. Enterprise/API-key eligibility and behavior require their own acceptance tests. [Authentication options](https://antigravity.google/docs/cli/install)

### Q4: Can safe edits and path restrictions be enforced?

**Agent:** `cove_agy_permissions` (default). **Status:** completed. **Verdict:** not established for v1.0.16 or by a simple flag omission.

Current headless documentation says unavailable approvals cause soft denials, which can still yield a successful exit code. It also describes workspace file reads/writes as allowed by default. Print mode alone does not establish read-only execution. [Headless permissions](https://antigravity.google/docs/cli/headless#permissions-in-headless-mode)

Execution modes separate default review and accepted edits; plan mode adds planning instructions rather than a documented filesystem prohibition. Shell permissions are separate. [Execution modes](https://antigravity.google/docs/cli/modes)

File permissions support path rules with deny-over-ask-over-allow precedence. A narrow allow cannot override a matching broad deny. No complete documented per-invocation recipe was found that guarantees read-only execution plus explicitly enabled edits independently of persistent user configuration. Command/MCP/browser tools can create additional mutation paths. [CLI permissions](https://antigravity.google/docs/permissions?tab=cli#cli-fine-grained-permissions)

The sandbox can retain writable workspace mounts; enabling it alone is not proof of no-write behavior. The docs do not fully reconcile headless defaults with all review settings. Treat this as a required capability investigation, not an implementation detail to assume away. [CLI sandbox](https://antigravity.google/docs/sandbox?tab=cli), [CLI reference](https://antigravity.google/docs/cli/reference)

### Q5: What constraints come with prompt arguments?

**Agent:** `cove_argv` (default). **Status:** completed. **Verdict:** application redaction is narrower than prompt secrecy.

With a native POSIX executable, each `create_subprocess_exec` argument is already a distinct argument; shell splitting or quoting the substituted prompt would corrupt its boundaries/content. Windows serializes arguments, so target parsing and batch launchers need separate consideration. [Python subprocess execution](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.subprocess_exec), [Windows argument conversion](https://docs.python.org/3/library/subprocess.html#converting-an-argument-sequence-to-a-string-on-windows)

Argument size is bounded. Linux has per-string and aggregate argument/environment limits; Windows has a total serialized command-line limit. Role text and continuation history enlarge the forwarded prompt, so this affects ordinary clink requests rather than only unusual inputs. [Linux execve](https://man7.org/linux/man-pages/man2/execve.2.html), [Windows CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)

Redacting PAL-owned command fields does not redact process arguments or auditing. Linux exposes command lines subject to host permissions; configured Windows auditing can record them. Child output can also echo prompts into captured metadata. [Linux process command line](https://man7.org/linux/man-pages/man5/proc_pid_cmdline.5.html), [Windows command-line auditing](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/component-updates/command-line-process-auditing)

The current output-file branch can undo early redaction by copying real argv again. Spawn handling wraps `FileNotFoundError` but not argument-size failures. Any argv design must address both explicitly and test Unicode, quotes, backslashes, multiline content, output-file mode, and error paths.

## Version evidence

The official changelog records these changes. They must not be projected backward onto v1.0.16. This table establishes feature/fix introduction, not runtime certification of any release. [Official CLI changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md)

| Version | Relevant change |
| --- | --- |
| 1.1.0 | Execution modes and interactive pre-write review |
| 1.1.1 | Fix for print-mode subprocess hangs while reading stdin |
| 1.1.3 | Headless permission soft denial replaces hangs/silent approvals |
| 1.1.4 | Headless runs honor persisted permission and related settings |
| 1.1.8 | Structured output formats and schema support |
| 1.1.10 | Business authentication, WIF, and ADC |
| 1.1.12 | Fix for headless execution ignoring mode selection |
| 1.1.13 | Gemini API-key support |
| 1.1.15 | Streaming JSON prompt input |
| 1.1.18 | Fix for valueless prompt flags consuming another flag |

## Reconciliation and revised conclusion

| Initial claim or assumption | Evidence | Status | Action |
| --- | --- | --- | --- |
| Existing hooks cannot independently replace stdin transport | Q1 | Confirmed | Retain the shared-hook versus lifecycle-override trade-off |
| No new Python dependency is intrinsically needed for either original option | Q1 | Engineering assessment retained | Reassess if the external protocol demands more; no dependency selected |
| Argument input and text output are the necessary current agy interface | Q2 | Contradicted as a current constraint | Evaluate documented structured stdin/output before adding argv transport |
| Both account categories imply supported clink usage | Q3 | Contradicted for consumer-plan wrappers | Preserve the user audience goal, but reconsider supported authentication routes |
| Safe behavior can follow merely from omitting write-enabling flags | Q4 | Inconclusive for the requested guarantee | Require version-specific permission evidence and tests before shipping |
| Command redaction prevents all prompt exposure | Q5 | Not established; narrower guarantee only | Separate PAL-owned logging from OS/audit/child-output channels |
| A configuration-driven argv primitive is the best starting point | Q2–Q5 | Superseded | User selected thin adapter with structured stdin; permission capability verification remains pending |

The code-level argument for sharing subprocess lifecycle remains valid. It no longer establishes that `prompt_to_arg` should be built: current structured stdin may avoid the argument transport entirely. A minimum supported version must follow required capabilities and live verification, not the issue's historical example alone.

The original two-option comparison was incomplete; the supplemental evaluation above addresses the current stdin interface. The documented design selects the thin adapter against 1.3.3 with inherited login and no required API key; verify the permission mechanism before implementation proceeds. Upstream endorsement of consumer-login wrapping, v1.0.16 compatibility, and a guaranteed read-only invocation must not appear as established capabilities.

## Open decisions and follow-up work

These items distinguish resolved design choices from runtime evidence and unrelated work still needed. The implementation tasks now own the feature's capability and acceptance checks; no implementation has been deferred silently.

1. **Audience and authentication — requirement resolved, runtime verification pending:** existing Google login/subscription use without an API key is required, alongside configured enterprise authentication. Consumer-login wrapping remains unsupported upstream and must be documented as such. Next: Task 1 verifies the design's agy-specific passthrough names and authentication through the actual filtered child environment; Task 2 implements those defaults and synthetic environment tests. Do not substitute API-key-only acceptance tests or silently switch providers.
2. **Version and transport — direction resolved, runtime verification pending:** use 1.3.3 as the current reference and a thin adapter with structured stdin. Next: inspect that binary's version/help and exercise a one-request/EOF lifecycle, result/error envelopes, roles, and PAL continuation with controlled fixtures. Legacy-version compatibility is out of scope.
3. **Edit safety and useful file access:** no robust per-request no-write mechanism is verified. Next: Task 1 tests effective policies without modifying users' persistent settings, including content-dependent reads, attempted file edits, command-based writes, and opt-in edits under permissive settings in both cwd modes. In scratch mode, project fixtures must remain outside the CLI cwd. If useful reads and the safe/edit contract cannot coexist, revisit scope explicitly before Task 2.
4. **Logging guarantee — scope resolved, preexisting follow-up deferred:** the approved feature protects the new forwarded prompt in command channels. Existing Claude code includes its system prompt in argv and the returned command; its test expects that value. This is preexisting and unmodified, so fix it in a separate scoped redaction change. Next: inspect `clink/agents/claude.py` and `tests/test_clink_claude_agent.py` together before changing expectations.
5. **Argument transport — rejected:** prompt-specific argument-size handling is unnecessary for the selected stdin transport. The generic subprocess failure behavior is unchanged; reconsider explicit size-error handling only if a future feature introduces prompt arguments.
6. **Preexisting lifecycle cleanup — resolved in the current baseline:** the original checkout had output-file cleanup and cancellation gaps. HEAD `2d821f1` has `ExitStack` cleanup and `_kill_and_reap()` for timeout/cancellation. Next: preserve `tests/test_clink_environment.py` coverage while adding the encoder at `_run_in_directory()`'s communication site; do not reopen the already-fixed gaps as agy work.

## Validation record

- Five independent verification agents completed; answers reconciled against the original recommendation.
- Parent rechecked the official FAQ, headless interface, and changelog. Capy search supplied existing clink safety context but no evidence resolving the new account/permission questions. No CoVe indexing was performed.
- `./code_quality_checks.sh` was attempted before and after saving these notes. Both attempts exited because no project/active virtual environment was found; linting and tests did not run.
- Final document checks passed: four Markdown files, 36 local links/anchors, six pending tasks, ordered dependencies, symmetric parallel markers, required final verification skills, and whitespace checks. These are author checks, not independent design review.
- Repository source was not changed. Authentication, CLI behavior, account availability, and permission enforcement were not tested live. The approved walkthrough is captured in the linked design/implementation/task documents; all implementation tasks remain pending.

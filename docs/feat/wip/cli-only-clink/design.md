# Design: CLI-only conversations through clink

> Status: reviewed and revised; Tasks 1–2 complete, Tasks 3–14 pending
> Created: 2026-10-10
> Issue: [BeehiveInnovations/pal-mcp-server#402](https://github.com/BeehiveInnovations/pal-mcp-server/issues/402)
> Source baseline: `2d821f1` (rebased from `c20d384` on 2026-10-12)
> Implementation: [implementation.md](implementation.md)
> Tasks: [tasks.md](tasks.md)
> Review: [review.md](review.md)

## Problem and outcome

Individual developers running PAL locally want to converse through their installed, authenticated AI CLIs without configuring a model API provider in PAL. The existing `clink` tool launches Codex, Claude Code, and Gemini CLI. PAL now starts without model providers and renders clink continuation history with a provider-independent budget. The remaining work is to make that path discoverable and reliable: retain CLI/role selection, budget complete exchanges, classify failed CLI output, and save successful exchanges atomically.

Success means a developer can start PAL without provider configuration and hold an ongoing, bounded conversation through a selected CLI. PAL makes no direct model-provider inference calls in that path. A first request followed by a context-dependent request is the minimum acceptance test, not a two-message product limit.

The user approved the original audience, outcome, constraints, and clink approach, then requested verification and correction of all valid design-review findings. This revision reconciles the plan with the newer server-reliability implementation. It preserves existing provider-free tools and raw audit records while keeping the intended bounded CLI conversation experience. The [review](review.md) records the evidence and resolutions; current implementation progress is in [tasks.md](tasks.md).

## Constraints

- Preserve API-backed startup, model selection, tools, and conversation behavior when providers are configured.
- Preserve provider-free startup and existing planner, tracer, docgen, challenge, apilookup, and Jules capabilities even when clink is disabled or no CLI executable is installed. Jules retains its own service-credential requirement.
- Use CLIs installed and authenticated by the developer. Do not install them or manage their credentials.
- Retain existing edit opt-in controls, with authorization supplied on each request.
- Never switch from a failed CLI request to an API provider.
- Use existing dependencies and process-local conversation storage.

## Current mechanisms and evidence

These are observations of the baseline source, not declarations that the proposed behavior exists.

| Mechanism         | Current behavior                                                                                                                                                                                                                          | Source                                                                                                                                                                           |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Startup           | `_serve()` configures providers before MCP stdio. No providers or an empty restricted auto-mode catalogue produces a warning; malformed registries and other existing bootstrap failures still raise.                                     | [server.py](../../../../server.py), [provider declarations](../../../../providers/configuration.py)                                                                              |
| CLI execution     | `CLinkTool.requires_model()` is false; its custom `execute()` calls an asynchronous subprocess runner.                                                                                                                                    | [tools/clink.py](../../../../tools/clink.py), [runner](../../../../clink/agents/base.py)                                                                                         |
| Continuation      | `_dispatch_tool()` routes clink without provider resolution. `@conversation_request()` saves the current input before execution; `invocation_history(text_only=True)` uses `CLIHistoryBudget`. The MCP boundary queues same-thread calls. | [invocation ownership](../../../../utils/conversation_context.py), [coordinator](../../../../utils/conversation_coordination.py)                                                 |
| Storage           | Thread records are process-local JSON with TTL. `create_thread()` and `add_turn()` write incrementally and emit optional transcript events; clink stores raw replies before presentation truncation.                                      | [conversation memory](../../../../utils/conversation_memory.py), [storage](../../../../utils/storage_backend.py), [transcripts](../../../../utils/transcripts.py)                |
| Discovery         | Listings iterate enabled tools. Planner/tracer work locally and Jules needs its own credentials, not a PAL model provider. `requires_model=False` also applies to provider-dependent consensus.                                           | [server.py](../../../../server.py), [tool-surface tests](../../../../tests/test_tool_surface.py), [Jules tests](../../../../tests/test_jules.py)                                 |
| CLI configuration | Built-in, configured-path, and user manifests resolve command arguments, roles, environment, and working directory. Executable discovery currently occurs at invocation.                                                                  | [registry](../../../../clink/registry.py), [models](../../../../clink/models.py)                                                                                                 |
| Error recovery    | Some agents/parsers turn nonzero exits or error-only output into readable results. Readable content alone does not establish success.                                                                                                     | [agents](../../../../clink/agents/base.py), [parsers](../../../../clink/parsers/base.py)                                                                                         |
| Existing tests    | Ownership tests already cover direct/server provider-free clink follow-ups and raw reply storage. A declaration-based isolation fixture exists; ordinary fixtures still inject providers/keys. Real stdio acceptance remains needed.      | [ownership tests](../../../../tests/test_conversation_ownership.py), [integration tests](../../../../tests/test_clink_integration.py), [fixtures](../../../../tests/conftest.py) |

The original five isolated CoVe checks inspected `c20d384`; they were source/documentation checks, not live inference tests. The mechanisms above were rechecked at `2d821f1` during review and correction. The earlier startup/history blockers are already resolved in this baseline.

## Chosen approach and rejected alternatives

Enable CLI-only operation through `clink`, keeping the existing API-provider architecture for API tools.

| Direction                 | User value                                                | Feasibility                                                                                                                                                                                     | Distinct benefit                                       | Decision                                              |
| ------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------- |
| CLI-only clink            | Meets the agreed conversation outcome.                    | Reuses execution, roles, parsers, and provider-independent history; adds atomic recording, selection defaults, configurable bounds, and discovery.                                              | Makes the existing CLI path reliable and discoverable. | Selected.                                             |
| CLI selection inside chat | Meets the outcome through one familiar tool.              | Requires request-dependent dispatch, conditional schemas, generation-parameter handling, and continuation changes.                                                                              | Unified API/CLI entry point.                           | Rejected: the exact `chat` tool is not a requirement. |
| CLI-backed providers      | Could extend CLI execution across model-backed workflows. | Requires async/sync bridging, truthful model capabilities, registry routing, authentication availability, and response/error adaptation. Optional API capabilities need not all be implemented. | Wider workflow reuse.                                  | Rejected: exceeds the local-conversation goal.        |

Relative effort is an engineering judgment grounded in inspected contracts, not a benchmark or proof that alternatives cannot work.

## Startup and availability

### Runtime mode

Resolve API-backed or provider-free mode from successfully configured PAL model providers at startup and retain it until restart. CLI-only conversation is a use of provider-free mode, not a requirement that a CLI be installed to start PAL. There is no new mode environment variable.

| Configuration                                                                                        | Result                                                                           |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| At least one provider passes existing bootstrap checks                                               | API-backed mode, including clink if enabled.                                     |
| Configured providers yield an empty restricted auto-mode catalogue                                   | Preserve the warning and API-backed mode; model calls retain existing errors.    |
| No providers are configured, including incomplete settings currently handled without raising         | Provider-free startup; report absent versus incomplete configuration distinctly. |
| No providers, and clink is disabled or no configured executable is found                             | Provider-free startup still succeeds; existing independent tools remain usable.  |
| Bootstrap raises, including malformed shipped/explicit registries or restriction validation failures | Preserve that error; never reinterpret it as a provider-free fallback.           |

Use `utils.env.get_env()` and the declarations in `providers/configuration.py` so `.env` semantics and the provider inventory remain authoritative, including OpenCode Go. Missing/empty values and shipped placeholders count as absent. Extend shared declaration handling for the legacy PowerShell `your_custom_api_url_here` placeholder so bootstrap and diagnostics agree. Inspect each declared credential and required endpoint independently to distinguish incomplete input from absence; respect key-optional custom endpoints. Model choices, restrictions, and optional catalogue paths do not themselves supply credentials. Incomplete settings retain their existing warning/error treatment; this feature adds no blanket startup rejection.

Existing provider credential checks are not live authentication checks. This feature adds no network probes to validate provider or CLI login state. Bad credentials that fail only on use continue to fail on use. Successful provider configurations determine API-backed mode even alongside partial settings for another provider. Provider-free availability never substitutes clink for a requested API tool; the caller must explicitly select clink.

### Executable discovery

Share a read-only executable resolver between startup and the runner. It must use the same executable token and lookup semantics as actual invocation, including explicitly configured paths. An empty executable list is a configuration error. Do not execute `--version`, a login command, or a prompt during discovery.

Finding the first executable token establishes launchability, not valid remaining arguments, accessible credentials, compatible versions, or successful inference. Invocation rechecks availability to handle an executable removed after startup. A missing selected CLI returns an error even if another CLI is installed; no substitution occurs.

### Tools, prompts, and diagnostics

Provider-free mode exposes the enabled subset of nine tools: `clink`, `version`, `listmodels`, `planner`, `tracer`, `docgen`, `challenge`, `apilookup`, and `jules`. Planner, tracer, and docgen guide local workflows; challenge and apilookup return instructions without PAL inference. Jules still validates its own service credentials on invocation. A missing CLI executable is diagnosed at clink invocation and does not remove local tools or prevent startup. Existing disabled-tool rules and essential protection for `version`/`listmodels` remain. This explicit set comes from inspecting each execution path, not `requires_model()` alone; consensus still needs PAL model providers. Challenge and apilookup do not support PAL continuation and must not be offered as continuation targets.

Keep the registered/enabled tool inventory separate from the runtime-available set. One availability decision governs tool listings, tool calls, prompt listings, and prompt retrieval. Check availability before coordination, history reads, or writes. A known but unavailable tool returns a structured error naming its need for a PAL model provider and available alternatives or setup guidance. Mention clink only if enabled, with setup guidance when no executable was found; disabled and unknown tools retain their existing distinction.

The provider-free `listmodels` result states that no PAL model providers are configured, distinguishes incomplete settings from absent ones, lists available independent tools and configured CLI names with executable-found/missing status, and leaves authentication unverified. It does not fabricate API model identities for CLIs. Identify statuses as startup observations; execution rechecks availability. Use the runtime snapshot without importing `server` into tool modules. API-mode listings retain their existing content.

Initialization describes the available independent tools and directs CLI conversation users to clink and its selector. In provider-free mode `/pal:continue` instructs the caller to continue with the previous available conversation tool and ID; CLI conversations use clink with optional explicit selection overrides. Tools without PAL continuation support are not offered as conversation targets. If clink is disabled, no instruction offers it. API-mode behavior remains unchanged. Prompt aliases cannot bypass availability filtering. With multiple configured CLIs, a new conversation requires `cli_name`; a continuation may omit it because it has saved selection state.

## Provider-independent continuation

### Ownership and dispatch

`CLinkTool.execute()` owns the complete CLI conversation path for MCP and direct callers. Replace its current `@conversation_request()` ownership with CLI-specific preparation and commit operations: leaving that decorator in place would save input before CLI success. Preserve its request/path validation, prompt-size checks, prompt-file expansion exactly once, attachment handling, and filtering of caller-supplied internal fields. Server dispatch already exempts clink from provider resolution; retain that path without adding model context or input writes. The legacy `reconstruct_thread_context()` function is not the active dispatch owner and is not changed by this feature.

Place CLI-specific preparation, snapshot, rendering, and commit logic in `clink/conversation.py`. Reuse PAL's thread/turn types, storage, shared coordinator, formatting conventions, and token estimator. Replace clink's use of the existing `CLIHistoryBudget` renderer with the complete-exchange renderer; do not introduce a provider identity. Other tools retain their invocation decorator and current history semantics.

Thread history is forwarded as text to a fresh CLI invocation. Native CLI session IDs are diagnostic metadata only. Records stay readable by existing API tools; `tool_name` keeps origin attribution. For an API-origin thread continued through clink, use the supplied CLI selection and shared thread records, not the API model as a CLI name. For a later explicit API-tool call, existing API selection rules still apply. There is no automatic cross-backend retry.

### Selection and permissions

Store the last successfully resolved CLI name and role in a namespaced `clink_defaults` entry in the thread's `initial_context`. Do not replace the original request's provenance. Resolve the current request using explicit values first, saved CLI defaults second, and ordinary new-conversation defaults last. An explicit invalid value fails. A saved role missing on an explicitly selected different CLI also fails and asks for a valid role.

Update defaults only with a committed successful exchange. An API-origin thread with no CLI defaults requires explicit CLI selection when multiple clients are configured. The schema must permit `continuation_id` instead of `cli_name` for a follow-up, while runtime validation rejects empty/invalid IDs and unresolved selections.

Never merge `allow_edits`, `editable_paths`, internal model fields, or arbitrary initial request parameters into a continuation. Edit controls are read only from the current validated request, defaulting to false/empty. Retain the existing argument buckets, supported-path checks, and execution-policy prompt described in the [completed safe-default design](../../done/clink-safe-by-default/design.md). Those controls are not an OS sandbox guarantee.

### History budget

Add optional per-client `history_token_budget`, a positive integer defaulting to **16,384**, to resolved CLI configuration. Use the existing provider-independent `utils.token_utils.estimate_tokens()` heuristic consistently. This limits the rendered history block, including labels, file/image path references, omission notices, and delimiters. It does not limit the CLI's own system prompts, current user request, tool results, or native context usage.

Build complete exchanges: a user message followed by its assistant response(s). Select the newest contiguous suffix that fits, then render it chronologically. Do not cut a message or retain half an exchange. Account for omission text and final wrappers in the final budget check. If the newest complete exchange cannot fit, fail before invoking the CLI and explain that the caller must start a shorter conversation or configure a larger budget for a future server session.

Retained turns carry path references; PAL does not reopen and embed historical file contents in the CLI history block. Current request files retain existing validation. This feature does not promise new image transport support. For legacy records with incomplete message groups, omit those groups with an explicit notice and count; a nonempty history with no complete exchange is an error. Reuse parent-chain traversal when present and retain its depth limits.

Return history metadata: estimated tokens, configured budget, retained/omitted message counts, and whether incomplete legacy records were omitted. Preserve the current raw-record contract: store the original current user message and raw parsed assistant reply, never the expanded history prompt. When output policy reduces a reply, also save the exact displayed text in `model_metadata.clink.visible_reply`. The clink history renderer uses that field when present and raw content otherwise; API consumers and transcripts retain the raw reply. The budget covers rendered history, not stored audit content. This reconciles the earlier reduced-reply-only proposal with current raw-history behavior without changing what a CLI follow-up sees.

### Successful execution and atomic recording

A readable parser result is not enough to commit an exchange. Nonzero exits remain errors, including recovered diagnostic output. Explicit CLI error envelopes, empty-response diagnostic fallbacks, Codex failed turns, and error-only events must be identified before output metadata is pruned. Preserve diagnostic content in the error response. Do not treat a permission-denied subtool warning plus an otherwise successful final answer as automatic execution failure.

For a follow-up, validate and canonicalize the UUID, acquire the shared coordinator without waiting, then take the snapshot, check two-message capacity, resolve/validate the request, and render history before launching. Extend `coordinate_conversation()` with a non-waiting option while preserving its default waiting behavior. In `handle_call_tool()`, check availability first and route clink without the outer waiting coordinator; clink acquires it exactly once inside `execute()` for both MCP and direct callers. Direct clink callers must not pre-acquire the same coordinator.

A clink call encountering any active holder or queued waiter for its ID returns a retryable busy error immediately. API/local-tool calls retain their waiting behavior, including when waiting behind clink. Hold coordination through commit and transcript emission and release it on every exit/cancellation; different IDs remain independent. Do not create a second unrelated reservation registry. Compare-and-set also detects writes from direct callers that do not participate in coordination.

After success, create both turns and updated selection defaults and commit them in one storage update. Add a compare-and-set operation to `InMemoryStorage`, using its existing lock and expiration rules: compare the exact stored snapshot, then replace it and refresh TTL atomically. A new conversation writes a complete two-turn record only after success. A failed CLI request creates/appends nothing and does not refresh TTL.

After a successful commit, preserve optional transcript recording through `utils.transcripts.record_event()`: emit the thread event once for a new thread, followed by the committed user and raw assistant events in stored turn order. A follow-up emits only its new turns, with their actual numbers and provenance. Rejected commits emit no events. Audit failure retains the existing best-effort warning and does not undo memory, suppress the answer/continuation, or rerun the CLI. These audit files do not enable durable conversation resumption.

A changed or expired snapshot, a storage failure, or concurrent updates by another tool must never be overwritten. Return the CLI reply with an explicit `history_saved=false` warning and no continuation offer. Do not rerun the CLI. Existing API writers are not being redesigned; compare-and-set protects the new CLI commit from changes observed since its snapshot.

The existing defaults remain 50 stored messages (user and assistant count separately) and a three-hour sliding TTL. Offer another exchange only when at least two slots remain, and express remaining exchanges accurately. Reject a continuation with fewer than two slots before invoking its CLI. A configured maximum below two cannot support this conversation contract and returns an actionable clink error before execution.

### Error outcomes

| Condition                                                                                                       | Result                                                          | History effect                                        |
| --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------- |
| Tool unavailable, invalid selector/path/permission, missing/expired ID, full history, oversized newest exchange | Actionable error before CLI launch                              | None                                                  |
| Clink continuation encounters a coordinator holder or waiter                                                    | Retryable busy error                                            | None                                                  |
| Executable missing, authentication/quota failure, timeout, failed/invalid CLI output                            | CLI error with diagnostic information; no API fallback          | None                                                  |
| CLI succeeds; commit succeeds                                                                                   | Reply and continuation offer if two slots remain                | User/reply pair and defaults committed; TTL refreshed |
| CLI succeeds; commit fails/conflicts/expires                                                                    | Reply plus recording warning; no offer                          | No partial write or overwrite                         |
| Memory commit succeeds; optional transcript write fails                                                         | Reply and normal continuation eligibility; audit warning logged | Memory remains committed; audit may be incomplete     |

## Authentication and assumptions

These are testable dependencies, not verified runtime guarantees:

1. The installed CLI accepts the configured noninteractive command. Validate each live smoke-test version; executable discovery alone cannot establish this.
2. Saved authentication is available to the PAL process and the CLI is configured for the intended account. Verify vendor login/configuration outside PAL without exposing credentials.
3. PAL-owned bounded history is sufficient; users accept expiration, omission notices, and loss on restart.
4. A 16,384-estimated-token history default is useful for the supported CLI configurations. Exercise its boundaries and document how to adjust it; it is not a model-capability assertion.
5. Changes to provider-free CLI handling can preserve existing API flows. Regression tests must substantiate this.

Current vendor documentation supports noninteractive saved-account use: [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [Codex authentication](https://learn.chatgpt.com/docs/auth), [Claude headless use](https://code.claude.com/docs/en/headless), and [Gemini authentication](https://geminicli.com/docs/get-started/authentication/#running-in-headless-mode).

The runner inherits only shared runtime variables and the selected CLI's configured environment allowlist, then applies explicit overrides. It does not force subscription billing. In particular, [Claude authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence) states that an `ANTHROPIC_API_KEY` present in print mode is used. Do not generalize that rule to other CLIs; their settings differ. Vendor facts were checked on 2026-10-10 and must be rechecked when writing implementation-time setup guidance.

## Not Doing

- CLI selection inside `chat` or CLI-backed providers: neither is needed for the selected conversation path.
- Additional CLI-backed PAL workflows: availability is intentionally limited in the new mode.
- Installation, login management, or billing enforcement: the local developer and vendor CLI own these.
- Shared/remote multi-user deployments: this design relies on the local user's process and account context.
- Native CLI session resumption or durable history: retain PAL's existing ephemeral conversation model.
- Multimodal parity or a new filesystem sandbox: preserve existing interfaces without claiming capabilities this change does not implement.
- Provider/catalogue redesign or dependency upgrades: use current interfaces and dependencies.

## Verification and delivery

The deterministic acceptance path launches the real MCP server with fake CLI executables, isolated configuration, and no provider credentials/registrations. Complete initialization, inspect tools/prompts, run clink, and follow up using its ID; inspect the fake CLI's received prompt for prior user and assistant markers. Fail if provider inference or API-backed model context is used in that path. Run against all three output formats.

Cover startup configuration states, tool/prompt availability, direct-call parity, history budgets and limits, complete exchange selection, selection inheritance, per-request edit permission, failed output, concurrency, atomic recording, and unchanged API paths. The [implementation plan](implementation.md#verification-matrix) defines concrete checks and fixture isolation.

Opt-in live smoke tests establish installed-version execution and context continuity under the intended authentication setup. They must not skip merely because API keys are absent, nor count a failed request as a passing/skipped success after the operator opts in. Record unmet prerequisites separately from failures. Existing integration and simulator suites remain required implementation checks in a configured environment.

For the live simulator, an absent `PAL_CLINK_LIVE_TEST` selector means the runner excludes the case before execution and reports `SKIPPED`, including an explicit individual invocation. Skipped cases are counted separately from passed/failed cases and do not prove coverage. Invalid selectors and opted-in prerequisite/request failures fail. A skip-only invocation exits successfully with a no-tests-executed summary, never a passing-test claim. The implementation plan defines the runner changes and contract tests.

Update getting-started, clink/configuration guidance, README navigation, and both setup scripts. Ship the complete continuation/discovery changes together; startup is already provider-free. Restart PAL to apply configuration/code changes. Rollback restores this baseline's provider-free startup, incremental clink recording, and existing renderer. No thread migration is needed because continuation state is process-local; transcript event compatibility remains required.

## Deferred findings and verification limits

| Finding                                                                                                                                   | Why outside this change                                                                    | Concrete next step                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Existing clink image arguments do not establish image forwarding in the runner.                                                           | The approved goal is textual conversations; no new multimodal contract is being designed.  | Track a separate image-transport change that verifies each CLI's supported mechanism and tests the actual forwarded input before documenting parity.               |
| Descendant-process cleanup is not fully established; the current runner already kills/reaps its direct child on timeout and cancellation. | No process-tree supervision redesign is part of this change.                               | Preserve and test existing child cleanup plus coordinator release; separately review descendant supervision before promising process-tree termination.             |
| API/local-tool invocation deliberately records input before execution, so failure may leave an unanswered user turn.                      | Their audit semantics are preserved; CLI success-only recording is the explicit exception. | Any future change needs a separate decision on failed-input auditing and failure/retry tests; do not apply the CLI commit policy to other tools opportunistically. |

During design review, quality checks exited before lint/tests because no virtual environment was active. No application code, installed environment, live authentication, or inference was changed by those review corrections. Their validation limits remain recorded in [implementation.md](implementation.md#design-session-validation). Implementation and current deterministic/live-check outcomes are recorded separately for [Task 1](implementation.md#task-1-execution-record-2026-10-13) and [Task 2](implementation.md#task-2-execution-record-2026-10-13).

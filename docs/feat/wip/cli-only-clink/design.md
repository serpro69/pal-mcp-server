# Design: CLI-only conversations through clink

> Status: approved for documentation; pending independent design review and implementation
> Created: 2026-10-10
> Issue: [BeehiveInnovations/pal-mcp-server#402](https://github.com/BeehiveInnovations/pal-mcp-server/issues/402)
> Source baseline: `c20d384`
> Implementation: [implementation.md](implementation.md)
> Tasks: [tasks.md](tasks.md)

## Problem and outcome

Individual developers running PAL locally want to converse through their installed, authenticated AI CLIs without configuring a model API provider in PAL. The existing `clink` tool already launches Codex, Claude Code, and Gemini CLI. Two server dependencies prevent a complete provider-free path: startup rejects missing provider configuration, and follow-up reconstruction requires provider capabilities to budget history.

Success means a developer can start PAL without provider configuration and hold an ongoing, bounded conversation through a selected CLI. PAL makes no direct model-provider inference calls in that path. A first request followed by a context-dependent request is the minimum acceptance test, not a two-message product limit.

The user approved the audience, outcome, constraints, selected approach, and all three design sections. This document records that agreement; it does not claim implementation or independent review is complete.

## Constraints

- Preserve API-backed startup, model selection, tools, and conversation behavior when providers are configured.
- Use CLIs installed and authenticated by the developer. Do not install them or manage their credentials.
- Retain existing edit opt-in controls, with authorization supplied on each request.
- Never switch from a failed CLI request to an API provider.
- Use existing dependencies and process-local conversation storage.

## Current mechanisms and evidence

These are observations of the baseline source, not declarations that the proposed behavior exists.

| Mechanism         | Current behavior                                                                                                                                                               | Source                                                                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Startup           | `main()` calls `configure_providers()` before opening MCP stdio; no recognized provider configuration raises. Auto mode additionally rejects an empty model catalogue.         | [server.py](../../../../server.py)                                                                                                                             |
| CLI execution     | `CLinkTool.requires_model()` is false; its custom `execute()` calls an asynchronous subprocess runner.                                                                         | [tools/clink.py](../../../../tools/clink.py), [runner](../../../../clink/agents/base.py)                                                                       |
| Continuation      | Server reconstruction runs before the tool's model exemption, uses the originating tool to choose model handling, and creates a provider-backed `ModelContext` even for clink. | [server.py](../../../../server.py), [model context](../../../../utils/model_context.py)                                                                        |
| Storage           | `ThreadContext` and `ConversationTurn` records are JSON in a process-local, TTL-controlled store. `add_turn()` writes one message at a time.                                   | [conversation memory](../../../../utils/conversation_memory.py), [storage](../../../../utils/storage_backend.py)                                               |
| Discovery         | Tool and prompt listings iterate enabled tools; `requires_model=False` also applies to `consensus`, which consults providers internally.                                       | [server.py](../../../../server.py), [consensus](../../../../tools/consensus.py)                                                                                |
| CLI configuration | Built-in, configured-path, and user manifests resolve command arguments, roles, environment, and working directory. Executable discovery currently occurs at invocation.       | [registry](../../../../clink/registry.py), [models](../../../../clink/models.py)                                                                               |
| Error recovery    | Some agents/parsers turn nonzero exits or error-only output into readable results. Readable content alone does not establish success.                                          | [agents](../../../../clink/agents/base.py), [parsers](../../../../clink/parsers/base.py)                                                                       |
| Existing tests    | CLI unit tests largely mock subprocesses; live tests do not prove provider-free MCP continuation. Global fixtures inject provider registrations and dummy keys.                | [CLI tests](../../../../tests/test_clink_tool.py), [integration tests](../../../../tests/test_clink_integration.py), [fixtures](../../../../tests/conftest.py) |

Five isolated CoVe verifiers inspected startup, continuation, chat adaptation, provider contracts, and CLI authentication. All completed. The findings support this approach and identify the dependencies above. They were source/documentation checks: no live inference or tests were executed during verification.

## Chosen approach and rejected alternatives

Enable CLI-only operation through `clink`, keeping the existing API-provider architecture for API tools.

| Direction                 | User value                                                | Feasibility                                                                                                                                                                                     | Distinct benefit                                   | Decision                                              |
| ------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------- |
| CLI-only clink            | Meets the agreed conversation outcome.                    | Reuses execution, roles, parsers, and PAL thread records; removes concrete startup/history dependencies.                                                                                        | Makes an existing CLI bridge independently usable. | Selected.                                             |
| CLI selection inside chat | Meets the outcome through one familiar tool.              | Requires request-dependent dispatch, conditional schemas, generation-parameter handling, and continuation changes.                                                                              | Unified API/CLI entry point.                       | Rejected: the exact `chat` tool is not a requirement. |
| CLI-backed providers      | Could extend CLI execution across model-backed workflows. | Requires async/sync bridging, truthful model capabilities, registry routing, authentication availability, and response/error adaptation. Optional API capabilities need not all be implemented. | Wider workflow reuse.                              | Rejected: exceeds the local-conversation goal.        |

Relative effort is an engineering judgment grounded in inspected contracts, not a benchmark or proof that alternatives cannot work.

## Startup and availability

### Runtime mode

Resolve one runtime mode at startup and retain it until restart. There is no new mode environment variable.

| Configuration                                                                                                          | Result                                                           |
| ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Recognized provider configuration passes existing checks                                                               | Existing API-backed mode, including clink if enabled.            |
| Explicit configuration produces no usable provider, or provider bootstrap fails existing validation/model restrictions | Preserve the error; do not enter CLI-only mode.                  |
| No provider configuration, clink enabled, at least one configured CLI executable found                                 | CLI-only mode.                                                   |
| No provider configuration and clink disabled or no configured executable found                                         | Startup error explaining API-provider setup and CLI setup paths. |

Use `utils.env.get_env()` so `.env` override semantics remain authoritative. Missing/empty values and known shipped placeholder values count as absent. In particular, the PowerShell template's `your_custom_api_url_here` must not masquerade as a custom endpoint. Inspect non-placeholder Azure key/endpoint and custom URL/key independently so incomplete pairs cannot be mistaken for absence. Ordinary model choices and restrictions do not themselves configure a provider.

Existing provider credential checks are not live authentication checks. This feature does not add network probes to validate either provider or CLI login state. Bad credentials that only fail on use continue to fail on use. Existing successful provider configurations take priority over CLI-only selection; model restrictions yielding no available models remain errors. If a valid provider is configured alongside another provider's partial settings, retain the existing bootstrap treatment of that combination rather than introducing a new global rejection.

### Executable discovery

Share a read-only executable resolver between startup and the runner. It must use the same executable token and lookup semantics as actual invocation, including explicitly configured paths. An empty executable list is a configuration error. Do not execute `--version`, a login command, or a prompt during discovery.

Finding the first executable token establishes launchability, not valid remaining arguments, accessible credentials, compatible versions, or successful inference. Invocation rechecks availability to handle an executable removed after startup. A missing selected CLI returns an error even if another CLI is installed; no substitution occurs.

### Tools, prompts, and diagnostics

CLI-only mode exposes exactly `clink`, `version`, and `listmodels`, subject to the existing disabled-tool rules. Existing essential-tool protection for `version` and `listmodels` remains. This explicit set does not infer independence from `requires_model()`.

Keep the registered/enabled tool inventory separate from the runtime-available set. One availability decision governs tool listings, tool calls, prompt listings, and prompt retrieval. Check availability before processing a continuation or modifying history. A known but unavailable tool returns a structured error naming the need for an API provider and the available clink path; a disabled or unknown tool retains its existing distinction.

The CLI-only `listmodels` result states that no API models are configured, lists configured CLI names with executable-found/missing status, and explicitly leaves authentication unverified. It does not fabricate API model identities for CLIs. Identify the status as a startup observation; execution rechecks it. Use the runtime snapshot without importing `server` into tool modules. API-mode listings retain their existing content.

The initialization instructions direct CLI-only users to `clink` and its CLI selector, not an empty model list. `/pal:continue` uses clink in CLI-only mode and preserves its existing API-mode behavior. Prompt aliases cannot bypass availability filtering. With multiple configured CLIs, a new conversation requires `cli_name`; a continuation may omit it because it has saved selection state.

## Provider-independent continuation

### Ownership and dispatch

`CLinkTool.execute()` owns the complete CLI conversation path for MCP and direct callers. Server dispatch checks tool availability, then routes clink before the legacy `reconstruct_thread_context()` path. It must not append a user turn, resolve a model, or inject a `ModelContext` for that request.

Place the CLI-specific snapshot, rendering, and commit logic in `clink/conversation.py`. Reuse PAL's thread/turn types, storage, formatting conventions, and token estimator. Do not create a dummy provider or pretend the CLI has an API model's capacity. API-tool reconstruction remains on its current path.

Thread history is forwarded as text to a fresh CLI invocation. Native CLI session IDs are diagnostic metadata only. Records stay readable by existing API tools; `tool_name` keeps origin attribution. For an API-origin thread continued through clink, use the supplied CLI selection and shared thread records, not the API model as a CLI name. For a later explicit API-tool call, existing API selection rules still apply. There is no automatic cross-backend retry.

### Selection and permissions

Store the last successfully resolved CLI name and role in a namespaced `clink_defaults` entry in the thread's `initial_context`. Do not replace the original request's provenance. Resolve the current request using explicit values first, saved CLI defaults second, and ordinary new-conversation defaults last. An explicit invalid value fails. A saved role missing on an explicitly selected different CLI also fails and asks for a valid role.

Update defaults only with a committed successful exchange. An API-origin thread with no CLI defaults requires explicit CLI selection when multiple clients are configured. The schema must permit `continuation_id` instead of `cli_name` for a follow-up, while runtime validation rejects empty/invalid IDs and unresolved selections.

Never merge `allow_edits`, `editable_paths`, internal model fields, or arbitrary initial request parameters into a continuation. Edit controls are read only from the current validated request, defaulting to false/empty. Retain the existing argument buckets, supported-path checks, and execution-policy prompt described in the [completed safe-default design](../../done/clink-safe-by-default/design.md). Those controls are not an OS sandbox guarantee.

### History budget

Add optional per-client `history_token_budget`, a positive integer defaulting to **16,384**, to resolved CLI configuration. Use the existing provider-independent `utils.token_utils.estimate_tokens()` heuristic consistently. This limits the rendered history block, including labels, file/image path references, omission notices, and delimiters. It does not limit the CLI's own system prompts, current user request, tool results, or native context usage.

Build complete exchanges: a user message followed by its assistant response(s). Select the newest contiguous suffix that fits, then render it chronologically. Do not cut a message or retain half an exchange. Account for omission text and final wrappers in the final budget check. If the newest complete exchange cannot fit, fail before invoking the CLI and explain that the caller must start a shorter conversation or configure a larger budget for a future server session.

Retained turns carry path references; PAL does not reopen and embed historical file contents in the CLI history block. Current request files retain existing validation. This feature does not promise new image transport support. For legacy records with incomplete message groups, omit those groups with an explicit notice and count; a nonempty history with no complete exchange is an error. Reuse parent-chain traversal when present and retain its depth limits.

Return history metadata: estimated tokens, configured budget, retained/omitted message counts, and whether incomplete legacy records were omitted. Store only the original current user message and the actual user-visible reply, never the recursively expanded history prompt. Preserve the existing output-size policy; when it summarizes/truncates a CLI reply, that reduced reply is what the user and future turns see.

### Successful execution and atomic recording

A readable parser result is not enough to commit an exchange. Nonzero exits remain errors, including recovered diagnostic output. Explicit CLI error envelopes, empty-response diagnostic fallbacks, Codex failed turns, and error-only events must be identified before output metadata is pruned. Preserve diagnostic content in the error response. Do not treat a permission-denied subtool warning plus an otherwise successful final answer as automatic execution failure.

For a follow-up, validate the ID, take a thread snapshot, check room for two messages, resolve/validate the current request, and render history before launching the CLI. Reserve the thread against another simultaneous clink continuation; reject overlap with a retryable busy error rather than queue it. The reservation is process-local, shared across clink instances, and released on every exit/cancellation. Other threads remain independent.

After success, create both turns and updated selection defaults and commit them in one storage update. Add a compare-and-set operation to `InMemoryStorage`, using its existing lock and expiration rules: compare the exact stored snapshot, then replace it and refresh TTL atomically. A new conversation writes a complete two-turn record only after success. A failed CLI request creates/appends nothing and does not refresh TTL.

A changed or expired snapshot, a storage failure, or concurrent updates by another tool must never be overwritten. Return the CLI reply with an explicit `history_saved=false` warning and no continuation offer. Do not rerun the CLI. Existing API writers are not being redesigned; compare-and-set protects the new CLI commit from changes observed since its snapshot.

The existing defaults remain 50 stored messages (user and assistant count separately) and a three-hour sliding TTL. Offer another exchange only when at least two slots remain, and express remaining exchanges accurately. Reject a continuation with fewer than two slots before invoking its CLI. A configured maximum below two cannot support this conversation contract and returns an actionable clink error before execution.

### Error outcomes

| Condition                                                                                                       | Result                                                 | History effect                                        |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------- |
| Tool unavailable, invalid selector/path/permission, missing/expired ID, full history, oversized newest exchange | Actionable error before CLI launch                     | None                                                  |
| Same thread already in a clink request                                                                          | Retryable busy error                                   | None                                                  |
| Executable missing, authentication/quota failure, timeout, failed/invalid CLI output                            | CLI error with diagnostic information; no API fallback | None                                                  |
| CLI succeeds; commit succeeds                                                                                   | Reply and continuation offer if two slots remain       | User/reply pair and defaults committed; TTL refreshed |
| CLI succeeds; commit fails/conflicts/expires                                                                    | Reply plus recording warning; no offer                 | No partial write or overwrite                         |

## Authentication and assumptions

These are testable dependencies, not verified runtime guarantees:

1. The installed CLI accepts the configured noninteractive command. Validate each live smoke-test version; executable discovery alone cannot establish this.
2. Saved authentication is available to the PAL process and the CLI is configured for the intended account. Verify vendor login/configuration outside PAL without exposing credentials.
3. PAL-owned bounded history is sufficient; users accept expiration, omission notices, and loss on restart.
4. A 16,384-estimated-token history default is useful for the supported CLI configurations. Exercise its boundaries and document how to adjust it; it is not a model-capability assertion.
5. Changes to provider-free CLI handling can preserve existing API flows. Regression tests must substantiate this.

Current vendor documentation supports noninteractive saved-account use: [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [Codex authentication](https://learn.chatgpt.com/docs/auth), [Claude headless use](https://code.claude.com/docs/en/headless), and [Gemini authentication](https://geminicli.com/docs/get-started/authentication/#running-in-headless-mode).

The runner inherits the PAL environment and applies configured overrides. It does not force subscription billing. In particular, [Claude authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence) states that an `ANTHROPIC_API_KEY` present in print mode is used. Do not generalize that rule to other CLIs; their settings differ. These facts were checked on 2026-10-10 and must be rechecked when writing implementation-time setup guidance.

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

Update getting-started, clink/configuration guidance, README navigation, and both setup scripts. Ship the new mode atomically with continuation and availability changes; removing the startup gate alone is insufficient. Restart PAL to apply configuration/code changes. Rolling back the implementation restores the old provider requirement; no persisted data migration is needed because threads are process-local.

## Deferred findings and verification limits

| Finding                                                                                                                                                 | Why outside this change                                                                   | Concrete next step                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Existing clink image arguments do not establish image forwarding in the runner.                                                                         | The approved goal is textual conversations; no new multimodal contract is being designed. | Track a separate image-transport change that verifies each CLI's supported mechanism and tests the actual forwarded input before documenting parity.                         |
| CLI cancellation/process-tree cleanup is not fully established by source inspection; the runner has timeout cleanup but no dedicated cancellation path. | No process-supervision redesign is part of this mode.                                     | Review cancellation semantics separately; ensure this change releases its own thread reservation in `finally` without claiming the child has stopped.                        |
| Existing API continuation can append a user turn before later reconstruction fails.                                                                     | API history semantics are explicitly preserved.                                           | A separate change should make API continuation transactional and test failure/retry behavior; do not extend this feature's CLI commit helper to API calls opportunistically. |

Design-session quality check: `./code_quality_checks.sh` exited before lint/tests because no virtual environment exists. `workon` and `mkvirtualenv` are unavailable, and there is no `logs/` directory. No application code was changed, environment installed, live authentication inspected, or inference requested. Document validation is recorded in [implementation.md](implementation.md#design-session-validation); implementation and its test results remain pending.

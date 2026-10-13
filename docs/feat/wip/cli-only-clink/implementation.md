# Implementation: CLI-only conversations through clink

> Status: Task 1 complete; Tasks 2–14 pending
> Created: 2026-10-10
> Design: [design.md](design.md)
> Tasks: [tasks.md](tasks.md)
> Baseline: `2d821f1`
> Review: [review.md](review.md)

## Read first

Implement the approved [design](design.md), not a CLI backend for `chat`. Read `AGENTS.md` and applicable nested instructions before changes. The project is Python, uses Pydantic request/configuration models, exposes MCP over stdio, and keeps conversation state in one process. No dependency change is planned.

`server.py` owns bootstrap, availability, dispatch, and prompts. `tools/clink.py` currently uses the admission/persistence decorator in `utils/conversation_context.py`; MCP calls also use `utils/conversation_coordination.py`. `clink/registry.py` resolves configuration; agents execute subprocesses and parsers interpret output. Shared thread types and transcript hooks are in `utils/conversation_memory.py`, with the synchronized store in `utils/storage_backend.py`. Provider declarations in `providers/configuration.py` already drive bootstrap and test isolation.

Do not use `requires_model()` as a runtime-availability test or reuse `ModelContext` for CLI history. Do not use inherited continuation helpers that create an empty thread and append messages separately for the new CLI commit path. Preserve API-tool behavior.

## Implementation contracts

These describe the finished feature. Tasks 3–5 below deliberately separate atomic recording, bounded history/selection, and non-waiting coordination; each task states its intermediate contract. Ship the complete feature only after final verification.

### CLI result classification

Extend `ParsedCLIResponse` with a backward-compatible success/error indication rather than teaching `CLinkTool` to reparse vendor JSON. Preserve diagnostics. A nonzero return code independently makes the call a failure, even when recovery produced readable output. Check both before metadata pruning, success formatting, or assistant persistence. Tasks 1–2 establish classification under the current invocation wrapper, which still records the user input first. Task 3 replaces that ownership and adds the final no-write/no-continuation-on-failure guarantees for all three parsers; those guarantees are not prerequisites for completing Tasks 1–2.

Gemini's explicit top-level error and empty-response fallback are failures. Claude's `is_error`, failed result subtype, and stderr-only fallback are failures. Codex error-only output and `turn.failed` are failures; an error event followed by a successful final response can remain a successful result with diagnostic metadata. Test permission-denied subtool warnings separately from terminal failure. Keep parsing useful diagnostics; change their classification, not their visibility.

### Request-local conversation service

Create `clink/conversation.py` with explicit operations for loading a validated snapshot, resolving saved CLI defaults, rendering bounded history, reserving a thread, and committing an exchange. Use request-local values across awaits; do not rely on mutable `CLinkTool._current_arguments` or `_model_context` for storage/selection after subprocess execution. Retain the existing prompt `ContextVar` behavior.

Remove `@conversation_request()` from clink when its replacement is ready; do not leave eager admission around the new commit path. Preserve Pydantic request validation, current path/permission and prompt-size checks, one-time prompt-file expansion, attachment normalization/reference handling, and filtering of untrusted internal fields. Keep these operations in the CLI service, using existing non-persisting helpers where appropriate. Other tools retain the existing decorator unchanged. Do not depend on `current_invocation()`/`invocation_history()` to prepare clink after removal, and do not call `_parse_response()` if it would invoke incremental persistence.

At the MCP boundary, check tool availability before acquiring a coordinator or reading history. `_dispatch_tool()` already exempts clink from model resolution; preserve that branch and its fresh instance. Inside clink, validate the ID, acquire coordination, load a snapshot, combine only saved CLI/role defaults with current values, validate/prepare the request, render history, execute and classify the CLI, apply output policy, then commit. The legacy `reconstruct_thread_context()` is not on this dispatch path; neither remove it nor introduce a bypass around it as the supposed fix.

Read the exact stored JSON snapshot for compare-and-set. Validate the UUID and deserialize with existing thread types. Keep the original `initial_context` and origin tool; add/update only its namespaced `clink_defaults` on successful commit. Do not copy internal `_model_context`, `_remaining_tokens`, expanded history, or edit authorization from an old request.

Extend `coordinate_conversation(thread_id, *, wait=True)` in `utils/conversation_coordination.py`. Preserve the existing waiting path by default. With `wait=False`, reject an ID with any holder or queued waiter before suspending; otherwise acquire the same entry atomically on the event loop. Keep reference cleanup on every error/cancellation and do not retain idle entries. Validate/canonicalize clink IDs before acquisition.

In `handle_call_tool()`, retain progress/error annotation but skip the outer waiting coordinator for clink only. `CLinkTool.execute()` acquires with `wait=False`, so MCP and direct clink callers use one owner and direct callers must not wrap it in another coordinator. API/local-tool calls continue to wait, including behind clink; clink rejects immediately behind either kind of holder/waiter. Hold the coordinator through commit and audit emission. Different IDs remain independent. Compare-and-set detects intervening uncoordinated writes; it does not redesign other tools' writers.

Add `compare_and_setex` to `InMemoryStorage`: under its existing lock, check expiry and compare the expected serialized value, then write the replacement and TTL. Expected absence supports first creation; an expired existing thread fails an update and is never resurrected. Return a boolean conflict result; surface exceptions distinctly. Construct a new two-turn `ThreadContext` in memory before first write; do not call `create_thread()` followed by `add_turn()` twice.

For follow-ups, reject insufficient capacity before CLI execution and check it again at commit. On successful commit, compute remaining complete exchanges as integer pairs of available message slots. On a commit failure/conflict, return the generated reply with `history_saved=false`, a clear warning, and no offer; never automatically repeat inference. Preserve the existing answer-survives-storage-failure behavior while replacing the incremental writes.

### Raw records and transcript compatibility

Store original user text and raw parsed assistant content in `ConversationTurn.content`. When presentation reduces the answer, save the displayed text under `ConversationTurn.model_metadata["clink"]["visible_reply"]`, merging it with existing provenance/usage metadata. The CLI renderer prefers this saved text; otherwise it uses raw content. Other consumers keep their existing raw-history behavior. Retain `test_clink_stores_raw_output_before_presentation_limit` and add assertions for displayed-text history; never store the expanded prompt.

After successful CAS, use `utils.transcripts.record_event()` for the same event schema as `create_thread()`/`add_turn()`: one thread event for first creation, then the new user and raw assistant events with committed turn numbers, timestamps, files/images, and model/tool provenance. Follow-ups emit only their two new events. Emit while coordination is held, with no await between memory commit and event emission, so cooperating writers preserve order. Emit nothing for failed/conflicting commits. The transcript helper remains best-effort: failure logs a warning, leaves memory and continuation eligibility intact, and never repeats CLI work. Disabled transcripts create no audit artifacts.

### History renderer and configuration

Begin with the approved default in `clink/constants.py`; expose per-client `history_token_budget` in the configuration slice. Add the field to raw and resolved config models with a positive-integer default of 16,384, and pass it through registry resolution. Reject zero, negative, fractional, boolean, and malformed values instead of silently coercing an invalid budget. Do not add a request-level knob or a provider identity.

The renderer groups a user turn with subsequent assistant turn(s) until the next user turn, traverses parent chains with the existing depth bound, and selects the newest contiguous suffix of complete groups. For malformed legacy groups, report omissions; do not repair stored records. A nonempty thread with no complete exchange is an error. Test multi-assistant groups and incomplete trailing user turns.

Use `utils.token_utils.estimate_tokens()` without provider lookups. Include headers, path references, omission annotations, and closing guidance in the budget; recompute the rendered result until it fits. Never trim individual messages. The latest complete group not fitting causes a pre-execution error. The current prompt and CLI-generated context are outside this history-only budget, which must be stated in documentation and diagnostics.

The prompt includes retained chronological history followed by the new user request exactly once. Historical files/images appear as references; no historical file content is read by this renderer. Current files retain validation and prompt-file handling. Use the displayed-text projection specified in [Raw records and transcript compatibility](#raw-records-and-transcript-compatibility), preserving raw storage and output truncation metadata.

### Runtime capability state

Create `utils/runtime_capabilities.py` for a small runtime record and pure availability decisions, avoiding a `tools/listmodels.py` import of `server.py`. Record API-backed/provider-free mode, successfully configured providers, absent/incomplete setup diagnostics, CLI executable statuses, and enabled/available tools. Initialize after existing bootstrap succeeds. Tests explicitly initialize/reset this state rather than relying on import order.

`configure_providers()` already permits missing providers and an empty restricted auto-mode catalogue. Preserve those warning paths, provider registration/cleanup, and existing validation failures. Expose its configured-provider result to runtime initialization rather than guessing mode from the presence of an input variable. Any registered provider keeps API-backed mode even if restrictions yield no available models. Incomplete settings that currently warn remain nonfatal; diagnostics distinguish them from genuine absence. Malformed registries and other actual bootstrap exceptions remain errors, never a signal to fall back to clink.

Derive diagnostics from `provider_configurations()` and each declaration's `key_env`, `required_env`, `key_optional`, and placeholder rules. This includes OpenCode Go automatically. Inspect keys and required endpoints independently; a key-optional custom endpoint works without a key, while a lone custom key is incomplete. Do not use all `environment_names()` values as credentials: restrictions, aliases, optional catalogue paths, and default-model choices do not configure a provider.

Extend `ProviderConfiguration` with per-required-setting placeholder metadata and a shared effective-value lookup; declare `CUSTOM_API_URL=your_custom_api_url_here` on `CustomProvider`. Use that lookup in `is_configured()`, constructor arguments, and diagnostics so old PowerShell templates cannot register a fake endpoint. Keep existing key placeholders and `get_env()` semantics, and return real values unchanged. Do not introduce a second provider-name/key list in runtime state or tests. Child-only `GOOGLE_API_KEY`/`ANTHROPIC_API_KEY` do not become PAL provider settings.

Create `clink/availability.py` for executable resolution using the runner's current lookup semantics. Startup and invocation share that resolver. Do not run commands to probe login/version and do not print full arguments or environment values. A wrapper executable being found does not validate its script/arguments. Keep other runner behavior unchanged.

Retain an enabled-tool inventory after `DISABLED_TOOLS` filtering. Provider-free mode admits its intersection with clink/version/listmodels/planner/tracer/docgen/challenge/apilookup/jules; API-backed mode retains all enabled tools. No executable is required for startup. Missing clink executables and Jules service credentials fail only their selected invocation. Apply the availability guard before coordination/history processing, preserve disabled/unknown distinctions, and never substitute a different backend. Mode changes require restart; clink invocation rechecks its selected executable.

### Discovery and setup

Use the same runtime availability in `handle_list_tools`, `handle_list_prompts`, and `handle_get_prompt`, including aliases looked up through `PROMPT_TEMPLATES`. Provider-free `/pal:continue` directs the caller to the previous available conversation tool and ID; for a CLI conversation this is clink with an optional selector override. Do not offer tools without PAL continuation support, instruct planner/tracer users to switch to clink, or offer clink when disabled. API-mode instructions remain unchanged. New clink calls require selection when multiple CLIs are configured; continuations may use saved selection.

Test all nine independent tools in provider-free discovery and dispatch, not only the tools mentioned in the initial review. Challenge/apilookup must return their local instruction payloads, docgen must complete/continue without expert inference, and consensus must remain unavailable. Include disabled-tool and prompt-alias cases. Do not offer challenge/apilookup as continuation targets.

Add a provider-free result in `ListModelsTool.execute()` using the runtime record: no PAL model providers, absent/incomplete setup hints, independent tool names, configured CLIs and executable status as of startup, and authentication unverified. API-mode formatting is preserved, including an empty catalogue. Startup guidance describes the actual capabilities without requiring an installed CLI or an unavailable API model.

Update Bash and PowerShell setup messages to present both setup paths. They may recommend installation/login but must not invoke a CLI for authentication detection. Replace active placeholder `CUSTOM_API_URL` assignments in generated PowerShell templates with an empty/commented value; test the classifier against old placeholder files. Avoid copying new credential values or changing user login configuration. Check both Python and Docker PowerShell setup branches, which have separate template/message sites.

### Optional live simulator selection

Use pre-execution exclusion with separate skip reporting, retaining boolean results for executed scenarios. The new live scenario declares `live_selector_env = "PAL_CLINK_LIVE_TEST"` and accepted values `codex`, `claude`, `gemini`; keep it out of the existing `--ci` selection. The runner evaluates this metadata only when the scenario is a candidate for the requested suite.

With no selector, record the reason in a separate `skipped_tests` mapping and exclude the case from execution and the passed/failed denominator. Do not invoke its `run()`, open a CLI session, or return a truthy skip sentinel as its result. An explicit `--individual clink_cli_only` without the selector has the same SKIPPED outcome and explains how to opt in. An invalid selector records a failure before live work; a valid selector executes the case and missing prerequisites/request errors fail normally.

Update `CommunicationSimulator` selection, `_run_selected_tests()`, `run_individual_test()`, and `print_test_summary()`, plus the module-level suite/individual wrappers. Report passed, failed, and skipped separately. A skip-only request exits 0 with “No tests executed” and its skip reason; it must not print PASSED or claim successful coverage. Mixed suites retain the exit status of executed cases, with skips reported separately. Preserve existing failure, cleanup, interruption, and selection-mode behavior. Test these outcomes with synthetic scenarios in `tests/test_simulator_sessions.py`; no live inference is needed to verify the runner contract.

## Ordered implementation slices

Tasks map to testable user-visible paths. The former Task 3 is split into Tasks 3–5, and provider declaration work and live-test runner work have their own slices. Mechanical registration/fixture wiring does not increase a size tag. All final contracts above remain required; the staging below prevents a task from claiming a later task's guarantees.

### Task 1: Gemini and Claude execution failures

Files: `clink/parsers/base.py`, `clink/parsers/gemini.py`, `clink/parsers/claude.py`, `tools/clink.py`, new `tests/test_clink_execution_errors.py`.

1. Add a backward-compatible parsed failure indication and classify terminal errors/diagnostic fallbacks; preserve successful answers with subtool warnings.
2. Reject failed parsed results or nonzero exits before success formatting/assistant persistence. Existing eager user/thread writes and error continuation metadata remain until Task 3.
3. Verify: `python -m pytest tests/test_clink_execution_errors.py tests/test_clink_gemini_agent.py tests/test_clink_claude_agent.py -q`; failed calls retain diagnostics, save no assistant reply, and never use API fallback.

### Task 2: Codex terminal failures

Files: `clink/parsers/codex.py`, `tests/test_clink_execution_errors.py`.

1. Classify error-only streams, `turn.failed` with partial text, recovered nonzero exits, and successful final output with warnings.
2. Verify: `python -m pytest tests/test_clink_execution_errors.py tests/test_clink_codex_agent.py tests/test_clink_parsers.py -q`. As in Task 1, assert classification/no assistant persistence now; Task 3 owns no-write/no-continuation guarantees.

### Task 3: Commit sequential CLI exchanges atomically

Files: new `clink/conversation.py`, `tools/clink.py`, `utils/storage_backend.py`, new `tests/test_clink_continuation.py`. Reuse existing transcript hooks and ownership/storage regression suites.

1. Replace clink's eager invocation decorator with request-local validation/preparation and a CAS commit of the raw user/reply pair. Preserve prompt-file, path, size, attachment, internal-field, output-limit, and permission handling.
2. Support sequential direct/MCP follow-ups using the existing provider-independent history renderer on a snapshot, without historical file-content loading; append current input exactly once. Retain current explicit CLI selection. Do not change the history algorithm, saved selection, or coordinator policy in this slice.
3. Preserve post-commit thread/turn transcript events, raw reply storage, two-slot capacity, TTL/conflict behavior, and answer-survives-storage-failure handling.
4. Verify: `python -m pytest tests/test_clink_continuation.py tests/test_clink_execution_errors.py tests/test_clink_safe_default.py tests/test_conversation_ownership.py tests/test_conversation_storage_errors.py tests/test_transcripts.py tests/test_transcript_resources.py -q`.

Intermediate contract: MCP still uses its existing waiting coordinator; direct same-thread concurrency retains the existing caller-coordination convention. CAS rejects intervening writes, but immediate busy errors are not promised until Task 5. Complete-exchange budgeting, displayed-history projection, and saved selection arrive in Task 4. All parser failures now leave memory/TTL unchanged and carry no continuation metadata.

### Task 4: Bound history and restore CLI selection

Files: `clink/conversation.py`, `tools/clink.py`, `tests/test_clink_continuation.py`, new `tests/test_clink_history_budget.py`; the default in `clink/constants.py` is mechanical wiring.

1. Replace the temporary reuse of the existing renderer with complete-exchange selection at the default 16,384-token budget. Account for wrappers, notices, references, legacy incomplete groups, parent depth, and oversized newest exchanges.
2. Preserve raw replies while saving reduced displayed text in `model_metadata.clink.visible_reply`; render that projection for subsequent clink calls.
3. Persist successful CLI/role defaults and relax the schema for continuation-only selection. Preserve explicit overrides and current-request-only edit authorization.
4. Verify: `python -m pytest tests/test_clink_history_budget.py tests/test_clink_continuation.py tests/test_clink_tool.py tests/test_clink_safe_default.py tests/test_conversation_ownership.py tests/test_chat_cross_model_continuation.py -q`. Per-client configuration is added in Task 6; same-thread policy remains unchanged until Task 5.

### Task 5: Reject overlapping CLI continuations immediately

Files: `utils/conversation_coordination.py`, `server.py`, `tools/clink.py`, new `tests/test_clink_concurrency.py`.

1. Add non-waiting admission to the shared coordinator and skip the outer MCP waiting lock for clink only. Acquire once inside clink through commit/audit for both MCP and direct callers.
2. Verify immediate busy errors for holders/waiters, mixed-tool ordering, unchanged API/local queuing, distinct-thread overlap, request-local state, and cleanup on errors/cancellation. Direct clink callers now use its internal coordinator rather than pre-acquiring it.
3. Verify: `python -m pytest tests/test_clink_concurrency.py tests/test_clink_continuation.py tests/test_conversation_concurrency.py tests/test_clink_progress.py -q`. The final coordination contract now applies; Task 12 adds the real-stdio busy check.

### Task 6: Configure history budgets per CLI

Files: `clink/models.py`, `clink/registry.py`, `clink/conversation.py`, `tests/test_clink_history_budget.py`, `docs/configuration.md`.

1. Expose/forward `history_token_budget` with strict positive-integer validation and the approved default. Select the current client's budget during preparation.
2. Document history-only heuristic accounting and restart effects. Verify explicit lower/higher budgets alter retained complete exchanges and invalid values fail configuration.
3. Verify: `python -m pytest tests/test_clink_history_budget.py tests/test_clink_continuation.py tests/test_clink_tool.py -q`.

### Task 7: Classify provider settings from shared declarations

Files: `providers/configuration.py`, `providers/custom.py`, `tests/test_provider_configuration.py`.

1. Add declaration-derived absent/incomplete/complete-input diagnostics and shared effective-value handling for required-setting placeholders. Declare the old PowerShell custom-URL placeholder on CustomProvider; preserve real values, key placeholders, and key-optional endpoints.
2. Verify OpenCode-Go-only setup, absent/placeholder-only input, partial Azure/custom pairs, valid-plus-partial combinations, and non-credential restrictions/catalogue paths. Existing bootstrap exceptions and nonfatal warnings retain their treatment.
3. Verify: `python -m pytest tests/test_provider_configuration.py tests/test_provider_configuration_errors.py tests/test_custom_provider.py -q`. Runtime mode still derives from actual successful registration in Task 8, not complete-input diagnostics alone.

### Task 8: Preserve provider-free startup and call availability

Files: new `utils/runtime_capabilities.py`, new `clink/availability.py`, `server.py`, new `tests/test_cli_only_startup.py`; forwarding runner executable lookup to the shared helper is mechanical.

1. Initialize runtime state from actual provider bootstrap results and Task 7 diagnostics. Preserve no-provider/no-CLI/clink-disabled startup, fatal bootstrap errors, and warnings for empty restricted catalogues.
2. Share executable lookup with the runner without running probes. Compute the enabled subset of all nine independent tools in provider-free mode; API-backed mode retains enabled tools.
3. Guard calls before coordination/history reads, preserving progress/error annotation, and run a first fake-CLI reply. Selected CLI/Jules prerequisites fail only their own calls.
4. Verify: `python -m pytest tests/test_cli_only_startup.py tests/test_server.py tests/test_disabled_tools.py tests/test_provider_configuration.py tests/test_auto_mode_custom_provider_only.py -q`. Coherent listings and prompt guidance are completed in Task 9 before release.

### Task 9: Discover and resume all available independent tools

Files: `server.py`, `tools/listmodels.py`, new `tests/test_cli_only_discovery.py`, `tests/test_tool_surface.py`; README navigation is mechanical.

1. Apply the same availability decision to tool/prompt listings and aliases. Parameterize no-provider discovery/dispatch coverage over all nine tools; include local challenge/apilookup payloads, docgen first/follow-up/completion, Jules with synthetic service transport, disabled tools, and unavailable consensus.
2. Make initialization, `listmodels`, and `/pal:continue` mode-aware. Preserve API-mode behavior; include absent/incomplete hints and unverified executable/auth observations. Continuation targets must support PAL history, so exclude challenge/apilookup/Jules as targets.
3. Verify: `python -m pytest tests/test_cli_only_discovery.py tests/test_server.py tests/test_disabled_tools.py tests/test_tool_surface.py tests/test_challenge.py tests/test_jules.py tests/test_listmodels.py -q`. Update intended surface changes explicitly without weakening preserved capability assertions.

### Task 10: Document local setup and usage

Files: `run-server.sh`, `docs/getting-started.md`, `docs/tools/clink.md`, `README.md`.

1. Document provider-free setup, CLI authentication prerequisites, first request/follow-up, permissions, limits, diagnostics, and failures against the completed runtime contracts.
2. Correct setup warnings and stale Gemini telemetry examples; check `bash -n run-server.sh`, relative links, shipped arguments, and primary vendor authentication guidance.
3. Completion here requires source/example/link consistency and script syntax. The actual setup walkthrough against the Task 12 acceptance harness belongs to Task 14, so documentation can proceed in parallel with that harness without an undeclared completion dependency.

### Task 11: Align PowerShell setup

Files: `run-server.ps1`, `docs/getting-started.md` (Windows clarification only if needed).

1. Update both setup branches' messages/templates and stop generating active custom-URL placeholders. Verify old placeholder and real-endpoint classification from Task 7.
2. Parse PowerShell syntax in a supported environment without running installation, MCP configuration mutations, or login probes. Task 14 owns the final setup walkthrough.

### Task 12: Prove the real MCP conversation

Files: new `tests/test_cli_only_mcp.py` and isolated launcher/fake-CLI fixture wiring.

1. Launch the actual server with isolated dotenv/provider/user-CLI state and normal manifest overrides. Test initialization, discovery, first reply, context-dependent follow-up, and unavailable-tool errors for all three vendor parsers.
2. Inspect subprocess input/argv: prior input/reply once, current edit flags only, and provider resolution/inference prohibited. Hold one fake continuation open and require a second same-ID call to return busy before release.
3. Verify: `python -m pytest tests/test_cli_only_mcp.py -q`. Run without installed vendor CLIs or credentials; this remains deterministic non-integration coverage.

### Task 13: Add optional live checks with truthful skip reporting

Files: `communication_simulator_test.py`, new `simulator_tests/test_clink_cli_only.py`, `tests/test_simulator_sessions.py`, `tests/test_clink_integration.py`; registry/exports in `simulator_tests/__init__.py` are mechanical.

1. Implement the [pre-execution selection/skip contract](#optional-live-simulator-selection) in the simulator runner and its module-level summaries/wrappers. Use synthetic scenarios for no selector, invalid selector, selected failures, mixed suites, and all-skipped/individual exit-status tests.
2. Add provider-free live first/follow-up checks for the selected CLI, using Task 12's isolation approach while preserving the intended CLI account's authentication access. The new simulator scenario owns its session/configuration isolation and temporary workspace; do not change developer login files. With a selector, missing executables/authentication/compatible flags are failures, not skips.
3. Verify deterministic runner checks with `python -m pytest tests/test_simulator_sessions.py -q`. Run opted-in `tests/test_clink_integration.py` and `python communication_simulator_test.py --individual clink_cli_only` only with the explicit selector and required installed/authenticated CLI; record version/auth prerequisites and actual outcomes.

### Task 14: Final verification and setup walkthrough

1. Run `$kk:test`, `./code_quality_checks.sh`, targeted MCP tests, script syntax checks, integration script, quick simulator suite, and individual CLI live checks in the configured environment. Missing prerequisites are unmet checks, never passing coverage.
2. Walk the Bash/PowerShell setup guidance and request examples against the completed Task 12 fake-CLI MCP harness; this explicitly depends on Tasks 10–12. Verify the Task 13 skip/failure summaries and live evidence requirements.
3. Run `$kk:document`, `$kk:review-code` with Python language input, and `$kk:review-spec` over all feature docs. Validate examples/links and record durable follow-ups for any remaining implementation findings before feature completion.

## Verification matrix

| Concern                  | Required observation                                                                                                                                                             | Primary test location                                                                                                                         |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Provider-free start      | Initialization succeeds without providers, without executables, or with clink disabled; a fake executable enables actual CLI calls.                                              | `tests/test_cli_only_startup.py`, `tests/test_cli_only_mcp.py`                                                                                |
| Provider diagnostics     | Declaration-derived absence/incomplete states include OpenCode Go and old placeholders; fatal bootstrap errors remain fatal and empty catalogues remain warnings.                | `tests/test_cli_only_startup.py`                                                                                                              |
| Availability             | Listings/aliases/calls/continue agree, preserve all nine independent tools, and reject unavailable tools before coordination or history reads.                                   | `tests/test_cli_only_discovery.py`, `tests/test_tool_surface.py`, `tests/test_jules.py`                                                       |
| Real transport           | Two requests through one MCP stdio process preserve prompt/reply markers for every CLI format.                                                                                   | `tests/test_cli_only_mcp.py`                                                                                                                  |
| Direct parity            | Direct `CLinkTool.execute()` does not need a server-prepared history prompt.                                                                                                     | `tests/test_clink_continuation.py`                                                                                                            |
| History bounds           | Complete newest exchanges fit including wrappers; impossible newest exchange errors; references cause no historical file reads.                                                  | `tests/test_clink_history_budget.py`                                                                                                          |
| Storage                  | Successful pair is one write; failure does not append or refresh TTL; expiration/conflict is not overwritten.                                                                    | `tests/test_clink_continuation.py`                                                                                                            |
| Concurrency              | MCP/direct clink calls reject busy holders/waiters immediately; API/local calls queue; mixed-tool and distinct-thread cases preserve ordering and release entries on every exit. | `tests/test_clink_concurrency.py`, `tests/test_conversation_concurrency.py`                                                                   |
| Audit and presentation   | CAS emits ordered raw transcript events only after commit; displayed-text history remains bounded; disabled/failed audit does not alter memory or repeat execution.              | `tests/test_clink_continuation.py`, `tests/test_conversation_ownership.py`, `tests/test_transcripts.py`, `tests/test_transcript_resources.py` |
| Selection/permissions    | Saved successful CLI/role defaults work; explicit overrides win; initial edit authorization is never inherited.                                                                  | `tests/test_clink_continuation.py`, existing safe-default tests                                                                               |
| CLI result errors        | Nonzero/explicit terminal errors remain failures despite readable content.                                                                                                       | `tests/test_clink_execution_errors.py`                                                                                                        |
| API regression           | Provider startup, restrictions, chat, and continuation retain existing results/contracts.                                                                                        | Existing server/auto-mode/chat/continuation suites                                                                                            |
| Live assumptions         | Recorded installed version and intended auth mode complete a follow-up; successful output alone does not prove billing.                                                          | Opt-in `tests/test_clink_integration.py` cases                                                                                                |
| Live selection and skips | No selector prevents execution and reports a separate skip; invalid selectors and opted-in failures fail; individual/all-skipped summaries do not claim coverage.                | `tests/test_simulator_sessions.py`                                                                                                            |

### Test isolation traps

`tests/conftest.py` registers providers at import, injects dummy keys even for `no_mock_provider`, fixes `DEFAULT_MODEL`, mocks effective auto mode, and reloads conversation modules. Reuse its declaration-based `isolated_provider_environment` fixture for focused in-process tests, additionally controlling dotenv values, intended default mode, and runtime capability state. Check fixture interactions explicitly; a marker alone cannot establish absence and a handwritten provider-key list can miss OpenCode Go or later providers.

For stdio acceptance use an isolated test launcher and an explicit subprocess environment allow-list. Prevent both `dotenv_values` and `load_dotenv` from reading the checkout's real `.env` before importing `server`; `reload_env({})` after a real dotenv load cannot remove credentials already copied into the environment. Redirect CLI user-config lookup to a test directory through test-only path patching, without reading personal login files or changing the developer's home/configuration. Then call the actual `server.run()` entrypoint.

Configure each fake executable through the normal CLI manifest override mechanism using the test Python executable and a fixture script. Fake output must exercise the actual vendor parser. This avoids depending on installed CLIs, native shell shebang behavior, vendor networking, or credentials. Use the existing MCP SDK and test dependencies; no new package is needed. Prohibit API-backed `ModelContext`/provider inference in the CLI-only path and inspect subprocess input, not merely returned continuation IDs.

Deterministic fake-CLI tests run with the ordinary non-integration suite. Use the test-only selector `PAL_CLINK_LIVE_TEST=codex`, `claude`, or `gemini` for live tests and the standalone live simulator case; it has no production runtime meaning. With no selector, pytest live cases skip normally and the simulator excludes its live case using the [separate skip-reporting contract](#optional-live-simulator-selection). An invalid selector or a selected case with a missing executable, unavailable authentication, or incompatible flags fails with an actionable prerequisite/error message instead of reporting success. Use a temporary workspace and safe default permissions. The legacy API integration/quick simulator checks require a separately configured environment and do not replace provider-free acceptance.

## Deferred work and risks

The concrete deferred findings and follow-up actions are in [design.md](design.md#deferred-findings-and-verification-limits). Image transport, API transaction behavior, and full subprocess cancellation supervision are not silently claimed solved here. If implementation discovers a dependency on one of them for the approved path, stop that slice and amend the design with evidence rather than silently weakening its acceptance test.

CLI authentication precedence and invocation flags vary by installed version. Recheck primary vendor documentation before changing CLI flags; apply dependency-handling guidance if a new or upgraded external interface/dependency becomes necessary. This plan reuses current flags and does not make such a change.

## Design-session validation

- Original source/vendor verification used `c20d384`; the 2026-10-12 review and corrections inspect `2d821f1`. No live inference or billing claim follows from those checks.
- The original five findings and the subsequent readiness findings R6–R8 are addressed in the plans, including the reopened R1 inventory gap. [review.md](review.md) records the corrections; all fourteen implementation tasks remain pending.
- `./code_quality_checks.sh` exits 1 with “No virtual environment found!” before linting/tests. No environment was installed for these documentation changes; application and live integration/simulator validation remain unrun.
- All implementation tasks remain pending. Document-only validation covers formatting, local links/anchors, task dependencies/parallel markers, and diff whitespace; results are recorded in the review's correction validation.

## Task 1 execution record (2026-10-13)

`ParsedCLIResponse.is_error` defaults to false for existing callers. Gemini and Claude parsers now classify terminal envelopes and diagnostic fallbacks as errors. Clink rejects either that flag or a nonzero exit before metadata pruning, output reduction, or assistant persistence, retaining diagnostic content and raw output. Successful replies with non-terminal warnings remain successful. Existing eager user/thread writes and error continuation metadata intentionally remain until Task 3.

Validation used Python 3.10.12 in the existing `pal` virtualenv (`source /home/sergio/.virtualenvs/pal/bin/activate`), without dependency changes:

| Check | Result |
| --- | --- |
| Baseline `./code_quality_checks.sh` | Passed: 2,024 passed, 16 skipped, 16 deselected; lint/format checks passed. Initial invocation lacked an active environment; the sandboxed retry stalled during async teardown, so the completed run used the existing environment outside the sandbox. |
| Task 1 pytest command above | Passed: 66 tests, including 52 new cases covering parsing, actual runner recovery, first/follow-up failures, no API resolution, preserved diagnostics, and successful warnings. |
| Final `./code_quality_checks.sh` | Passed: 2,076 passed, 16 skipped, 16 deselected, 19 subtests passed; Ruff, Black, and isort passed. |
| `./run_integration_tests.sh` | Failed: 3 failed, 2 passed, 11 skipped. Claude returned `4` plus a summary where the existing test expects exactly `4`; both OpenAI consensus cases failed with connection errors. Gemini CLI was unavailable. |
| `python communication_simulator_test.py --quick` | Failed: runner reported 4 passed and 2 failed (`basic_conversation`, `per_tool_deduplication`). Logs contain invalid Gemini API-key errors and missing continuation IDs. This is not successful live acceptance. |
| `kk:review-code:isolated` | Independent code-reviewer approved with no P0–P3 findings after inspecting all six implementation/test/task files. PAL `gemini-3.8-flash` external review failed with an invalid API key; it supplied no expert findings. The workflow proceeded with the independent reviewer as specified by its fallback. No systemic findings to index. |

Deferred verification work is owned by Tasks 13–14, rather than changing live-test policy or account configuration in this parser slice:

- Task 13: reconcile the Claude live assertion with the role's summary contract, asserting the arithmetic answer while still rejecting CLI errors; do not weaken failure classification or count request errors as skips.
- Task 14: rerun integration and quick simulator checks with valid credentials and reachable endpoints; investigate any remaining continuation failures with their exact payloads. Install/authenticate the intended CLI separately before claiming live Gemini coverage.
- Task 14: retry the external PAL review when valid credentials are available. The completed independent review does not stand in for an external model result.

No implementation scope change or new project convention was needed. These results supersede the design-session environment limitation above; they do not complete the remaining feature tasks.

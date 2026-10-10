# Implementation: CLI-only conversations through clink

> Status: pending implementation and independent review
> Created: 2026-10-10
> Design: [design.md](design.md)
> Tasks: [tasks.md](tasks.md)
> Baseline: `c20d384`

## Read first

Implement the approved [design](design.md), not a CLI backend for `chat`. Read `AGENTS.md` and applicable nested instructions before changes. The project is Python, uses Pydantic request/configuration models, exposes MCP over stdio, and keeps conversation state in one process. No dependency change is planned.

`server.py` owns bootstrap, tool availability, MCP dispatch, and prompts. `tools/clink.py` owns the CLI request; `clink/registry.py` resolves user/built-in configuration; `clink/agents/` executes subprocesses; `clink/parsers/` interprets output. Shared thread types are in `utils/conversation_memory.py`, with the synchronized in-memory store in `utils/storage_backend.py`.

Do not use `requires_model()` as a runtime-availability test or reuse `ModelContext` for CLI history. Do not use inherited continuation helpers that create an empty thread and append messages separately for the new CLI commit path. Preserve API-tool behavior.

## Implementation contracts

### CLI result classification

Extend `ParsedCLIResponse` with a backward-compatible success/error indication rather than teaching `CLinkTool` to reparse vendor JSON. Preserve content/metadata for diagnostics. The agent result's nonzero return code independently makes the call a failure, even when a recovery hook produced readable output. `CLinkTool` checks the indication and return code before metadata pruning, output formatting, or any history update.

Gemini's explicit top-level error and empty-response fallback are failures. Claude's `is_error`, failed result subtype, and stderr-only fallback are failures. Codex error-only output and `turn.failed` are failures; an error event followed by a successful final response can remain a successful result with diagnostic metadata. Test permission-denied subtool warnings separately from terminal failure. Keep parsing useful diagnostics; change their classification, not their visibility.

### Request-local conversation service

Create `clink/conversation.py` with explicit operations for loading a validated snapshot, resolving saved CLI defaults, rendering bounded history, reserving a thread, and committing an exchange. Use request-local values across awaits; do not rely on mutable `CLinkTool._current_arguments` or `_model_context` for storage/selection after subprocess execution. Retain the existing prompt `ContextVar` behavior.

At server dispatch, resolve tool availability before any continuation processing. Send requests targeting clink directly to its `execute()` path before legacy reconstruction. Inside clink, load a snapshot when an ID exists, combine only allowed selection defaults with current request values, validate the request/permissions/files, prepare history, run the CLI, classify its result, apply the existing reply-size policy, and commit original user text plus the user-visible reply.

Read the exact stored JSON snapshot for compare-and-set. Validate the UUID and deserialize with existing thread types. Keep the original `initial_context` and origin tool; add/update only its namespaced `clink_defaults` on successful commit. Do not copy internal `_model_context`, `_remaining_tokens`, expanded history, or edit authorization from an old request.

Use a process-local reservation shared across clink instances, keyed by continuation UUID. A second clink request for that ID fails immediately with retryable metadata. Different IDs proceed independently. Clean reservations on validation/execution/commit errors and cancellation. A concurrent non-clink writer is handled by the commit comparison, not by overwriting its update.

Add `compare_and_setex` to `InMemoryStorage`: under its existing lock, check expiry and compare the expected serialized value, then write the replacement and TTL. Expected absence supports first creation; an expired existing thread fails an update and is never resurrected. Return a boolean conflict result; surface exceptions distinctly. Construct a new two-turn `ThreadContext` in memory before first write; do not call `create_thread()` followed by `add_turn()` twice.

For follow-ups, reject insufficient capacity before CLI execution and check it again at commit. On successful commit, compute remaining complete exchanges as integer pairs of available message slots. On a commit failure/conflict, return the generated reply with `history_saved=false`, a clear warning, and no offer; never automatically repeat inference. Do not silently swallow failed recording as the old helper does.

### History renderer and configuration

Begin with the approved default in `clink/constants.py`; expose per-client `history_token_budget` in the configuration slice. Add the field to raw and resolved config models with a positive-integer default of 16,384, and pass it through registry resolution. Reject zero, negative, fractional, boolean, and malformed values instead of silently coercing an invalid budget. Do not add a request-level knob or a provider identity.

The renderer groups a user turn with subsequent assistant turn(s) until the next user turn, traverses parent chains with the existing depth bound, and selects the newest contiguous suffix of complete groups. For malformed legacy groups, report omissions; do not repair stored records. A nonempty thread with no complete exchange is an error. Test multi-assistant groups and incomplete trailing user turns.

Use `utils.token_utils.estimate_tokens()` without provider lookups. Include headers, path references, omission annotations, and closing guidance in the budget; recompute the rendered result until it fits. Never trim individual messages. The latest complete group not fitting causes a pre-execution error. The current prompt and CLI-generated context are outside this history-only budget, which must be stated in documentation and diagnostics.

The prompt includes retained chronological history followed by the new user request exactly once. Historical files/images appear as references; no historical file content is read by this renderer. Current files still pass the existing validation. Preserve output truncation metadata and record the same reduced reply shown to the user.

### Runtime capability state

Create `utils/runtime_capabilities.py` for a small runtime record and pure availability decisions, avoiding a `tools/listmodels.py` import of `server.py`. Record API/CLI-only mode, configured provider identities, CLI executable statuses, and enabled/available tool names. Initialize once during startup after environment/configuration parsing. Tests explicitly initialize/reset this state rather than relying on import order.

Refactor `configure_providers()` to distinguish genuine absence from explicit incomplete configuration while keeping successful provider registration, restrictions, cleanup, and API-mode checks. If no provider was requested, return that state to startup instead of immediately raising. If a non-placeholder provider input was supplied but no usable configuration results, preserve an actionable failure. Do not catch arbitrary bootstrap errors and reinterpret them as CLI absence.

The absence classifier must cover the primary Gemini/OpenAI/XAI/DIAL/OpenRouter keys, both Azure key and endpoint, and custom URL/key. Use existing exact placeholder conventions, including the PowerShell custom-URL placeholder. Do not trim or mutate real credential values. Restrictions/default-model settings alone do not request a provider. `GOOGLE_API_KEY`/`ANTHROPIC_API_KEY` used by child CLIs are not silently promoted into new PAL provider configuration rules.

Create `clink/availability.py` for executable resolution using the runner's current lookup semantics. Startup and invocation share that resolver. Do not run commands to probe login/version and do not print full arguments or environment values. A wrapper executable being found does not validate its script/arguments. Keep other runner behavior unchanged.

Retain an enabled-tool inventory after `DISABLED_TOOLS` filtering, then compute the runtime available set. CLI-only requires enabled clink and at least one found configured executable, and admits only clink/version/listmodels. Guard ordinary calls before history reconstruction. API mode keeps its existing enabled tools. Mode changes require restart; invocation still rechecks its selected executable.

### Discovery and setup

Use the same runtime availability in `handle_list_tools`, `handle_list_prompts`, and `handle_get_prompt`, including aliases looked up through `PROMPT_TEMPLATES`. In CLI-only mode `/pal:continue` requests clink with the previous ID and optional explicit CLI override; do not retain the API-model instruction. New clink calls require selection when multiple CLIs are configured, while the schema accepts a continuation with no CLI name and runtime verifies saved state.

Add an early CLI-only result in `ListModelsTool.execute()` using the runtime record: no API providers/models, configured CLI names, found/missing executable status as of startup, and authentication not checked. API-mode result formatting is preserved. Startup instructions/logs must describe the actual mode, rather than a nonexistent auto/fixed API model.

Update Bash and PowerShell setup messages to present both setup paths. They may recommend installation/login but must not invoke a CLI for authentication detection. Replace active placeholder `CUSTOM_API_URL` assignments in generated PowerShell templates with an empty/commented value; test the classifier against old placeholder files. Avoid copying new credential values or changing user login configuration. Check both Python and Docker PowerShell setup branches, which have separate template/message sites.

## Ordered implementation slices

Each task in [tasks.md](tasks.md) maps to one complete user-visible path. Sizes measure substantive implementation/test work; mechanical field forwarding and fixture wiring are called out rather than hidden in an oversized task.

### Task 1: Gemini and Claude execution failures

Files: `clink/parsers/gemini.py`, `clink/parsers/claude.py`, `tools/clink.py`, new `tests/test_clink_execution_errors.py`; the new result flag in `clink/parsers/base.py` is mechanical transport plumbing.

1. Classify Gemini/Claude terminal failures and preserve their diagnostic text → verify: parser-result assertions for success, explicit errors, empty fallbacks, and valid answers containing subtool warnings.
2. Gate clink success/continuation on zero return code and successful parsed result → verify: `python -m pytest tests/test_clink_execution_errors.py tests/test_clink_gemini_agent.py tests/test_clink_claude_agent.py -q`; failed requests neither create a thread nor append an assistant reply.

### Task 2: Codex terminal failures

Files: `clink/parsers/codex.py`, `tests/test_clink_execution_errors.py`.

1. Distinguish final agent output from error-only/failed-turn events using Task 1's result contract → verify: fixtures for a successful turn, error-only stream, failed turn with partial text, and warning followed by successful output.
2. Preserve nonzero-exit diagnostics while returning a tool error → verify: `python -m pytest tests/test_clink_execution_errors.py tests/test_clink_codex_agent.py tests/test_clink_parsers.py -q`.

### Task 3: Provider-free conversation continuation

Files: new `clink/conversation.py`, `tools/clink.py`, `server.py`, `utils/storage_backend.py`, new `tests/test_clink_continuation.py`; the default constant is mechanical configuration wiring.

1. Implement snapshot/render/selection handling with the default budget and direct clink dispatch → verify: direct and MCP-handler second calls contain the first prompt/reply with provider lookup and `ModelContext` construction set to fail.
2. Add the storage comparison and paired commit, thread reservation, limits, warnings, and safe selection inheritance → verify: failure/expiry/conflict leaves serialized state unchanged; successful writes add exactly two turns; overlapping same-thread calls reject and different threads stay independent.
3. Update clink's continuation schema and response offers; remove its use of incremental shared helpers → verify: continuation without `cli_name` validates, new calls with ambiguous selection fail, explicit CLI/role changes persist only on success, omitted edit authorization stays false.
4. Preserve API dispatch/reconstruction and shared-record compatibility → verify: `python -m pytest tests/test_clink_continuation.py tests/test_clink_safe_default.py tests/test_conversation_continuation_integration.py tests/test_chat_cross_model_continuation.py -q`.

This is the risk-first core. It can be verified through direct/handler calls before startup is relaxed; it is not the full issue's delivery by itself.

### Task 4: User-configurable history bounds

Files: `clink/models.py`, `clink/registry.py`, `clink/conversation.py`, new `tests/test_clink_history_budget.py`, `docs/configuration.md`; default/forwarding changes in `clink/constants.py` are mechanical.

1. Expose and validate the per-client setting and pass the selected client's budget to reconstruction → verify: missing setting resolves to 16,384; invalid values fail; explicit lower/higher settings change retained complete exchanges through clink.
2. Document heuristic accounting and restart implications → verify: `python -m pytest tests/test_clink_history_budget.py tests/test_clink_continuation.py tests/test_clink_tool.py -q`; rendered history including wrappers is within the configured estimate, or the request fails before launching.

### Task 5: Start and chat without a provider

Files: new `utils/runtime_capabilities.py`, new `clink/availability.py`, `server.py`, new `tests/test_cli_only_startup.py`; replacing the runner's existing executable lookup with the shared helper is mechanical.

1. Classify configuration absence versus explicit invalid/incomplete inputs; preserve API-mode checks → verify: absent/empty/placeholder, partial Azure/custom settings, valid custom endpoint, and empty post-restriction catalogue tests.
2. Compute runtime mode/availability and use the shared executable resolver → verify: enabled clink plus one found executable starts CLI-only; disabled clink/no executables fails; API mode does not require an installed CLI.
3. Guard unavailable calls before continuation and run a first fake-CLI request → verify: `python -m pytest tests/test_cli_only_startup.py tests/test_server.py tests/test_disabled_tools.py tests/test_auto_mode_custom_provider_only.py -q`; no provider call is needed and denied requests preserve history.

### Task 6: Discover and continue available CLI conversations

Files: `server.py`, `tools/listmodels.py`, new `tests/test_cli_only_discovery.py`; README navigation is a mechanical documentation link.

1. Align tool/prompt listing and alias retrieval with runtime availability → verify: exactly the three CLI-only tools, matching prompts, and rejection of unavailable aliases/direct calls.
2. Make initialization, `listmodels`, and `/pal:continue` mode-aware → verify: no fabricated models or authentication claim, clink continuation instructions in CLI-only mode, and unchanged API-mode instructions/list output.
3. Verify discovery coherency → verify: `python -m pytest tests/test_cli_only_discovery.py tests/test_server.py tests/test_disabled_tools.py -q`.

### Task 7: Local setup and usage documentation

Files: `run-server.sh`, `docs/getting-started.md`, `docs/tools/clink.md`, `README.md`.

1. Present CLI installation/authentication prerequisites, provider-free setup, first request, continuation, per-request edit permissions, limits, and failures → verify: walk each instruction against Tasks 3–6 using fake-CLI MCP acceptance, and check all relative links.
2. Correct the setup warning and stale command documentation without altering user credentials → verify: `bash -n run-server.sh`; compare documented commands to `clink/constants.py` plus manifests and current vendor documentation. In particular, do not retain the unshipped Gemini `--telemetry false` argument.

### Task 8: PowerShell setup path

Files: `run-server.ps1`, `docs/getting-started.md` (Windows-specific clarification only if needed).

1. Update both setup branches' messages/templates and stop generating active custom-URL placeholders → verify: inspect all template sites and parse the script with PowerShell's parser on a supported environment without executing installation or MCP configuration changes.
2. Check generated configuration classification → verify: old placeholder values select absence; a real supplied endpoint remains provider configuration; no live CLI/auth command is added to setup.

### Task 9: Full MCP and optional live verification

Files: new `tests/test_cli_only_mcp.py`, `tests/test_clink_integration.py`, new `simulator_tests/test_clink_cli_only.py`; fake CLI fixtures and registration in `simulator_tests/__init__.py` (`TEST_REGISTRY` and exports) are mechanical wiring.

1. Launch the real server entrypoint with isolated configuration and controlled fake CLI processes → verify: initialization, discovery, initial reply, dependent follow-up, and unavailable-tool error over the same stdio session for all three parser formats.
2. Record fake CLI stdin/argv and make provider resolution/inference fail loudly → verify: prior user/reply markers appear exactly once, edit flags need current opt-in, and no model provider is used.
3. Add explicitly opted-in live checks for the selected installed/authenticated CLI, and a standalone simulator case → verify: the selected integration test and `python communication_simulator_test.py --individual clink_cli_only`; a missing prerequisite is reported distinctly and an actual opted-in request failure fails.

### Task 10: Final verification and handoff

1. Run `$kk:test` for the affected feature and the repository's comprehensive checks → verify: record full unit/quality results, targeted MCP tests, supported script syntax checks, integration script, quick simulator suite, and individual CLI simulator result. Record missing credentials/tools as unmet checks rather than success.
2. Run `$kk:document` and check all setup/configuration claims against implemented behavior → verify: links and examples work; deferred items remain discoverable; completed feature documents move only under the applicable workflow.
3. Run `$kk:review-code` with Python language input and `$kk:review-spec` for all feature documents → verify: address findings or record explicit durable follow-up tasks before marking completion. Independent review is not claimed by these author checks.

## Verification matrix

| Concern                     | Required observation                                                                                                                                  | Primary test location                                           |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Provider-free start         | MCP initialization succeeds with genuinely empty provider state and a fake executable.                                                                | `tests/test_cli_only_startup.py`, `tests/test_cli_only_mcp.py`  |
| Invalid configured provider | No silent CLI-only fallback after configuration/restriction failure.                                                                                  | `tests/test_cli_only_startup.py`                                |
| Availability                | Listings, aliases, direct calls, and continue shortcut agree; denied calls leave history intact.                                                      | `tests/test_cli_only_discovery.py`                              |
| Real transport              | Two requests through one MCP stdio process preserve prompt/reply markers for every CLI format.                                                        | `tests/test_cli_only_mcp.py`                                    |
| Direct parity               | Direct `CLinkTool.execute()` does not need a server-prepared history prompt.                                                                          | `tests/test_clink_continuation.py`                              |
| History bounds              | Complete newest exchanges fit including wrappers; impossible newest exchange errors; references cause no historical file reads.                       | `tests/test_clink_history_budget.py`                            |
| Storage                     | Successful pair is one write; failure does not append or refresh TTL; expiration/conflict is not overwritten.                                         | `tests/test_clink_continuation.py`                              |
| Concurrency                 | Same clink thread is busy; distinct threads do not leak selection, prompts, or recording state; reservation released after all failures/cancellation. | `tests/test_clink_continuation.py`                              |
| Selection/permissions       | Saved successful CLI/role defaults work; explicit overrides win; initial edit authorization is never inherited.                                       | `tests/test_clink_continuation.py`, existing safe-default tests |
| CLI result errors           | Nonzero/explicit terminal errors remain failures despite readable content.                                                                            | `tests/test_clink_execution_errors.py`                          |
| API regression              | Provider startup, restrictions, chat, and continuation retain existing results/contracts.                                                             | Existing server/auto-mode/chat/continuation suites              |
| Live assumptions            | Recorded installed version and intended auth mode complete a follow-up; successful output alone does not prove billing.                               | Opt-in `tests/test_clink_integration.py` cases                  |

### Test isolation traps

`tests/conftest.py` registers providers at import, injects dummy keys even for `no_mock_provider`, fixes `DEFAULT_MODEL`, mocks effective auto mode, and reloads conversation modules. A marker alone cannot establish absence. In focused in-process tests explicitly clear and restore provider registrations/instances, set the intended default mode, and replace the dotenv mapping with an empty controlled mapping; do not depend on fixture execution order.

For stdio acceptance use an isolated test launcher and an explicit subprocess environment allow-list. Prevent both `dotenv_values` and `load_dotenv` from reading the checkout's real `.env` before importing `server`; `reload_env({})` after a real dotenv load cannot remove credentials already copied into the environment. Redirect CLI user-config lookup to a test directory through test-only path patching, without reading personal login files or changing the developer's home/configuration. Then call the actual `server.run()` entrypoint.

Configure each fake executable through the normal CLI manifest override mechanism using the test Python executable and a fixture script. Fake output must exercise the actual vendor parser. This avoids depending on installed CLIs, native shell shebang behavior, vendor networking, or credentials. Use the existing MCP SDK and test dependencies; no new package is needed. Prohibit API-backed `ModelContext`/provider inference in the CLI-only path and inspect subprocess input, not merely returned continuation IDs.

Deterministic fake-CLI tests run with the ordinary non-integration suite. Use the test-only selector `PAL_CLINK_LIVE_TEST=codex`, `claude`, or `gemini` for live tests and the standalone live simulator case; it has no production runtime meaning. With no selector these live cases are unrequested and skipped. An invalid selector or a selected case with a missing executable, unavailable authentication, or incompatible flags fails with an actionable prerequisite/error message instead of reporting success. Use a temporary workspace and safe default permissions. The legacy API integration/quick simulator checks require a separately configured environment and do not replace provider-free acceptance.

## Deferred work and risks

The concrete deferred findings and follow-up actions are in [design.md](design.md#deferred-findings-and-verification-limits). Image transport, API transaction behavior, and full subprocess cancellation supervision are not silently claimed solved here. If implementation discovers a dependency on one of them for the approved path, stop that slice and amend the design with evidence rather than silently weakening its acceptance test.

CLI authentication precedence and invocation flags vary by installed version. Recheck primary vendor documentation before changing CLI flags; apply dependency-handling guidance if a new or upgraded external interface/dependency becomes necessary. This plan reuses current flags and does not make such a change.

## Design-session validation

- Before and after document creation, `./code_quality_checks.sh` exited 1 with “No virtual environment found!” before linting or tests. `workon` and `mkvirtualenv` were unavailable. No development environment was installed for a documentation-only task.
- No `logs/` directory existed to inspect; no server-health claim is made.
- The isolated verification checked source at `c20d384` and current vendor documentation, not installed CLI execution.
- Only feature documents are authored in this session. All implementation tasks remain pending.
- Document checks passed: Prettier formatting for all three files; 39 local links/anchors; ten consecutively numbered pending S/M tasks; acyclic dependencies and symmetric, nonblocking parallel markers; explicit verification steps; and the final test/document/code/spec review gates.
- `git diff --check` passed for the tracked tree. The new, untracked documents were separately checked for trailing whitespace by the document validator. The working tree contains only the new `docs/feat/wip/cli-only-clink/` directory.
- No implementation tests or live integration/simulator checks were run for this documentation-only delivery. The failed quality-check attempts above remain an environment limitation, not passing validation. Recommended next gate: `$kk:review-design cli-only-clink`; optional `$kk:clarify-docs` may first refine these three documents without changing approved requirements.

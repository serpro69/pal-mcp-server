# Tasks: CLI-only conversations through clink

> Design: [design.md](design.md)
> Implementation: [implementation.md](implementation.md)
> Issue: [#402](https://github.com/BeehiveInnovations/pal-mcp-server/issues/402)
> Status: pending
> Created: 2026-10-10
> Not Doing: CLI-backed chat/providers, additional CLI-backed workflows, installation/login/billing management, shared remote deployments, native CLI sessions, durable history, multimodal parity, new filesystem sandbox, dependency upgrades

All implementation checkboxes are pending. The user approved the design presentation; independent design review has not run. Known deferred findings and concrete next steps are recorded in [design.md](design.md#deferred-findings-and-verification-limits). No task may be marked complete from a mocked single response or a skipped live test.

## Task 1: Surface Gemini and Claude execution failures

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-1-gemini-and-claude-execution-failures](implementation.md#task-1-gemini-and-claude-execution-failures)

- [ ] 1.1 Add a backward-compatible failure indication to `clink/parsers/base.py`; set it for Gemini/Claude explicit errors and diagnostic-only fallbacks → verify successful answers and failed outputs have distinct classifications while preserving text.
- [ ] 1.2 In `tools/clink.py`, reject nonzero exits and failed parsed results before pruning metadata or recording history → verify errors create no thread/reply turn and trigger no API fallback.
- [ ] 1.3 Add `tests/test_clink_execution_errors.py`, covering normal output, explicit failures, diagnostic recovery, and non-terminal subtool warnings → verify the Task 1 command in the implementation plan passes.

## Task 2: Surface Codex terminal failures

- **Status:** pending
- **Depends on:** Task 1
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-2-codex-terminal-failures](implementation.md#task-2-codex-terminal-failures)

- [ ] 2.1 Update `clink/parsers/codex.py` to distinguish successful final output from error-only streams and `turn.failed` → verify a failed turn containing partial text is still an error.
- [ ] 2.2 Extend `tests/test_clink_execution_errors.py` for error-only, partial failure, recovered nonzero exit, and successful output with warnings → verify the Task 2 command passes and no failure creates a continuation.

## Task 3: Continue CLI conversations without provider context

- **Status:** pending
- **Depends on:** Task 2
- **Size:** M
- **Slicing:** Risk-First — removes the provider dependency in the complete follow-up path.
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-3-provider-free-conversation-continuation](implementation.md#task-3-provider-free-conversation-continuation)

- [ ] 3.1 Create `clink/conversation.py` for validated snapshots, complete-exchange rendering, saved selection, and request-local preparation; define the default budget in `clink/constants.py` → verify history contains previous user/reply text without provider resolution or historical file-content reads.
- [ ] 3.2 Dispatch clink before legacy reconstruction in `server.py`, and make `CLinkTool.execute()` own direct/MCP continuation → verify both call paths produce equivalent history and record the new prompt exactly once.
- [ ] 3.3 Add synchronized `compare_and_setex` in `utils/storage_backend.py` and use it to commit a complete user/reply pair plus selection defaults → verify conflicts, expiry, and storage failure cause no partial write or overwrite.
- [ ] 3.4 Reserve a clink thread during execution, check two-message capacity, release on every exit, and return a warning/no offer if recording fails → verify overlap rejects, separate threads remain independent, and failed requests do not consume slots or refresh TTL.
- [ ] 3.5 Update clink's continuation schema, defaults, permissions, and remaining-exchange counts → verify omitted `cli_name` works only with resolvable continuation defaults; edit permission is never inherited; failed selection switches do not persist.
- [ ] 3.6 Add `tests/test_clink_continuation.py` and run the Task 3 regression command → verify provider-free follow-ups, cross-tool record compatibility, bounded legacy handling, and unchanged API-tool paths.

## Task 4: Configure history budgets per CLI

- **Status:** pending
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** Task 5, Task 6
- **Docs:** [implementation.md#task-4-user-configurable-history-bounds](implementation.md#task-4-user-configurable-history-bounds)

- [ ] 4.1 Add `history_token_budget` to `clink/models.py` and forward it through `clink/registry.py` into the conversation renderer → verify missing values use 16,384 and invalid values fail configuration.
- [ ] 4.2 Add `tests/test_clink_history_budget.py` for adjustable bounds, exact wrapper accounting, newest complete exchanges, oversized latest exchange, references, and legacy omissions → verify the Task 4 command passes.
- [ ] 4.3 Document the setting in `docs/configuration.md`, including heuristic/history-only accounting and restart effects → verify examples match resolved configuration and do not imply a model context-window guarantee.

## Task 5: Start and chat with only an installed CLI

- **Status:** pending
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** Task 4
- **Docs:** [implementation.md#task-5-start-and-chat-without-a-provider](implementation.md#task-5-start-and-chat-without-a-provider)

- [ ] 5.1 Add runtime capability state in `utils/runtime_capabilities.py` and refactor provider bootstrap in `server.py` to distinguish absent configuration from explicit failures → verify the configuration matrix, including old PowerShell placeholders and partial Azure/custom input.
- [ ] 5.2 Add shared executable resolution in `clink/availability.py` and use it from startup and `BaseCLIAgent.run()` → verify no command is executed during discovery and API mode works without an installed CLI.
- [ ] 5.3 Select CLI-only mode only for enabled clink plus a found executable; compute the explicit available-tool set and reject unavailable calls before state changes → verify missing/disabled paths fail with actionable setup guidance.
- [ ] 5.4 Add `tests/test_cli_only_startup.py` and run the Task 5 command → verify startup and a first fake-CLI reply with absent provider state, while existing provider/restriction behavior remains intact.

## Task 6: Discover and resume the available CLI tools

- **Status:** pending
- **Depends on:** Task 5
- **Size:** M
- **Can run in parallel with:** Task 4
- **Docs:** [implementation.md#task-6-discover-and-continue-available-cli-conversations](implementation.md#task-6-discover-and-continue-available-cli-conversations)

- [ ] 6.1 Apply runtime availability consistently to tool/prompt listing and prompt aliases in `server.py` → verify CLI-only exposes clink/version/listmodels and unavailable aliases cannot bypass the check.
- [ ] 6.2 Make startup instructions and `/pal:continue` describe clink in CLI-only mode → verify no instruction requests an unavailable API model and API-mode prompts retain their behavior.
- [ ] 6.3 Add CLI-only diagnostics in `tools/listmodels.py` and README navigation → verify executable presence is distinguished from unverified authentication and no model identities are invented.
- [ ] 6.4 Add `tests/test_cli_only_discovery.py` and run the Task 6 command → verify listings, aliases, direct calls, and continuation instructions agree.

## Task 7: Document and explain local setup

- **Status:** pending
- **Depends on:** Task 4, Task 6
- **Size:** M
- **Can run in parallel with:** Task 9
- **Docs:** [implementation.md#task-7-local-setup-and-usage-documentation](implementation.md#task-7-local-setup-and-usage-documentation)

- [ ] 7.1 Update `run-server.sh` messaging and `docs/getting-started.md` with API and CLI setup paths → verify `bash -n run-server.sh` and walk the CLI-only instructions against MCP acceptance.
- [ ] 7.2 Update `docs/tools/clink.md` and `README.md` with first request, continuation, per-request edit permission, history behavior, and failures → verify relative links and examples match implemented fields.
- [ ] 7.3 Verify vendor auth guidance and actual shipped invocation arguments; correct the stale Gemini telemetry example → verify primary sources, disclose version assumptions, and avoid claims that a successful reply proves subscription billing.

## Task 8: Align PowerShell setup

- **Status:** pending
- **Depends on:** Task 7
- **Size:** S
- **Can run in parallel with:** Task 9
- **Docs:** [implementation.md#task-8-powershell-setup-path](implementation.md#task-8-powershell-setup-path)

- [ ] 8.1 Update both `run-server.ps1` setup branches' messages and custom-endpoint template values → verify generated defaults represent absence and supplied endpoints remain configured.
- [ ] 8.2 Validate PowerShell syntax without executing installation/configuration mutations; clarify Windows setup docs only as needed → verify no CLI login probe or new credential handling is introduced.

## Task 9: Prove the MCP conversation and live prerequisites

- **Status:** pending
- **Depends on:** Task 4, Task 6
- **Size:** M
- **Can run in parallel with:** Task 7, Task 8
- **Docs:** [implementation.md#task-9-full-mcp-and-optional-live-verification](implementation.md#task-9-full-mcp-and-optional-live-verification)

- [ ] 9.1 Add `tests/test_cli_only_mcp.py` and fake CLI fixtures using isolated dotenv, provider, and CLI-user configuration → verify real stdio initialization/discovery/first request/follow-up for all three CLI output formats.
- [ ] 9.2 Assert prior prompt/reply markers, current edit flags, and unavailable-tool behavior; prohibit provider inference/context resolution → verify tests cannot pass because `tests/conftest.py` supplied dummy credentials or mocked providers.
- [ ] 9.3 Extend `tests/test_clink_integration.py` with test-only `PAL_CLINK_LIVE_TEST` selection and provider-free conversation checks → verify unselected cases skip, but invalid selection and selected prerequisite/request failures fail with clear diagnostics.
- [ ] 9.4 Add `simulator_tests/test_clink_cli_only.py` and register it in `simulator_tests/__init__.py` → verify `python communication_simulator_test.py --individual clink_cli_only` with the same explicit selector and record CLI versions/auth prerequisites for live runs.

## Task 10: Final verification

- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-10-final-verification-and-handoff](implementation.md#task-10-final-verification-and-handoff)

- [ ] 10.1 Run `$kk:test`, `./code_quality_checks.sh`, targeted CLI-only MCP tests, and script syntax checks → verify all deterministic checks pass and record actual results.
- [ ] 10.2 Run `./run_integration_tests.sh`, `python communication_simulator_test.py --quick`, and the individual CLI-only simulator/live checks in a configured environment → verify results; record missing prerequisites as unmet checks, never as completed coverage.
- [ ] 10.3 Run `$kk:document` and validate configuration/setup links and examples → verify user guidance matches final behavior and deferred findings retain concrete follow-up actions.
- [ ] 10.4 Run `$kk:review-code` with Python language input → verify findings are fixed or recorded as durable follow-up work, with explicit reasons for any remaining gap.
- [ ] 10.5 Run `$kk:review-spec` over all feature documents → verify implementation matches the approved scope and acceptance criteria before marking this feature done.

## Dependency Graph

```text
Task 1 -> Task 2 -> Task 3
Task 3 -> Task 4
Task 3 -> Task 5 -> Task 6
(Task 4 + Task 6) -> Task 7 -> Task 8
(Task 4 + Task 6) -> Task 9
(Tasks 1-9) -> Task 10
```

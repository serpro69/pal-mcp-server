# Tasks: CLI-only conversations through clink

> Design: [design.md](design.md)
> Implementation: [implementation.md](implementation.md)
> Issue: [#402](https://github.com/BeehiveInnovations/pal-mcp-server/issues/402)
> Status: done
> Created: 2026-10-10
> Baseline: `2d821f1`; review corrections: 2026-10-12
> Final validation: [validation.md](validation.md)
> Not Doing: CLI-backed chat/providers, additional CLI-backed workflows, installation/login/billing management, shared remote deployments, native CLI sessions, durable history, multimodal parity, new filesystem sandbox, dependency upgrades

All fourteen tasks are done. Review findings R1–R8 are addressed in the plans, and final implementation corrections and actual check results are recorded in [validation.md](validation.md). Deferred scope remains in [design.md](design.md#deferred-findings-and-verification-limits). Tasks 1–6 established reliable bounded CLI conversations. Tasks 7–9 added provider setup diagnostics, runtime capability snapshots, call guards, and coherent discovery/continuation guidance. Tasks 10–11 document local setup and usage and align Bash/PowerShell setup messages and templates. Task 12 proves conversations and immediate busy rejection through real MCP stdio with isolated fake CLIs. Task 13 adds explicitly selected live checks with separate skip reporting; Claude and Codex completed real first/follow-up conversations. Task 14 completed the setup walkthrough and final reviews. Existing API-environment failures and unverified platforms remain explicit limitations; skipped or failed checks are not completed coverage.

## Task 1: Surface Gemini and Claude execution failures

- **Status:** done
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-1-gemini-and-claude-execution-failures](implementation.md#task-1-gemini-and-claude-execution-failures)

- [x] 1.1 Add a backward-compatible failure indication in `clink/parsers/base.py` and classify Gemini/Claude terminal errors and diagnostic-only output → verify distinct success/failure classifications with diagnostic text retained.
- [x] 1.2 Reject failed parsed results/nonzero exits in `tools/clink.py` before metadata pruning, success formatting, or assistant persistence → verify no assistant turn/API fallback; current user/thread writes and error continuation metadata remain until Task 3.
- [x] 1.3 Add `tests/test_clink_execution_errors.py` and run the Task 1 command → verify successful answers with non-terminal warnings remain successful.

Completed 2026-10-13: 66 targeted tests passed; full quality checks passed with 2,076 tests passed and 16 skipped. Independent code review found no issues. Live integration/simulator checks failed and external PAL review was unavailable; results and concrete follow-ups are in the [execution record](implementation.md#task-1-execution-record-2026-10-13). Task 1's deterministic contract is complete; feature-level live acceptance remains open under Tasks 13–14.

## Task 2: Surface Codex terminal failures

- **Status:** done
- **Depends on:** Task 1
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-2-codex-terminal-failures](implementation.md#task-2-codex-terminal-failures)

- [x] 2.1 Classify error-only streams, `turn.failed` with partial text, recovered nonzero exits, and successful warning-bearing replies in `clink/parsers/codex.py`.
- [x] 2.2 Extend `tests/test_clink_execution_errors.py` and run the Task 2 command → verify failures save no assistant turn; Task 3 owns the later no-write/no-continuation guarantees.

Completed 2026-10-13: 102 targeted tests passed; full quality checks passed with 2,121 tests passed and 16 skipped. Independent review found no issues. Live-check and external-review limitations remain recorded in the [Task 2 execution record](implementation.md#task-2-execution-record-2026-10-13).

## Task 3: Commit sequential CLI exchanges atomically

- **Status:** done
- **Depends on:** Task 2
- **Size:** M
- **Slicing:** Risk-First — establish one persistence owner without also changing history selection or scheduling.
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-3-commit-sequential-cli-exchanges-atomically](implementation.md#task-3-commit-sequential-cli-exchanges-atomically)

- [x] 3.1 Create `clink/conversation.py` and replace clink's eager invocation decorator with request-local preparation → preserve request/path/size checks, prompt-file expansion once, attachments, internal-field filtering, and current-request permissions.
- [x] 3.2 Add `compare_and_setex` to `utils/storage_backend.py` and commit complete raw user/reply pairs → verify two-slot capacity, TTL refresh only after success, conflict/expiry protection, and reply/warning/no-offer on failed recording.
- [x] 3.3 Reuse current provider-independent snapshot rendering for sequential follow-ups and emit existing transcript thread/turn events only after commit → verify no historical file-content reads, raw audit content, event ordering, and disabled/failed audit behavior.
- [x] 3.4 Add `tests/test_clink_continuation.py` and run the Task 3 regressions → verify direct/MCP sequential parity and that every classified failure leaves memory/TTL unchanged with no continuation metadata. Keep existing MCP waiting and direct caller-coordination behavior until Task 5.

Completed 2026-10-13: all 327 targeted checks passed; full quality checks passed with 2,199 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 3 execution record](implementation.md#task-3-execution-record-2026-10-13).

## Task 4: Bound history and restore CLI selection

- **Status:** done
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-4-bound-history-and-restore-cli-selection](implementation.md#task-4-bound-history-and-restore-cli-selection)

- [x] 4.1 Implement complete-exchange rendering in `clink/conversation.py` with the 16,384 default in `clink/constants.py` → verify wrappers/notices/reference accounting, chronological suffix selection, parent depth, incomplete legacy groups, and oversized-newest-exchange errors.
- [x] 4.2 Preserve raw content while storing reduced presentation in `model_metadata.clink.visible_reply` → verify subsequent CLI history uses displayed text without losing raw cross-tool/audit content.
- [x] 4.3 Update `tools/clink.py` selection/defaults/schema for successful CLI/role inheritance → verify explicit overrides, continuation without `cli_name`, ambiguity errors, and no inherited edit permission.
- [x] 4.4 Add `tests/test_clink_history_budget.py`, extend continuation tests, and run the Task 4 command → verify the new history/selection behavior; per-client settings and immediate busy errors remain assigned to Tasks 6 and 5.

Completed 2026-10-13: 209 targeted and 14 tool-surface tests passed; full quality checks passed with 2,257 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 4 execution record](implementation.md#task-4-execution-record-2026-10-13).

## Task 5: Reject overlapping CLI continuations immediately

- **Status:** done
- **Depends on:** Task 4
- **Size:** M
- **Can run in parallel with:** Task 6
- **Docs:** [implementation.md#task-5-reject-overlapping-cli-continuations-immediately](implementation.md#task-5-reject-overlapping-cli-continuations-immediately)

- [x] 5.1 Add `wait=False` admission to `utils/conversation_coordination.py`, skip only clink's outer waiting lock in `server.py`, and acquire once inside `CLinkTool.execute()` through commit/audit.
- [x] 5.2 Add `tests/test_clink_concurrency.py` and run the Task 5 command → verify immediate busy results through direct/MCP-handler calls, API/local queuing, mixed-tool ordering, distinct-thread overlap, and cleanup on errors/cancellation. Direct clink callers no longer pre-acquire coordination.

Completed 2026-10-13: 181 targeted tests passed; full quality checks passed with 2,309 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 5 execution record](implementation.md#task-5-execution-record-2026-10-13).

## Task 6: Configure history budgets per CLI

- **Status:** done
- **Depends on:** Task 4
- **Size:** M
- **Can run in parallel with:** Task 5, Task 7, Task 8, Task 9
- **Docs:** [implementation.md#task-6-configure-history-budgets-per-cli](implementation.md#task-6-configure-history-budgets-per-cli)

- [x] 6.1 Add/forward `history_token_budget` through `clink/models.py` and `clink/registry.py` into the renderer → verify strict positive-integer validation, the default, and client-specific bounds.
- [x] 6.2 Extend `tests/test_clink_history_budget.py` and run the Task 6 command → verify adjustable bounds still account for complete groups and final wrappers.
- [x] 6.3 Document heuristic history-only accounting and restart effects in `docs/configuration.md` → verify examples match configuration fields.

Completed 2026-10-13: 223 targeted tests passed; full quality checks passed with 2,392 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 6 execution record](implementation.md#task-6-execution-record-2026-10-13).

## Task 7: Classify provider settings from shared declarations

- **Status:** done
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** Task 6
- **Docs:** [implementation.md#task-7-classify-provider-settings-from-shared-declarations](implementation.md#task-7-classify-provider-settings-from-shared-declarations)

- [x] 7.1 Add declaration-derived setup diagnostics and required-setting placeholder handling in `providers/configuration.py`; declare the legacy custom-URL placeholder in `providers/custom.py` → verify real values remain unchanged and custom keys remain optional.
- [x] 7.2 Extend `tests/test_provider_configuration.py` and run the Task 7 command → verify OpenCode-Go-only, absence/placeholders, partial Azure/custom, valid-plus-partial, and non-credential restriction/catalogue cases; retain existing bootstrap warnings/errors.

Completed 2026-10-13: 197 targeted tests passed; full quality checks passed with 2,436 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 7 execution record](implementation.md#task-7-execution-record-2026-10-13).

## Task 8: Preserve provider-free startup and call availability

- **Status:** done
- **Depends on:** Task 5, Task 7
- **Size:** M
- **Can run in parallel with:** Task 6
- **Docs:** [implementation.md#task-8-preserve-provider-free-startup-and-call-availability](implementation.md#task-8-preserve-provider-free-startup-and-call-availability)

- [x] 8.1 Add `utils/runtime_capabilities.py` using actual provider registration and Task 7 diagnostics → verify provider-free/no-CLI/clink-disabled startup, API mode with an empty restricted catalogue, and preserved fatal errors.
- [x] 8.2 Add `clink/availability.py` and share executable resolution with the runner → verify lookup semantics match invocation and discovery executes no command.
- [x] 8.3 Guard calls in `server.py` before coordination/history reads using the enabled subset of clink/version/listmodels/planner/tracer/docgen/challenge/apilookup/jules → verify independent tools survive and selected prerequisite errors stay local to their tool.
- [x] 8.4 Add `tests/test_cli_only_startup.py` and run the Task 8 command → verify a first fake-CLI reply without provider use and retained bootstrap behavior. Task 9 completes discovery coherency before release.

Completed 2026-10-13: 203 targeted and 191 related regression tests passed; full quality checks passed with 2,490 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 8 execution record](implementation.md#task-8-execution-record-2026-10-13).

## Task 9: Discover and resume all available independent tools

- **Status:** done
- **Depends on:** Task 8
- **Size:** M
- **Can run in parallel with:** Task 6
- **Docs:** [implementation.md#task-9-discover-and-resume-all-available-independent-tools](implementation.md#task-9-discover-and-resume-all-available-independent-tools)

- [x] 9.1 Apply availability to tool/prompt listings and aliases in `server.py` → verify all nine independent tools, disabled-tool rules, and rejection of unavailable consensus.
- [x] 9.2 Update initialization, `/pal:continue`, and `tools/listmodels.py` → verify absent/incomplete setup hints, unverified authentication, executable observations, and unchanged API-mode behavior; continuation targets must support PAL history.
- [x] 9.3 Add parameterized no-provider coverage in `tests/test_cli_only_discovery.py`, including local challenge/apilookup, docgen first/follow-up/completion, and Jules with fake transport → verify no lost capabilities or unsupported continuation offers.
- [x] 9.4 Update intended surface assertions and run the Task 9 command → verify listings, aliases, calls, and guidance agree.

Completed 2026-10-13: 194 targeted tests passed; full quality checks passed with 2,530 tests passed and 16 skipped. Independent code review found no issues. Live-check and external-review limitations are recorded in the [Task 9 execution record](implementation.md#task-9-execution-record-2026-10-13).

## Task 10: Document local setup and usage

- **Status:** done
- **Depends on:** Task 6, Task 9
- **Size:** M
- **Can run in parallel with:** Task 12, Task 13
- **Docs:** [implementation.md#task-10-document-local-setup-and-usage](implementation.md#task-10-document-local-setup-and-usage)

- [x] 10.1 Update `run-server.sh` and `docs/getting-started.md` with the API/CLI setup paths → verify syntax and source/example consistency; the acceptance-harness walkthrough belongs to Task 14.
- [x] 10.2 Update `docs/tools/clink.md` and `README.md` with requests, continuation, permissions, limits, and failures → verify links and implemented fields.
- [x] 10.3 Verify vendor authentication guidance and shipped arguments; remove the stale Gemini telemetry example → disclose version assumptions and make no subscription-billing claim from a successful reply.

Completed 2026-10-13: Bash syntax, 95 relative links, 17 JSON examples, five clink request shapes, and all three shipped runner command shapes passed validation. Full quality checks passed with 2,530 tests passed and 16 skipped. Independent review found no issues. Live-check and external-review limitations are recorded in the [Task 10 execution record](implementation.md#task-10-execution-record-2026-10-13). PowerShell alignment is next; the acceptance-harness setup walkthrough remains Task 14.

## Task 11: Align PowerShell setup

- **Status:** done
- **Depends on:** Task 10
- **Size:** S
- **Can run in parallel with:** Task 12, Task 13
- **Docs:** [implementation.md#task-11-align-powershell-setup](implementation.md#task-11-align-powershell-setup)

- [x] 11.1 Update both `run-server.ps1` setup branches' messages/templates → verify generated defaults are absent and real supplied endpoints remain configured.
- [x] 11.2 Parse PowerShell syntax without installation/configuration mutations and clarify Windows documentation where needed → verify no login probe or credential management is introduced; Task 14 owns the setup walkthrough.

Completed 2026-10-13: PowerShell 7.6.6 syntax parsing, isolated template/import checks for both branches, 25 documentation links, and 197 provider regression tests passed. Full quality checks passed with 2,530 tests passed and 16 skipped. Independent review approved after correcting an existing PowerShell 7-only expression that contradicted the script's declared 5.1 minimum. Native Windows execution and the full setup walkthrough remain unverified; details and existing live-check failures are in the [Task 11 execution record](implementation.md#task-11-execution-record-2026-10-13).

## Task 12: Prove the real MCP conversation

- **Status:** done
- **Depends on:** Task 6, Task 9
- **Size:** M
- **Can run in parallel with:** Task 10, Task 11
- **Docs:** [implementation.md#task-12-prove-the-real-mcp-conversation](implementation.md#task-12-prove-the-real-mcp-conversation)

- [x] 12.1 Add `tests/test_cli_only_mcp.py` and isolated launcher/fake-CLI fixtures → verify real stdio initialization/discovery/first request/dependent follow-up for all vendor parsers with no installed CLI or credentials.
- [x] 12.2 Inspect prior prompt/reply markers, current edit flags, and unavailable-tool errors; prohibit provider inference/model context → verify fixture credentials cannot satisfy acceptance.
- [x] 12.3 Hold one fake continuation open and require the second same-ID call to return busy before release → verify the actual MCP boundary respects immediate rejection; run the Task 12 command.

Completed 2026-10-13: six real-stdio acceptance cases passed across Gemini, Claude, and Codex parser formats, with provider/dotenv isolation, dependent history, current-request edit permissions, and busy-before-release/retry assertions. Full quality checks passed with 2,536 tests passed and 16 skipped. Independent review approved after correcting synchronization-socket cleanup order for Python 3.12. Installed-vendor/platform limits and existing live-check failures are recorded in the [Task 12 execution record](implementation.md#task-12-execution-record-2026-10-13).

## Task 13: Add optional live checks with truthful skip reporting

- **Status:** done
- **Depends on:** Task 12
- **Size:** M
- **Can run in parallel with:** Task 10, Task 11
- **Docs:** [implementation.md#task-13-add-optional-live-checks-with-truthful-skip-reporting](implementation.md#task-13-add-optional-live-checks-with-truthful-skip-reporting)

- [x] 13.1 Implement pre-execution live-case exclusion and separate `skipped_tests` reporting in `communication_simulator_test.py`, including individual/suite wrappers and all-skipped exit behavior → verify no skipped case becomes a boolean pass.
- [x] 13.2 Extend `tests/test_simulator_sessions.py` with synthetic no-selector/invalid-selector/prerequisite-failure/request-failure/success cases and mixed/all-skipped summaries → run the deterministic runner command without live inference.
- [x] 13.3 Extend `tests/test_clink_integration.py` with explicit selector-gated provider-free first/follow-up tests → verify no selector skips, but opted-in prerequisite/request failures fail.
- [x] 13.4 Add and register `simulator_tests/test_clink_cli_only.py` with selector metadata and its own isolated session/workspace → verify `--individual clink_cli_only` reports SKIPPED with no selector and actual outcomes with a valid selector; record installed version/authentication prerequisites.

Completed 2026-10-13: 67 deterministic runner/session/gate tests passed; full quality checks passed with 2,583 tests passed and 16 skipped. Opted-in live first/follow-up conversations passed with Claude Code 2.1.296 and Codex 0.162.1. No-selector pytest/simulator checks reported skips without claiming coverage. Independent review approved with no findings. Antigravity is installed but its clink integration remains unimplemented; Gemini was not selected. Existing API suite failures and external-review limits remain in the [execution record](implementation.md#task-13-execution-record-2026-10-13). Task 14 is next.

## Task 14: Final verification and setup walkthrough

- **Status:** done
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9, Task 10, Task 11, Task 12, Task 13
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-14-final-verification-and-setup-walkthrough](implementation.md#task-14-final-verification-and-setup-walkthrough)

- [x] 14.1 Run `$kk:test`, `./code_quality_checks.sh`, targeted MCP tests, and supported script syntax checks → record actual deterministic results.
- [x] 14.2 Run the integration script, quick simulator suite, and selected individual CLI/live checks in a configured environment → record missing prerequisites as unmet checks, never passing coverage.
- [x] 14.3 Walk the Bash/PowerShell setup instructions and request examples from Tasks 10–11 against the completed Task 12 fake-CLI harness → verify the full setup path and Task 13 skip/failure summaries.
- [x] 14.4 Run `$kk:document` and `$kk:review-code` with Python language input → validate guidance and fix or durably record implementation findings with reasons.
- [x] 14.5 Run `$kk:review-spec` over all feature documents → verify the final contracts and acceptance before marking the feature complete.

Completed 2026-10-13: final quality checks passed with 2,625 tests passed and 16 skipped; 73 MCP/simulator, 151 parser/runner, and 185 provider checks passed in targeted runs. The approved isolated walkthrough passed three setup workflows and nine fake-CLI conversations; Claude/Codex live checks passed. Corrected malformed-output diagnostics, shipped Azure placeholder handling, and missing-review provenance. Independent code review approved and spec review reported conformance. [Final validation](validation.md) retains broader API-suite failures, external-review authentication limits, and stubbed/platform operations as unmet coverage rather than passing claims.

## Dependency Graph

Tasks 1–2 do not claim Task 3's no-write guarantee. Task 3 preserves existing rendering/coordination temporarily; Tasks 4–5 supply the final history and busy-error behavior. Documentation Tasks 10–11 can run alongside acceptance work because their harness walkthrough is explicitly owned by Task 14.

```text
Task 1 -> Task 2 -> Task 3 -> Task 4
Task 4 -> Task 5
Task 4 -> Task 6
Task 7 (independent provider-declaration slice)
(Task 5 + Task 7) -> Task 8 -> Task 9
(Task 6 + Task 9) -> Task 10 -> Task 11
(Task 6 + Task 9) -> Task 12 -> Task 13
(Tasks 1-13) -> Task 14
```

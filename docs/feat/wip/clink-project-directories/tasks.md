# Tasks: clink project directories

> Design: [design.md](design.md)
> Implementation: [implementation.md](implementation.md)
> Verification: [verification.md](verification.md)
> Review corrections: [review-resolution.md](review-resolution.md)
> Readiness: [fresh review](readiness.md) — Task 2.0 resolves the SDK/model versus directory-error contract; keep both validation boundaries covered.
> Status: in-progress
> Created: 2026-10-15
> Not Doing: automatic project discovery, manifest generation, native permission changes, switching established projects within a conversation, filesystem sandboxing, project locks, relative attachments, persistent conversations, greeting-prompt changes, dependency repair

## Task 1: Define directory selection and identity

- **Status:** done
- **Depends on:** —
- **Size:** S
- **Slicing:** Contract-first; define the new request boundary without exposing an incomplete public API.
- **Can run in parallel with:** Task 4.1 and documentation drafting in Task 5.3
- **Docs:** [selection and identity](implementation.md#task-1-define-directory-selection-and-identity)

- [x] 1.1 Add immutable selections/bindings and typed errors in `clink/working_directory.py`; verify malformed and missing state are distinct.
- [x] 1.2 Implement home expansion, lexical request normalization, preserved manifest spelling, canonical identity and selected-path validation; test the selection table in `tests/test_clink_working_directory.py`.
- [x] 1.3 Use `os.path.samefile()` for aliases/case variants and bound launch-target checks; verify retargeting, missing paths and comparison errors without string fallback.
- [x] 1.4 Run the Task 1 focused command and record results.

Evidence: [Task 1 verification and isolated review](verification.md#task-1-directory-selection-and-identity-2026-10-17).
Focused tests: 98 passed, one host-dependent skip. Comprehensive checks pass;
broader integration/simulator failures and the unavailable external reviewer are
recorded with follow-up steps. At Task 1 completion the helper was not connected
to the public tool; Task 2 supplies that integration.

## Task 2: Integrate project selection and atomic continuation

- **Status:** done
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** Task 4.1 and documentation drafting in Task 5.3
- **Docs:** [request and persistence](implementation.md#task-2-integrate-project-selection-and-atomic-continuation)

- [x] 2.0 Reconcile the design's rejection contract and implementation plan with the [readiness finding](readiness.md#p2-schema-rejection-cannot-return-the-promised-clink-error-envelope): preserve SDK/request-model errors for invalid types and reserve directory-specific metadata for directory-resolution failures. Documentation prerequisite completed before Task 2.1.
- [x] 2.1 Add optional `working_directory_absolute_path` to the request/schema; verify chat-compatible naming/home expansion and unchanged CLI/continuation requirements. Follow the [schema/model validation boundary](readiness.md#p2-schema-rejection-cannot-return-the-promised-clink-error-envelope); keep SDK validation enabled.
- [x] 2.2 Resolve under existing coordination and pass a deep client copy with the launch path to the factory; verify no shared mutation or pre-rejection spawn.
- [x] 2.3 Atomically save both paths in `clink_working_directory`; verify established-project immutability, equivalent aliases, successful scratch-to-first-project promotion with history retained, failed promotion remaining scratch, and unchanged failure/CAS/TTL semantics.
- [x] 2.4 Cover missing-state migration, non-inheritance of chat's raw directory, parent history and API/local interleaving; preserve original provenance checks.
- [x] 2.5 Run the Task 2 focused command and record results.

Evidence: [Task 2 verification and isolated review](verification.md#task-2-request-and-atomic-continuation-2026-10-17).
Focused suite: 360 passed. Final comprehensive checks: 3,225 passed, 17 skipped.
Independent reviewer approved without findings. Existing integration/simulator
failures and unavailable external review remain documented. Detailed directory
metadata and spawn diagnostics remain Task 3; native execution acceptance remains
Tasks 4–6.

## Task 3: Return actionable directory diagnostics

- **Status:** done
- **Depends on:** Task 2
- **Size:** M
- **Can run in parallel with:** Task 4.1 and documentation drafting in Task 5.3
- **Docs:** [directory diagnostics](implementation.md#task-3-return-actionable-directory-diagnostics)

- [x] 3.1 Map directory-helper errors to stable types, validation stage, execution flag, requested/bound paths and new-conversation fields; verify exact payloads and zero state effects. Respect the [readiness finding](readiness.md#p2-schema-rejection-cannot-return-the-promised-clink-error-envelope): SDK/request-model rejections do not promise this directory-specific envelope.
- [x] 3.2 Add PAL-owned launch/identity/source metadata after native merging; test spoof resistance, response reduction, execution errors and null scratch paths.
- [x] 3.3 Replace manifest-only runner wording and classify directory-related spawn failures; verify deletion/access races, cancellation and admission release.
- [x] 3.4 Preserve specific native Codex Git-policy errors with the operator workaround; verify unrelated failures are not relabeled and no bypass/retry occurs.
- [x] 3.5 Run the Task 3 focused command and record results.

Evidence: [Task 3 verification and isolated review](verification.md#task-3-directory-diagnostics-2026-10-18).
Focused suite: 255 passed. Final comprehensive checks: 3,323 passed, 17 skipped.
Independent reviewer approved without findings. Existing integration/simulator
failures and unavailable external review are recorded with follow-up steps.
The full subprocess matrix, MCP acceptance and operator guides remain Tasks 4–6.

## Task 4: Verify native adapters with the shared child fixture

- **Status:** done
- **Depends on:** Task 2, Task 3 for completion; subtask 4.1 has no feature-task dependency
- **Size:** M
- **Can run in parallel with:** Tasks 1–3 for subtask 4.1; Task 5.3 documentation drafting
- **Docs:** [shared fixture/subprocess coverage](implementation.md#task-4-verify-native-adapters-with-the-shared-child-fixture)

- [x] 4.1 Extend existing `tests/fixtures/cli_only_fake.py` with cwd/optional marker capture and Agy framing; preserve stage, duplicate-launch, history and socket-gate assertions and rerun existing MCP tests. Can start immediately.
- [x] 4.2 Add subprocess tests for launch spelling, child filesystem identity, and unchanged Claude edit arguments under symlinked request/manifest cwd.
- [x] 4.3 Overlap project A/B/fallback and bound threads on one client; verify unchanged registry/server cwd, independent launches and cleanup.
- [x] 4.4 Test automatic scratch-only Codex skip flags separately from explicit operator overrides; retain all vendor edit/role, failure and cancellation behavior.
- [x] 4.5 Run the Task 4 focused command and record results.

Evidence: [Task 4 verification and isolated review](verification.md#task-4-shared-fixture-and-subprocess-acceptance-2026-10-18).
Focused suite: 142 passed, two opt-in cases deselected. Final comprehensive checks:
3,381 passed, 17 skipped. Independent reviewer approved without findings. Existing
integration/simulator failures and unavailable external review remain documented.
The subprocess matrix uses fake vendor executables; full MCP directory acceptance,
operator guides and live native CLI observations remain Tasks 5–6.

## Task 5: Exercise MCP clients and explain operator choices

- **Status:** pending
- **Depends on:** Task 2, Task 3, Task 4 for completion; Task 5.3 drafting can start immediately
- **Size:** M
- **Can run in parallel with:** Tasks 1–4 for Task 5.3 drafting only
- **Docs:** [MCP and operator documentation](implementation.md#task-5-exercise-mcp-clients-and-explain-operator-choices)

- [ ] 5.1 Add isolated stdio cases in `tests/test_clink_working_directory_mcp.py` reusing the shared fake; verify four adapters, binding, conflicts, aliases and fresh projects with exact response fields. Cover the [two validation boundaries](readiness.md#p2-schema-rejection-cannot-return-the-promised-clink-error-envelope): wrong JSON types through the SDK's registered handler or real stdio, and directory-resolution errors through both direct and MCP calls; a direct `handle_call_tool()` call does not test SDK validation.
- [ ] 5.2 Verify fallback, explicit scratch-to-first-project promotion, failed promotion and omitted scratch continuation, legacy/API-origin handling and overlap without developer settings/providers leaking into fixtures.
- [ ] 5.3 Update `docs/tools/clink.md` and `docs/configuration.md` for field mapping, `~`, dual paths, transitions, errors, native-configuration activation, deferred operator policy and Codex's non-Git workaround. Draft in parallel; reconcile with tested behavior before completion.
- [ ] 5.4 Run the Task 5 focused command and check document links.

## Task 6: Final verification and documentation

- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [final verification](implementation.md#task-6-final-verification-and-documentation)

- [ ] 6.1 Run `$kk:test`, comprehensive checks, integrations and quick simulator tests; record baseline failures/skips without weakening assertions. Follow up the [Task 1 verification gaps](verification.md#task-1-follow-up-verification-gaps), including consensus replay mismatches and simulator failures.
- [ ] 6.2 Extend the existing Agy opt-in harness and run controlled native project smoke cases in prepared environments; record unavailable coverage.
- [ ] 6.3 Run `$kk:document` and reconcile examples/evidence with actual behavior.
- [ ] 6.4 Run `$kk:review-code` with Python; resolve findings or record specific deferred work.
- [ ] 6.5 Run `$kk:review-spec` against all feature documents and reconcile deviations/statuses.

## Dependencies and parallel work

- Completion order: Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6.
- Task 4.1 owns the shared fixture and compatibility checks; it can run alongside Tasks 1–3.
- Task 5.3 owns the two user guides; drafting can overlap Tasks 1–4, with final validation after the MCP contract is tested.
- Early fixture/docs work does not satisfy parent-task completion dependencies. No dangling graph edges or implicit shared-file ownership.

## Deferred follow-up and known baseline

- **D1:** Possible operator directory allowlist for restricted MCP clients. See the [risk, deferral reason and next steps](implementation.md#deferred-follow-up-operator-directory-policy). This is not implemented by the current feature.
- The earlier TLS hostname failure did not recur in the 2026-10-17 Task 1 baseline or post-change checks; preserve the [historical evidence](verification.md#baseline-quality-checks).
- Tasks 1–4 are complete; Tasks 5–6 remain pending. The public field, atomic binding, directory diagnostics and shared-fixture subprocess matrix are implemented; full MCP directory acceptance, live native observations and operator guides remain.

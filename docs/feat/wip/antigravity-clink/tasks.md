# Tasks: Antigravity clink

> Design: [design.md](./design.md)
> Implementation: [implementation.md](./implementation.md)
> Verification: [verification.md](./verification.md)
> Status: pending
> Created: 2026-10-10
> Reference CLI: agy 1.3.3
> Authentication: existing login/subscription; no required API key
> Not Doing: PAL-managed authentication, mandatory API keys, v1.0.16 compatibility, native session reuse, prompt templates/argv transport, per-path editing, new OS sandbox, persistent user-settings changes, unrelated client fixes

The user approved the direction and all three walkthrough sections. Independent design review is pending. All implementation tasks remain pending; documentary verification is not live capability evidence. Do not begin Task 2 or enable agy until Task 1 establishes the permission contract. Task 1 failure requires a recorded blocker and design revision.

Size reflects substantive behavior; mechanical registry entries, manifest fields, and fixture data are excluded. Each implementation slice includes its own meaningful verification. Task 4 and Task 5 may run in parallel after Task 3 because they own separate behavioral/test surfaces.

## Task 1: Establish the live default and edit contract

- **Status:** pending
- **Depends on:** —
- **Size:** S
- **Can run in parallel with:** —
- **Slicing:** Risk-First
- **Docs:** [Capability contract](./implementation.md#task-1--establish-the-live-capability-contract), [Permission checkpoint](./design.md#permission-checkpoint)

### Subtasks

- [ ] 1.1 Identify native `agy` 1.3.3 and an already-authenticated test environment; create `capability-evidence.md` — verify: version/executable/account class recorded without credentials, and no API key is required for the personal-login case.
- [ ] 1.2 Verify one structured input followed by EOF in a disposable workspace — verify: exactly one complete result and process exit; save a sanitized fixture and record observed deadlines.
- [ ] 1.3 Establish the actual no-edit mechanism under permissive saved settings, including direct and command/tool-mediated writes — verify: sentinel files remain unchanged while reads and a useful response work; saved user configuration is untouched.
- [ ] 1.4 Verify deliberate edits, override precedence, and concurrent safe/edit requests — verify: opted-in fixture edits succeed, conflicting inputs cannot weaken default restrictions, and policy does not leak between processes.
- [ ] 1.5 Record cached, missing, and expired authentication observations and account/platform gaps — verify: errors are explicit and personal execution does not depend on API-key mode; unavailable enterprise coverage is tracked for Task 6.
- [ ] 1.6 Fill the design permission checkpoint and implementation's verified invocation contract with precise evidence and reproduction steps — verify: no guessed flags or enforcement assumptions remain. If not feasible, mark this task blocked with evidence and return to design; leave dependent tasks unstarted.

## Task 2: Complete a default-role request through the shared runner

- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Default request](./implementation.md#task-2--default-role-request-end-to-end), [Input lifecycle](./design.md#input-and-process-lifecycle), [Response contract](./design.md#response-contract)

### Subtasks

- [ ] 2.1 Add `_encode_stdin` in `clink/agents/base.py`, preserving raw UTF-8 by default — verify: existing client stdin bytes and existing agent tests are unchanged.
- [ ] 2.2 Add `AgyAgent` in `clink/agents/agy.py` with one newline-terminated user event and the verified default policy — verify: `CLinkTool.execute()` reaches the controlled subprocess with one correct message and no prompt argv. Reject deliberate edits until Task 4.
- [ ] 2.3 Add `AgyJSONLParser` in `clink/parsers/agy.py` — verify: only one valid successful result with nonempty text is accepted; malformed, absent, duplicate, empty, or unsuccessful results fail.
- [ ] 2.4 Register agent/parser/internal defaults and add `conf/cli_clients/agy.json` with the default role — verify: discovery works, protocol flags occur once, and existing default selection is preserved.
- [ ] 2.5 Validate the complete command and reject protocol-conflicting command-prefix/config/role arguments and output-file mode — verify: print aliases, extra input/output format settings, native continuation, and conflicting split/equals forms fail before execution.
- [ ] 2.6 Add `tests/test_clink_agy.py` with a default-request round trip and a subprocess fixture that waits for EOF — verify: Unicode, quotes, multiline text, and backslashes survive; the process completes and no real CLI is called in unit tests.
- [ ] 2.7 Run the focused Task 2 command from the implementation plan — verify: the new default path and existing client agent/tool regressions pass.

## Task 3: Surface failures and bounded diagnostics

- **Status:** pending
- **Depends on:** Task 2
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Failure path](./implementation.md#task-3--failures-and-bounded-diagnostics-end-to-end), [Diagnostics](./design.md#diagnostics-and-privacy)

### Subtasks

- [ ] 3.1 Exercise failure semantics through `tools/clink.py` and the agy parser — verify: nonzero exits, zero-exit unsuccessful results, malformed/incomplete streams, missing executables, and timeouts become tool errors.
- [ ] 3.2 Select and namespace agy metadata in `clink/parsers/agy.py` — verify: no raw events or CLI fields overwrite PAL command/client/role/return-code/continuation metadata.
- [ ] 3.3 Add agy-only diagnostic handling in the success/error metadata builders — verify: diagnostic text is limited to 2,048 characters with a truncation indicator, raw stdout is absent from error metadata, and existing clients retain their contracts.
- [ ] 3.4 Preserve permission notices alongside usable results — verify: a notice is visible without guessed stderr-based success classification.
- [ ] 3.5 Extend `tests/test_clink_agy.py` for command privacy and no replay — verify: prompt markers stay out of argv, command metadata, and PAL command logs; failure/timeout starts the subprocess only once.
- [ ] 3.6 Run the focused Task 3 command from the implementation plan — verify: error/metadata behavior and existing tool/parser regressions pass.

## Task 4: Honor explicit edits and reject unsupported path scope

- **Status:** pending
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** Task 5
- **Docs:** [Deliberate edits](./implementation.md#task-4--deliberate-edits-and-scope-rejection), [Permission checkpoint](./design.md#permission-checkpoint)

### Subtasks

- [ ] 4.1 Add the verified opt-in path to `clink/agents/agy.py`, replacing Task 2's explicit edit rejection — verify: default/edit behavior matches Task 1 and neither path changes saved settings or authentication.
- [ ] 4.2 Enforce verified precedence for unsafe/conflicting config and role inputs — verify: default mode cannot acquire writes through those arguments or permissive saved settings.
- [ ] 4.3 Extend `tests/test_clink_safe_default.py` for agy path restrictions — verify: unsupported `editable_paths` fails before prompt-file access or subprocess creation.
- [ ] 4.4 Add `tests/test_clink_agy_safety.py` for policy selection and concurrency — verify: requests do not share mutable policy state and existing clients' gating still passes.
- [ ] 4.5 Run live sentinel-file tests with `PAL_RUN_AGY_INTEGRATION=1` and update `capability-evidence.md` — verify: denied writes leave files unchanged and deliberate edits occur as requested; missing prerequisites and violations fail in an opted-in run.

## Task 5: Support roles and PAL continuation

- **Status:** pending
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** Task 4
- **Docs:** [Roles and continuation](./implementation.md#task-5--roles-and-pal-continuation), [Users and acceptance](./design.md#users-and-acceptance-criteria)

### Subtasks

- [ ] 5.1 Add planner/codereviewer bindings to `conf/cli_clients/agy.json` using existing prompt files — verify: a fresh schema lists all three roles and encoded requests contain the corresponding role instructions.
- [ ] 5.2 Add `tests/test_clink_agy_continuation.py` exercising server history preparation before the external boundary — verify: the second request receives prior user/assistant content, uses a new process, and does not use native agy continuation.
- [ ] 5.3 Add `simulator_tests/test_clink_agy_validation.py` and register `clink_agy_validation` — verify: it is listed by the simulator and validates a role-based request plus a history-dependent follow-up.
- [ ] 5.4 Add personal/enterprise live role cases with the integration marker and `PAL_RUN_AGY_INTEGRATION=1` opt-in — verify: no mandatory API-key guard is used; configured CLI failures fail and unavailable account coverage is explicitly recorded.
- [ ] 5.5 Update `docs/tools/clink.md` and README examples/setup — verify: parameter names match the actual schema, the login requirement and upstream limitation are accurate, and the verified default/edit behavior is described.
- [ ] 5.6 Run the focused unit command and individual simulator from the implementation plan — verify: all-role discovery, PAL history, and response-limit behavior remain compatible.

## Task 6: Final verification and documentation

- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Final verification](./implementation.md#task-6--final-verification-and-documentation)

### Subtasks

- [ ] 6.1 Run `$kk:test` over the full feature and repository-required suites — verify: meaningful success/failure cases and all existing-client regressions pass.
- [ ] 6.2 Run `./code_quality_checks.sh`, `./run_integration_tests.sh`, quick simulator mode, and individual `clink_agy_validation` — verify: outcomes and skip reasons are recorded; dedicated agy tests cover the API-key-free path independently of provider-key checks in helpers.
- [ ] 6.3 Complete the 1.3.3 personal/enterprise and safe/edit evidence matrix — verify: A1–A8 are traceable to passing checks; missing account/platform coverage is not presented as complete acceptance.
- [ ] 6.4 Run `$kk:document` and reconcile design/plan/tasks/user docs — verify: final behavior, exact permission mechanism, and unresolved/deferred findings are consistently documented.
- [ ] 6.5 Run `$kk:review-code` with Python input — verify: findings are resolved or durably recorded; blockers prevent completion.
- [ ] 6.6 Run `$kk:review-spec` over the implementation and all feature documents — verify: no missing requirements, stale task state, or unsupported capability claims remain.
- [ ] 6.7 Inspect final packaging/diff, restart the local MCP session for updated code, and confirm discovery — verify: the preset/roles ship, existing clients remain available, and `.capy/knowledge.db` is never committed.

## Dependency Graph

```text
Task 1 -> Task 2 -> Task 3
Task 3 -> Task 4 -> Task 6
Task 3 -> Task 5 -> Task 6

Task 6 verifies the evidence and outputs of every preceding task.
```

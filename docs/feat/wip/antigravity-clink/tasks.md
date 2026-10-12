# Tasks: Antigravity clink

> Design: [design.md](./design.md)
> Implementation: [implementation.md](./implementation.md)
> Verification: [verification.md](./verification.md)
> Review: [review-design.md](./review-design.md)
> Status: pending
> Created: 2026-10-10
> Reference CLI: agy 1.3.3
> Authentication: existing login/subscription; no required API key
> Not Doing: PAL-managed authentication, mandatory API keys, v1.0.16 compatibility, native session reuse, prompt templates/argv transport, per-path editing, new OS sandbox, persistent user-settings changes, unrelated client fixes

The user approved the direction and all three walkthrough sections. Design review completed; its four findings were revalidated and addressed in these documents. All implementation tasks remain pending; documentary verification is not live capability evidence. Do not begin Task 2 or enable agy until Task 1 establishes the permission, filtered-environment, and working-directory contract. Task 1 failure requires a recorded blocker and design revision.

Size reflects substantive behavior; mechanical registry entries, manifest fields, and fixture data are excluded. Each implementation slice includes its own meaningful verification. Task 4 and Task 5 may run in parallel after Task 3 because they own separate behavioral/test surfaces.

## Task 1: Establish the live default and edit contract

- **Status:** pending
- **Depends on:** —
- **Size:** S
- **Can run in parallel with:** —
- **Slicing:** Risk-First
- **Docs:** [Capability contract](./implementation.md#task-1--establish-the-live-capability-contract), [Permission checkpoint](./design.md#permission-checkpoint)

### Subtasks

- [ ] 1.1 Identify native `agy` 1.3.3 and an already-authenticated test environment; create `capability-evidence.md` — verify: version/executable/account class recorded without credentials, and probes use the shared runner's filtered environment without requiring an API key for the personal-login case.
- [ ] 1.2 Verify one structured input followed by EOF through the shared lifecycle in both configured-project and default-scratch cwd modes — verify: exactly one complete result and process exit; save a sanitized fixture, actual cwd/reference locations, and observed deadlines.
- [ ] 1.3 Establish the actual no-edit mechanism under permissive saved/project settings, including direct and command/tool-mediated writes — verify: sentinels remain unchanged and an answer reports a marker read only from a referenced file, including a project outside the scratch cwd; saved user configuration is untouched.
- [ ] 1.4 Verify deliberate edits, override precedence, and concurrent safe/edit requests in both cwd modes — verify: opted-in fixture edits succeed, conflicting inputs cannot weaken default restrictions, and policy does not leak between processes.
- [ ] 1.5 Verify 1.3.3's [authentication passthrough names](./design.md#child-authentication-environment), cached/missing/expired authentication, and account/platform gaps — verify: operator selectors and routing survive the filtered environment, errors are explicit, and personal execution does not depend on API-key mode; unavailable enterprise coverage is tracked for Task 6.
- [ ] 1.6 Fill the design permission checkpoint and implementation's verified invocation contract with exact policy, environment names, cwd/settings scope, and reproduction steps — verify: no guessed flags or enforcement assumptions remain. If useful file access and A4 cannot coexist in either cwd mode, mark this task blocked with evidence and return to design; leave dependent tasks unstarted.

## Task 2: Complete a default-role request through the shared runner

- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Default request](./implementation.md#task-2--default-role-request-end-to-end), [Input lifecycle](./design.md#input-and-process-lifecycle), [Response contract](./design.md#response-contract)

### Subtasks

- [ ] 2.1 Add `_encode_stdin` at `_run_in_directory()`'s communication site in `clink/agents/base.py`, preserving raw UTF-8 by default — verify: existing client stdin bytes, working-directory/progress ownership, and cleanup regressions remain intact.
- [ ] 2.2 Add `AgyAgent` in `clink/agents/agy.py` with one newline-terminated user event and the verified default policy — verify: `CLinkTool.execute()` reaches the controlled subprocess with one correct message and no prompt argv. Reject deliberate edits until Task 4.
- [ ] 2.3 Add `AgyJSONLParser` in `clink/parsers/agy.py` — verify: only one valid successful result with nonempty text is accepted; malformed, absent, duplicate, empty, or unsuccessful results fail.
- [ ] 2.4 Register agent/parser/internal defaults, including Task 1's verified authentication `env_passthrough`, and add `conf/cli_clients/agy.json` with the default role — verify: discovery works, protocol flags occur once, no selector/provider or project cwd is forced, and existing default selection is preserved.
- [ ] 2.5 Validate the complete command and reject protocol-conflicting command-prefix/config/role arguments and output-file mode — verify: print aliases, extra input/output format settings, native continuation, and conflicting split/equals forms fail before execution.
- [ ] 2.6 Add `tests/test_clink_agy.py` with a default-request round trip and a subprocess fixture that waits for EOF; extend `tests/test_clink_environment.py` with agy cases — verify: Unicode, quotes, multiline text, and backslashes survive; synthetic auth/routing values and operator precedence are preserved, absent selectors stay absent, unrelated secrets stay excluded, and actual child cwd matches each mode. No real CLI is called in unit tests.
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
- [ ] 3.5 Extend `tests/test_clink_agy.py` for command privacy and no automatic replay — verify: prompt markers stay out of argv, command metadata, and PAL command logs; failure/timeout starts the subprocess only once per invocation, without claiming deduplication across caller retries.
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
- [ ] 4.3 Extend `tests/test_clink_safe_default.py` through the public decorated call, including an actual user `prompt.txt` input — verify: unsupported `editable_paths` fails before role-prompt loading, agent creation, or subprocess launch. Shared user-prompt admission and turn persistence may already have occurred, as specified by A5.
- [ ] 4.4 Add `tests/test_clink_agy_safety.py` for policy selection and concurrency — verify: requests do not share mutable policy state and existing clients' gating still passes.
- [ ] 4.5 Run live sentinel-file tests through the shared runner in both cwd modes with `PAL_RUN_AGY_INTEGRATION=1` and update `capability-evidence.md` — verify: referenced-file reads work, denied writes leave files unchanged, and deliberate edits occur as requested, including outside the scratch cwd; missing prerequisites and violations fail in an opted-in run.

## Task 5: Support roles and PAL continuation

- **Status:** pending
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** Task 4
- **Docs:** [Roles and continuation](./implementation.md#task-5--roles-and-pal-continuation), [Users and acceptance](./design.md#users-and-acceptance-criteria)

### Subtasks

- [ ] 5.1 Add planner/codereviewer bindings to `conf/cli_clients/agy.json` using existing prompt files — verify: a fresh schema lists all three roles and encoded requests contain the corresponding role instructions.
- [ ] 5.2 Add `tests/test_clink_agy_continuation.py` exercising `conversation_request` and `invocation_history` through MCP dispatch and decorated direct execution, following `tests/test_conversation_ownership.py` — verify: two requests persist four raw turns once; the second stdin contains prior user/assistant content and the current input once, uses a new process, and does not use native agy continuation. Fake only the external process boundary.
- [ ] 5.3 Add `simulator_tests/test_clink_agy_validation.py` and register `clink_agy_validation` — verify: it is listed by the simulator and validates a role-based request plus a history-dependent follow-up.
- [ ] 5.4 Add personal/enterprise live role cases with the integration marker and `PAL_RUN_AGY_INTEGRATION=1` opt-in through the filtered environment and both cwd modes — verify: no mandatory API-key guard is used; codereviewer reports a marker read only from a referenced file, configured CLI failures fail, and unavailable account coverage is explicitly recorded.
- [ ] 5.5 Update `docs/tools/clink.md` and README examples/setup — verify: parameter names match the actual schema; authentication passthrough, cwd/project settings, the upstream limitation, and verified default/edit behavior are described without treating a denial plus successful answer as file-access evidence.
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
- [ ] 6.3 Complete the 1.3.3 personal/enterprise, filtered-environment, both-cwd, and safe/edit evidence matrix — verify: A1–A8 are traceable to passing checks, including actual referenced-file reads; missing account/platform coverage is not presented as complete acceptance.
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

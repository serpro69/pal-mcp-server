# Tasks: Antigravity clink

> Design: [design.md](./design.md)
> Implementation: [implementation.md](./implementation.md)
> Verification: [verification.md](./verification.md)
> Review: [review-design.md](./review-design.md)
> Status: in-progress; permission blocker removed by approved contract revision
> Created: 2026-10-10
> Reference CLI: agy 1.3.5
> Upstream improvement: [agy #1233](https://github.com/google-antigravity/antigravity-cli/issues/1233), not a release dependency
> Authentication: existing login/subscription; no required API key
> Not Doing: PAL-managed authentication, mandatory API keys, v1.0.16 compatibility, native session reuse, prompt templates/argv transport, per-path editing, new OS sandbox, persistent user-settings changes, enforced read-only agy execution, unrelated client fixes

The user approved the initial walkthrough and, on 2026-10-14, revised the contract to inherit agy permissions: false omits PAL's edit-mode flag; true requests `--mode accept-edits`; false is not a read-only guarantee. The stronger downgrade guarantee is deferred to upstream #1233. This removes the permission blocker without marking unrun authentication or production checks as passed. Task 1's remaining authentication verification is in progress; Tasks 2–6 remain pending. See [capability-evidence.md](./capability-evidence.md). Earlier documents report a design review whose file is absent from this checkout; this revision needs a fresh design review.

Size reflects substantive behavior; mechanical registry entries, manifest fields, and fixture data are excluded. Each implementation slice includes its own meaningful verification. Task 4 and Task 5 may run in parallel after Task 3 because they own separate behavioral/test surfaces.

## Task 1: Establish the live transport and inherited-permissions contract

- **Status:** in-progress
- **Remaining:** Personal/ADC and missing/expired-session evidence in 1.1/1.5 remains unverified. The former no-edit enforcement blocker was removed by the user's revised contract; mode argument selection and adapter concurrency checks belong to Tasks 2/4. See [contract revision and remaining work](./capability-evidence.md#contract-revision-and-remaining-work).
- **Depends on:** —
- **Size:** S
- **Can run in parallel with:** —
- **Slicing:** Risk-First
- **Docs:** [Capability contract](./implementation.md#task-1--establish-the-live-capability-contract), [Permission checkpoint](./design.md#permission-checkpoint)

### Subtasks

- [ ] 1.1 Identify native `agy` 1.3.5 and an already-authenticated test environment; create `capability-evidence.md` — verify: version/executable/account class recorded without credentials, and probes use the shared runner's filtered environment without requiring an API key for the personal-login case. **Partial:** enterprise login verified without a forwarded key; personal case unavailable.
- [x] 1.2 Verify one structured input followed by EOF through the shared lifecycle in both configured-project and default-scratch cwd modes — verify: exactly one complete result and process exit; save a sanitized fixture, actual cwd/reference locations, and observed deadlines. **Evidence:** T1/T2 on macOS with enterprise login; CLI help reports default print timeout 0s, with no timeout behavior exercised.
- [x] 1.3 Record useful reads and the inherited-permissions contract — verify: R1/R2b return file-only markers in both cwd modes, operator-managed Non-Workspace Access is recorded, and false is explicitly documented as adding no native mode override rather than enforcing no edits. **Revised requirement:** the former unconditional no-edit proof is deferred upstream, not claimed passed.
- [x] 1.4 Verify native explicit edits in both cwd modes without settings mutation — verify: E2/E3 changed the intended disposable sentinel and retained identical settings hashes in stable repeats. Production argument precedence and per-request argument isolation remain Task 4; native read-only isolation is outside this release's contract.
- [ ] 1.5 Verify 1.3.5's [authentication passthrough names](./design.md#child-authentication-environment), cached/missing/expired authentication, and account/platform gaps — verify: operator selectors and routing survive the filtered environment, errors are explicit, and personal execution does not depend on API-key mode; unavailable enterprise coverage is tracked for Task 6.
- [x] 1.6 Record the approved invocation contract, proposed auth passthrough names and gaps, cwd/setup, and reproduction steps — verify: false inherits settings, true requests `--mode accept-edits`, no downgrade flag is invented, and upstream #1233 has explicit future verification criteria. Authentication cases in 1.5 remain incomplete.

## Task 2: Complete a default-role request through the shared runner

- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Default request](./implementation.md#task-2--default-role-request-end-to-end), [Input lifecycle](./design.md#input-and-process-lifecycle), [Response contract](./design.md#response-contract)

### Subtasks

- [ ] 2.1 Add `_encode_stdin` at `_run_in_directory()`'s communication site in `clink/agents/base.py`, preserving raw UTF-8 by default — verify: existing client stdin bytes, working-directory/progress ownership, and cleanup regressions remain intact.
- [ ] 2.2 Add `AgyAgent` in `clink/agents/agy.py` with one newline-terminated user event and no generated mode flag for false — verify: `CLinkTool.execute()` reaches the process with one correct message and no prompt/mode argv. Reject true until Task 4; false may still permit writes under saved agy policy.
- [ ] 2.3 Add `AgyJSONLParser` in `clink/parsers/agy.py` — verify: only one valid successful result with nonempty text is accepted; malformed, absent, duplicate, empty, or unsuccessful results fail.
- [ ] 2.4 Register agent/parser/internal defaults, including Task 1's verified authentication `env_passthrough`, and add `conf/cli_clients/agy.json` with the default role — verify: discovery works, protocol flags occur once, no selector/provider or project cwd is forced, and existing default selection is preserved.
- [ ] 2.5 Validate the complete command, including both argument buckets; reserve protocol flags and adapter-owned mode selection — verify: print aliases, extra formats, continuation, output-file mode, configured `--mode`, and `--dangerously-skip-permissions` split/equals conflicts fail before execution.
- [ ] 2.6 Add `tests/test_clink_agy.py` with a default-request round trip and a subprocess fixture that waits for EOF; extend `tests/test_clink_environment.py` with agy cases — verify: Unicode, quotes, multiline text, and backslashes survive; synthetic auth/routing values and operator precedence are preserved, absent selectors stay absent, unrelated secrets stay excluded, and actual child cwd matches each mode. No real CLI is called in unit tests.
- [ ] 2.7 Qualify `allow_edits` in both request-model and generated-schema descriptions in `tools/clink.py` before agy becomes discoverable — verify: agy false inherits permissions and has no read-only guarantee; existing-client behavior and `readOnlyHint=false` remain intact.
- [ ] 2.8 Run the focused Task 2 command from the implementation plan — verify: the new default path, schema qualification, and existing client agent/tool regressions pass.

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

## Task 4: Request native edit mode and reject unsupported path scope

- **Status:** pending
- **Depends on:** Task 3
- **Size:** M
- **Can run in parallel with:** Task 5
- **Docs:** [Deliberate edits](./implementation.md#task-4--deliberate-edits-and-scope-rejection), [Permission checkpoint](./design.md#permission-checkpoint)

### Subtasks

- [ ] 4.1 Add adapter-owned `--mode accept-edits` for true, replacing Task 2's rejection — verify: false has no generated mode flag, true has exactly one, and neither path changes saved settings/authentication. Do not duplicate mode flags in the preset.
- [ ] 4.2 Test reserved mode/bypass arguments from all configured sources — verify: overrides fail before execution, while a controlled false-request response may report a write allowed by saved agy policy without being misclassified as a PAL safety violation.
- [ ] 4.3 Extend `tests/test_clink_safe_default.py` through the public decorated call, including an actual user `prompt.txt` input — verify: unsupported `editable_paths` fails before role-prompt loading, agent creation, or subprocess launch. Shared user-prompt admission and turn persistence may already have occurred, as specified by A5.
- [ ] 4.4 Add `tests/test_clink_agy_safety.py` for sequential true-then-false and concurrent calls — verify: PAL's generated edit flag stays request-local, false omits it on follow-ups, and existing-client gating passes. No native downgrade/isolation guarantee is asserted.
- [ ] 4.5 Run opt-in live fixture tests in both cwd modes and update `capability-evidence.md` — verify: content-dependent reads and intended native opt-in edits succeed with stable saved settings. Record default denials or writes against operator-declared policy; do not require universal denial. Missing prerequisites or failures of the declared operation fail the opted-in run.

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
- [ ] 5.5 Update `docs/tools/clink.md` and README examples/setup — verify: schema and docs agree on inherited permissions, possible false-request writes, true's native edit-mode flag, operator-managed external access, and unsupported path scope. Link upstream #1233 and future strengthening criteria; retain authentication/upstream-support caveats and content-dependent file-access evidence.
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
- [ ] 6.3 Complete the 1.3.5 personal/enterprise, filtered-environment, both-cwd, and inherited/explicit-edit evidence matrix — verify: revised A1–A8 are traceable, including reads and accurate permission descriptions; missing account/platform coverage is not complete acceptance. Upstream read-only enforcement is not a release prerequisite.
- [ ] 6.4 Run `$kk:document` and reconcile design/plan/tasks/user docs — verify: inherited-permissions semantics, native mode selection, and upstream/deferred findings are consistently documented.
- [ ] 6.5 Run `$kk:review-code` with Python input — verify: findings are resolved or durably recorded; blockers prevent completion.
- [ ] 6.6 Run `$kk:review-spec` over the implementation and all feature documents — verify: no missing requirements, stale task state, or unsupported capability claims remain.
- [ ] 6.7 Inspect final packaging/diff, restart the local MCP session for updated code, and confirm discovery — verify: the preset/roles ship, existing clients remain available, and `.capy/knowledge.db` is never committed.

## Deferred upstream improvement

- [ ] Revisit enforced permission downgrades when [agy #1233](https://github.com/google-antigravity/antigravity-cli/issues/1233) has a documented release. This is outside Tasks 1–6 and does not block their implementation/release under the accepted contract. Verify the exact version and flags, permissive saved modes, direct and command/MCP writes, both cwd modes, argument precedence, and concurrent requests. If a stronger guarantee is supported, update adapter, tests, schema, guide, and A4 together; a new `default` flag alone is not sufficient evidence.

## Dependency Graph

```text
Task 1 -> Task 2 -> Task 3
Task 3 -> Task 4 -> Task 6
Task 3 -> Task 5 -> Task 6

Task 6 verifies the evidence and outputs of every preceding task.
```

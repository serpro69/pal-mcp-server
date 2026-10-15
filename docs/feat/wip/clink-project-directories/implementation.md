# Implementation: per-request project directories

> Design: [design.md](design.md)
> Tasks: [tasks.md](tasks.md)
> Status: pending; no runtime changes made

## Starting points and constraints

Read `tools/clink.py`'s custom request/schema and `_execute_coordinated()` together
with `CLIConversation` in `clink/conversation.py`. The latter owns the thread
snapshot and atomic success-only commit. `ResolvedCLIClient` is a Pydantic model,
but instances returned by the singleton registry are shared and mutable.
`create_agent()` constructs a fresh agent holding that client reference.

`BaseCLIAgent.run()` owns configured/scratch directory lifetime and passes cwd to
the subprocess. Codex builds its repository-check flag from the client's
`working_dir`, not from a separate `run()` argument. Reuse this connection with a
request-local client copy. Native argument and permission semantics are covered
in the design and must not be generalized into a read-only guarantee.

Use existing Python, Pydantic, asyncio, pytest, and repository helpers; no new
dependency is needed. Keep source changes scoped to this feature. All paths below
are repository-relative. Tests must exercise observable cwd, persistence, and
failure behavior rather than only asserting helper call structure.

## Task 1: Select and bind a conversation's project

**Size: M.** Primary files: `tools/clink.py`, new `clink/working_directory.py`,
`clink/conversation.py`, new `tests/test_clink_working_directory.py`.

1. Add optional strict-string-or-null `working_dir` to the request model and the
   manually maintained MCP schema. Explain server-host paths, fallback,
   inheritance, and immutable project selection. Preserve the existing schema's
   CLI/continuation requirements. **Step → verify:** direct validation and schema
   tests accept omission/null/absolute strings and reject invalid value types;
   existing tool-schema tests still pass.
2. Implement an immutable directory selection and path/binding validation in
   `clink/working_directory.py`. Separate a missing binding from a malformed one.
   Follow the design's table, canonicalization, and selected-path-only validation.
   **Step → verify:** parameterized boundary cases cover explicit precedence,
   configuration/scratch fallback, relative/empty/NUL/nonexistent/file inputs,
   spaces, symlink aliases, changed symlink targets, and malformed stored data.
3. In `_execute_coordinated()`, resolve the directory under existing admission
   after the CLI is selected and before role/history preparation. Deep-copy the
   client and pass the copy to `create_agent()`. Keep original request provenance
   and shared registry objects unchanged. **Step → verify:** captured agent input
   uses the selected directory for each CLI; a valid request overrides a missing
   configured directory; rejection produces no spawn or conversation write.
4. Extend `CLIConversation.commit()` to save `clink_working_directory` atomically
   beside `clink_defaults`. Never persist the actual scratch directory or change
   a binding after success. **Step → verify:** first call and follow-up use one
   binding; same-directory repeats succeed; different projects and scratch-to-
   project changes fail; CLI/role changes retain the directory. CLI failure,
   cancellation and forced CAS/storage failure do not establish a binding or
   refresh TTL. Preserve the returned answer on persistence failure.
5. Add PAL-owned selection metadata to normal/reduced results and post-selection
   CLI failures after native metadata is merged. **Step → verify:** directory,
   source and mode are accurate; scratch has null path; spoofed native metadata
   cannot replace them; failed validation never claims execution.
6. Cover legacy/API-origin and parent-linked threads. Missing binding is migrated
   only by a successful clink commit; raw historical fields and parent binding
   do not select cwd. Existing API/local turns preserve the current thread's
   binding. Update exact-context assertions in `tests/test_clink_continuation.py`
   to expect the new sibling record, preserving their provenance checks.
   **Step → verify:** failed migration leaves the snapshot unchanged; a successful
   round trip through a local/API tool retains the bound directory.

Run `python -m pytest tests/test_clink_working_directory.py
tests/test_clink_continuation.py tests/test_clink_concurrency.py
tests/test_clink_tool.py -q -m 'not integration'` after this slice.

## Task 2: Prove real child behavior and failure cleanup

**Size: M.** Primary files: `clink/agents/base.py`, new
`tests/test_clink_working_directory_subprocess.py`, `tests/test_clink_codex_agent.py`.
Mechanical fixture: new `tests/fixtures/clink_working_directory.py`.

1. Add a deterministic Python child fixture that accepts each adapter's input
   framing, captures cwd/argv into a fixture-owned location, reads a relative
   marker in the project, and emits the adapter's valid response format. It must
   support Agy's stream-json input and output as well as the other three CLIs.
   **Step → verify:** requests through production tool/factory/runner code return
   different markers for projects A/B without any provider/network invocation.
2. Exercise overlapping calls using the same registry client: project A, project
   B, and a call without a directory. The latter must use its own configured or
   scratch fallback. Also exercise continued calls on different bound threads.
   **Step → verify:** each actual child reports its expected cwd; server cwd and
   original client path/arguments are unchanged; configured projects survive and
   scratch directories are removed. Preserve existing same-thread busy behavior.
3. Keep Codex's existing repository policy tied to the effective choice. Do not
   add `--skip-git-repo-check` for a request- or conversation-selected directory.
   **Step → verify:** captured argv includes the flag only for scratch; repeat
   after continuation and CLI switching. Assert other adapters' safe/edit and
   role arguments remain unchanged, including Agy's reserved-option checks.
4. Handle directory-related spawn failures in the runner as `CLIAgentError` with
   the original cause. Distinguish missing cwd from executable-not-found when
   possible; do not label every `FileNotFoundError` as an absent executable.
   Do not catch cancellation as an ordinary error. **Step → verify:** delete a
   selected directory between validation and spawn, simulate denied cwd access,
   and verify explicit errors, no commit, and released admission. Existing
   timeout/cancellation tests must still prove child reaping before scratch
   cleanup; configured project directories are never removed.

Run `python -m pytest tests/test_clink_working_directory_subprocess.py
tests/test_clink_environment.py tests/test_clink_codex_agent.py
tests/test_clink_safe_default.py tests/test_clink_agy_safety.py -q
-m 'not integration'`. These are offline subprocess tests, not live model tests.

## Task 3: Exercise MCP clients and document the contract

**Size: M.** Primary files: new `tests/test_clink_working_directory_mcp.py`,
`docs/tools/clink.md`, `docs/configuration.md`. Reuse the fixture from Task 2 and
the isolated server/bootstrap patterns in `tests/test_cli_only_mcp.py` and
`tests/fixtures/cli_only_server.py`; avoid changing unrelated vendor tests.

1. Start an isolated provider-free MCP server with fake CLI manifests. Use a
   real `ClientSession` to inspect the new schema, select project A, continue
   with omission, repeat A, and reject project B. A fresh conversation may use B.
   Parameterize all four adapters. **Step → verify:** actual child capture and
   local markers prove the MCP arguments reach the runner; rejected changes
   create no child capture, history write, or continuation-state change.
2. Exercise manifest fallback and scratch binding through stdio as well as
   distinct-conversation overlap. Existing helper conventions isolate HOME,
   manifests, state, and provider calls. **Step → verify:** no developer override
   or credential affects the test, source directories persist, and temporary
   runner directories are cleaned up after shutdown/failure.
3. Update the clink request table and examples, selection precedence, immutable
   continuations, explicit null behavior, validation, diagnostics, and native
   permission limitations. Update `docs/configuration.md` to distinguish static
   per-client defaults from request selection, including user-override precedence.
   Explain that MCP schema support does not automatically discover a caller's
   project and that existing continuations become bound on their first successful
   post-upgrade clink call. **Step → verify:** examples agree with stdio tests and
   all local document links resolve; no example implies automatic command approval.

Run `python -m pytest tests/test_clink_working_directory_mcp.py
tests/test_cli_only_mcp.py tests/test_cli_only_discovery.py -q -m 'not integration'`.
Loopback tests need an environment that permits localhost sockets.

## Task 4: Final verification and documentation

**Size: S.** Depends on Tasks 1–3. Record commands, outcomes, skips and limitations
in [verification.md](verification.md), updating task statuses only with evidence.

- Run `$kk:test` for the full feature. Execute `./code_quality_checks.sh`,
  `./run_integration_tests.sh`, and
  `python communication_simulator_test.py --quick` as required by AGENTS.md.
  **Step → verify:** record actual exit statuses and distinguish baseline failures,
  missing credentials/selectors, skips, and new regressions; do not waive failures.
- In each prepared native CLI environment, use a disposable project with a
  file-only marker to check a request-selected directory and an omitted-directory
  follow-up. Record version, native policy, observed cwd and marker result. Start
  with the existing Agy opt-in harness in
  `simulator_tests/test_clink_agy_validation.py`, adding a request-selected mode
  while retaining configured/scratch coverage. **Step → verify:** a file read is
  evidenced by its marker and a project change is rejected before native execution.
  Unavailable native environments remain explicit unverified coverage.
- Run `$kk:document` to reconcile user docs and feature evidence.
  **Step → verify:** no documentation claims project discovery, permission grants,
  read-only confinement, or restart-persistent conversations.
- Run `$kk:review-code` with Python as the implementation language, then
  `$kk:review-spec` against this design, implementation plan and tasks.
  **Step → verify:** resolve findings or record any remaining issue durably with
  its impact, reason for deferral and concrete next step.

## Assumptions, exclusions and known baseline

The [design assumptions](design.md#assumptions) are testable implementation bets,
not verified vendor guarantees. The [scope exclusions](design.md#not-doing) and
[rejected alternatives](design.md#rejected-alternatives) apply to every task.
The project remains caller-selected for each new conversation; no client-side
integration or automatic root discovery is part of this implementation.

The pre-design quality run reproduced the existing TLS hostname failure after
1,302 passing tests; details and the next step are in
[verification.md](verification.md#baseline-quality-checks). Dependency repair is
outside this design task. Final implementation verification must rerun the
original assertion in the locked environment and report any remaining failure.

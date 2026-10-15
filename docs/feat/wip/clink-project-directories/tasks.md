# Tasks: clink project directories

> Design: [design.md](design.md)
> Implementation: [implementation.md](implementation.md)
> Verification: [verification.md](verification.md)
> Status: pending
> Created: 2026-10-15
> Not Doing: automatic project discovery, manifest generation, native permission changes, project switching within a conversation, filesystem sandboxing, project locks, relative attachments, persistent conversations, greeting-prompt changes, dependency repair

## Task 1: Select and bind a conversation's project

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [request and state implementation](implementation.md#task-1-select-and-bind-a-conversations-project)

- [ ] 1.1 Add strict optional `working_dir` to `CLinkRequest` and the MCP schema in `tools/clink.py`; verify valid values and unchanged CLI/continuation requirements.
- [ ] 1.2 Add `clink/working_directory.py` for immutable selections, canonical path validation and saved-binding validation; verify the design's selection table and error cases in `tests/test_clink_working_directory.py`.
- [ ] 1.3 Resolve the invocation directory under existing coordination and pass a deep client copy to `create_agent()`; verify request precedence and no registry/server-cwd mutation.
- [ ] 1.4 Extend `CLIConversation.commit()` to atomically save `clink_working_directory`; verify immutable continuation, scratch binding, CLI switching, failed calls and failed persistence.
- [ ] 1.5 Add PAL-owned selection metadata after native metadata merging; verify spoof resistance, output reduction and post-selection errors without exposing scratch paths.
- [ ] 1.6 Cover legacy/API-origin migration, parent-history non-inheritance and intervening API/local turns; update exact-context assertions while preserving provenance and success-only writes.
- [ ] 1.7 Run the Task 1 focused command and record results.

## Task 2: Prove real child behavior and failure cleanup

- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [subprocess implementation](implementation.md#task-2-prove-real-child-behavior-and-failure-cleanup)

- [ ] 2.1 Add the four-format child fixture in `tests/fixtures/clink_working_directory.py` and subprocess tests in `tests/test_clink_working_directory_subprocess.py`; verify cwd and relative marker reads through the production runner.
- [ ] 2.2 Overlap different projects, bound continuations and fallback calls on the same registry client; verify independent directories, unchanged configuration and correct cleanup.
- [ ] 2.3 Extend Codex repository-policy coverage and all-adapter permission/role argument assertions; verify skip-Git behavior is scratch-only and edit authorization remains current-request-only.
- [ ] 2.4 Normalize directory-related spawn errors in `clink/agents/base.py`; verify missing/denied cwd errors, no commit, released admission and preserved cancellation cleanup.
- [ ] 2.5 Run the Task 2 focused command and record results.

## Task 3: Exercise MCP clients and document the contract

- **Status:** pending
- **Depends on:** Task 1, Task 2
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [MCP and documentation implementation](implementation.md#task-3-exercise-mcp-clients-and-document-the-contract)

- [ ] 3.1 Add `tests/test_clink_working_directory_mcp.py` using isolated stdio servers and the real runner; verify schema, project A continuation, pre-spawn project B rejection and fresh-conversation selection for all four adapters.
- [ ] 3.2 Cover manifest/scratch binding and distinct-conversation overlap through MCP; verify captured cwd, persistence, cleanup and isolation from developer manifests/providers.
- [ ] 3.3 Update `docs/tools/clink.md` and `docs/configuration.md` for request selection, immutable bindings, validation, null semantics and permission limits; verify examples against the tested contract and check links.
- [ ] 3.4 Run the Task 3 focused command and record results.

## Task 4: Final verification

- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [final verification plan](implementation.md#task-4-final-verification-and-documentation)

- [ ] 4.1 Run `$kk:test`, comprehensive quality checks, required integration tests and quick simulator checks; record failures/skips without weakening assertions.
- [ ] 4.2 Extend the opt-in Agy validation harness with request-directory coverage and run native project/continuation smoke cases in prepared CLI environments; record versions, policy, file-read evidence and unavailable coverage.
- [ ] 4.3 Run `$kk:document`; reconcile user-facing examples and verification limits with the implementation.
- [ ] 4.4 Run `$kk:review-code` with Python as language input; resolve findings or record specific deferred work durably.
- [ ] 4.5 Run `$kk:review-spec` against all feature documents; reconcile deviations and update evidence/statuses.

## Dependency Graph

```text
Task 1 --> Task 2 --> Task 3 --> Task 4
   |--------------------^         ^
   |------------------------------|
              Task 2 -------------|
```

## Known baseline

The pre-design comprehensive check passed lint/format and 1,302 unit tests before
an existing TLS hostname assertion failed. See
[baseline evidence and next step](verification.md#baseline-quality-checks).
No runtime implementation task has started, and design fact-checking does not
replace independent design/code/spec review or native CLI acceptance tests.

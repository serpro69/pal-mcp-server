# Design review: corroboration and corrections

> Date: 2026-10-15
> Source checked: `6da3e2b`
> Input: nine findings supplied by the user from an independent design review
> Scope: correct design/implementation/tasks; do not implement runtime changes
> Design: [design.md](design.md)
> Implementation: [implementation.md](implementation.md)
> Tasks: [tasks.md](tasks.md)

Each finding was checked against source and the actual proposal, not accepted on
the strength of the review alone. The knowledge-base entry repeating the review
was treated as provenance, not independent corroboration. All nine identify an
issue worth addressing; several claims need the qualifications below.

## R1 — P1: canonical launch paths

**Valid; fixed.** `CLinkTool._validate_editable_paths()` deliberately uses
`os.path.normpath()` without symlink resolution, with an explicit `/tmp` versus
`/private/tmp` comment. `ClinkRegistry._resolve_path()` returns absolute manifest
paths unchanged. The former design would add realpath conversion before launch,
including for existing manifest users. That is a concrete behavior change even
without proving any vendor-specific trust failure.

Sources: [editable-path validation](../../../../tools/clink.py),
[registry path resolution](../../../../clink/registry.py),
[Claude argument generation](../../../../clink/agents/claude.py).

The design now saves `launch_path` and `canonical_path` separately. Request launch
paths preserve normalized alias spelling; manifest paths preserve the registry's
existing spelling. Continuations retain the original launch path even when an
equivalent alias is supplied. Task 4.2 verifies literal spawn and Claude edit
arguments, plus actual filesystem location. A child OS/CLI may still resolve
aliases internally; passing an alias is not a guarantee about native `getcwd()`
or trust-key implementation.

## R2 — P2: immutable scratch mode

**Valid scope ambiguity; fixed with the user's explicit decision.** The prior user instruction
required a new conversation to change projects; it did not explicitly make
scratch mode a project. Scratch has no stable directory identity. Freezing that
mode can unnecessarily prevent an existing conversation from selecting its first
project. A new PAL conversation need not restart the server, but its automatically
retained history would still be separate.

The user chose "Allow selecting the first project" in this correction pass.
The selection table and persistence contract now allow that transition on the
next successful CAS with history retained. Failure leaves scratch state and TTL
unchanged; omission keeps scratch even if a newly selected CLI has a configured
project. Changing an established project still requires a new conversation.
Tasks 2.3 and 5.2 require direct and MCP coverage of those cases.

## R3 — P2: chat/clink API consistency

**Valid; fixed, with a semantic qualification.** Chat declares
`working_directory_absolute_path` and expands `~` before checking absoluteness
and directory existence. It uses the directory for generated artifacts and has
additional `PAL_WORKSPACE_ROOT`/no-follow restrictions; it is not the same
execution-permission boundary as clink.

Source: [ChatRequest, _validate_file_paths and _artifact_directory](../../../../tools/chat.py).

The proposed clink input now uses the same name and host-side home expansion.
No chat behavior changes, no public `working_dir` alias, and no shared artifact
validator are proposed. The design explicitly forbids adopting a chat-origin
thread's raw directory as a clink binding. Manifest `working_dir`, namespaced
stored binding and `metadata.working_directory` remain distinct internal roles;
chat has no matching response metadata object to rename.

## R4 — P2: native configuration activation

**Valid; fixed as an explicit boundary and follow-up.** Clink launches the native
CLI with a selected cwd and retained native configuration/auth locations; there
is no PAL check that project-local configuration has been reviewed. Claude's
official docs describe project settings/hooks and project MCP definitions,
including different interactive and headless approval behavior.

Sources: [runner](../../../../clink/agents/base.py),
[child environment](../../../../clink/availability.py),
[Claude settings](https://code.claude.com/docs/en/settings),
[Claude project MCP](https://code.claude.com/docs/en/mcp#project-scope).

The design now names prompt-injected cwd selection, local hooks/MCP/instructions,
and the extra capability for a restricted caller without shell access. Native
activation remains version/policy-dependent; this is not proof that all vendors
automatically execute all project configuration. Task 5.3 covers operator docs.
[D1](implementation.md#deferred-follow-up-operator-directory-policy) records a
possible operator-owned project-roots policy, why it needs a separate contract,
and the validation required before restricted-client deployment.

## R5 — P3: Codex outside Git

**Valid documentation/diagnostic gap; fixed.** The adapter automatically supplies
`--skip-git-repo-check` only when client cwd is absent. Local `codex exec --help`
explicitly describes the flag as allowing operation outside a Git repository.
The base command builder preserves operator `additional_args`; its model does
not forbid this flag. Therefore "any non-Git request fails" is too absolute:
an existing operator override can already disable the native check.

Sources: [Codex hook](../../../../clink/agents/codex.py),
[argument assembly](../../../../clink/agents/base.py),
[manifest validation](../../../../clink/models.py), local CLI help (no inference).

Design and Tasks 3.4/4.4/5.3 now distinguish automatic scratch behavior from an
explicit operator override, document the workaround and native failure, and
require targeted diagnostic guidance without broadly reclassifying failures,
adding bypass flags or retrying automatically.

## R6 — P3: path equivalence

**Valid and locally reproduced; fixed.** On this host, differently cased
`/Users/sergio/Projects/personal/pal-mcp-server` and its `PROJECTS` spelling both
exist and satisfy `os.path.samefile()`, but their resolved strings differ.
`os.path.normcase()` leaves the capitalization unchanged. `/tmp` resolves to
`/private/tmp` and both identify the same directory.

The design now names `os.path.samefile()` as the comparison operation, including
saved launch-to-canonical consistency, and treats stat failures explicitly.
Tasks 1.3/4.2 cover case aliases, symlinks, distinct targets and retargeting.
This does not persist inode identity or claim immunity to filesystem races.

## R7 — P3: duplicated child fixture

**Valid; fixed.** The existing fake already consumes staged prompts, records
argv/environment names, rejects duplicate launches, checks history, implements
the socket gate and emits three vendor formats. The proposed parallel fixture
would duplicate those contracts.

Source: [cli_only_fake.py](../../../../tests/fixtures/cli_only_fake.py).

Task 4.1 now extends that fake with cwd/optional marker capture and Agy framing,
preserving existing default scenarios and tests. No parallel wire-format fixture
is proposed. The MCP and subprocess suites reuse the same fixture.

## R8 — P3: task size, parallelism and graph

**Valid planning issue; fixed.** The old Task 1 mixed input/identity design,
public API wiring, atomic state, migration and diagnostics. Exact file count
alone does not prove size L, but its separate risk areas justify splitting it.
Whole downstream tasks were dependent; the review correctly identifies fixture
and documentation subtasks that can start earlier. The old graph was hard to
read and had a dangling-looking repeated Task 2 line.

The revised plan has six S/M tasks: identity contract; request/binding integration;
diagnostics; shared-fixture/subprocess verification; MCP/operator docs; final
verification. The small first task is explicitly contract-first and exposes no
partial public field. Task 4.1 and Task 5.3 have explicit early-start ownership
and later completion gates. A dependency list replaces the ASCII graph as
requested. No runtime task is marked complete by these document edits.

## R9 — P3: pre-spawn errors

**Valid; fixed.** Existing clink errors support free-form metadata and some typed
conversation failures, but the proposal did not define directory-specific fields.
The runner's current "Configured working_dir" message assumes manifest ownership.

Sources: [error envelopes](../../../../tools/clink.py),
[directory validation and spawn errors](../../../../clink/agents/base.py).

The design now specifies stable error types, validation/spawn stages, execution
status, requested/bound paths and new-conversation guidance. Tasks 3.1–3.3 test
the exact fields and no-spawn/no-write behavior, replace source-specific wording,
and preserve OS causes without misclassifying executable errors or cancellation.

## Validation and remaining work

Before edits, the comprehensive checks passed lint/format and 1,302 tests before
the same existing TLS hostname assertion failed (23 integration cases deselected).
Output: `/tmp/clink-project-directories-review-before.log`; the reproducible
failure and next steps remain in [verification.md](verification.md#baseline-quality-checks).
The post-edit comprehensive run also passed lint/format and 1,302 tests, then
stopped at that same assertion (23 deselected), with output in
`/tmp/clink-project-directories-review-after.log`. No assertion, dependency or
runtime source was modified. The final document check validated 74 local links
across five documents, whitespace, six task sections and their referenced
implementation sections. `git diff --check` passed. The remaining edits after
the comprehensive run resolve the user's scratch preference and record results;
they change documentation only and were checked for links/consistency.

All nine findings have corresponding design corrections; R2 uses the user's
explicit choice. D1 is a documented possible policy follow-up, not implemented
enforcement. Native alias/trust behavior and the runtime feature remain
unverified until implementation and acceptance tests. Re-review is still required.

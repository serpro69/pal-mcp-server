# Implementation: per-request project directories

> Design: [design.md](design.md)
> Tasks: [tasks.md](tasks.md)
> Review corrections: [review-resolution.md](review-resolution.md)
> Status: Tasks 1–6 complete; native Codex/Claude verified; unavailable coverage and credential failures recorded

## Starting points and constraints

Read `tools/clink.py`'s custom request/schema with `CLIConversation` in
`clink/conversation.py`. The latter owns the snapshot and atomic successful
commit. Registry clients are mutable and shared; fresh agents retain that client
reference. Pass a deep request-local copy to the factory, never mutate the cached
client or server cwd. Codex's Git-check hook and the shared runner must see the
same selected launch path.

The public input is `working_directory_absolute_path`, matching chat and its
home-expansion behavior. The manifest remains `working_dir`. Chat's directory
controls artifact output, so do not reuse its containment validator or inherit
its raw request field when continuing an API-origin thread. Persist a clink-only
record with separate launch spelling and canonical identity. Follow the exact
selection, comparison and error contracts in the design.

Use existing Python/Pydantic/asyncio/pytest dependencies. Paths below are relative
to the repository. Native settings, manifest defaults and credentials remain
unchanged. The historical comprehensive-check failure is recorded in
[verification.md](verification.md#baseline-quality-checks); it did not recur in
the [Task 1 checks](verification.md#task-1-directory-selection-and-identity-2026-10-17).

## Task 1: Define directory selection and identity

**Size: S. Slicing: Contract-first.** Define the new public input boundary's
selection contract before tool and persistence integration. Primary files:
new `clink/working_directory.py` and `tests/test_clink_working_directory.py`.
This task does not expose a half-implemented public field.

1. Define immutable selection/binding records with mode, launch path, canonical
   path and invocation source. Directory mode requires both absolute paths;
   scratch mode has neither. Distinguish absent state from malformed state.
   **Step → verify:** strict record-shape and invalid-state tests fail explicitly.
2. Implement current-input home expansion and lexical normalization; derive
   identity from the resulting launch path. Preserve registry-resolved manifest
   spelling as supplied, including symlinks and operator `..` semantics.
   **Step → verify:** request `~`, spaces, relative/empty/NUL/non-string input,
   nonexistent paths, files and symlinks follow the design. A valid explicit path
   overrides a missing manifest directory without validating the losing value.
3. Use `os.path.samefile()` for target comparison and saved launch/canonical
   consistency. Preserve the original launch spelling on equivalent repeats.
   **Step → verify:** aliases on a case-insensitive filesystem succeed; different
   directories fail; inaccessible/deleted paths and comparison errors never fall
   back to lexical equality. Host-specific case tests report unavailable coverage,
   alongside deterministic helper tests of the comparison branch.
4. Define typed directory validation/conflict/binding errors and structured data
   without coupling the helper to storage or MCP response generation.
   **Step → verify:** cover the design's selection table and error categories,
   including bound-path retargeting and scratch behavior.

Run `python -m pytest tests/test_clink_working_directory.py -q`.

Implemented in `clink/working_directory.py`: `resolve_working_directory()` takes
the current `requested` value, registry-resolved `configured` path and raw
`saved_binding`. Pass `MISSING_BINDING` only when the namespaced storage key is
absent; a present null is malformed. The result contains an immutable `binding`
and invocation `source`. Persist `binding.to_record()` only on successful commit;
use `to_metadata()` for the selection and typed errors' `to_details()` for domain
diagnostics. These helpers neither persist state nor construct MCP envelopes.
Scratch records contain only `mode`; selection metadata supplies null paths.
The Task 2 integration wraps `binding.launch_path` as the request-local client's
directory and preserves the Task 2.0 SDK/model validation boundary. See the
[test and review evidence](verification.md#task-1-directory-selection-and-identity-2026-10-17).

## Task 2: Integrate project selection and atomic continuation

**Size: M.** Depends on Task 1. Primary files: `tools/clink.py`,
`clink/conversation.py`, new `tests/test_clink_working_directory_requests.py`,
and relevant assertions in `tests/test_clink_continuation.py`.

0. Resolve the [readiness finding](readiness.md#p2-schema-rejection-cannot-return-the-promised-clink-error-envelope)
   before exposing the field: preserve SDK errors for wrong JSON types and the
   existing direct-call request-model validation envelope. Directory-specific
   diagnostics apply only after string/null input reaches the directory helper;
   do not load conversation state just to decorate an invalid type.
1. Expose optional strict-string-or-null `working_directory_absolute_path` in
   `CLinkRequest` and the custom MCP schema. Describe host-side `~` expansion,
   manifest/scratch fallback, inheritance and new-conversation requirements.
   **Step → verify:** direct validation and schema tests accept the same JSON types;
   invoke the SDK's registered handler for wrong-type rejection, since calling
   `handle_call_tool()` directly bypasses schema validation;
   existing CLI/continuation requirements hold. No public `working_dir` alias is
   introduced, and chat's existing input behavior remains unchanged.
2. Under existing admission, resolve the directory after CLI lookup and before
   role/history preparation. Pass a deep client copy with `working_dir=launch_path`
   (or `None` for scratch) to `create_agent()`.
   **Step → verify:** every adapter receives the selection, registry/server cwd
   remain unchanged, and invalid requests fail before spawn.
3. Extend `CLIConversation.commit()` to save `clink_working_directory` beside
   `clink_defaults` in the two-turn CAS. Preserve original provenance; never
   persist temporary paths or invocation source. **Step → verify:** omission
   retains both paths, equivalent aliases preserve launch spelling, different
   projects fail, CLI/role changes retain the project, and failure/CAS cases
   leave binding/history/TTL unchanged. Include scratch-to-first-project success
   with retained history, failed promotion remaining scratch, and omitted scratch
   follow-ups ignoring a newly selected CLI's configured project.
4. Migrate only missing clink binding state. Never adopt a raw chat
   `working_directory_absolute_path`, old `working_dir`, attachment, or parent
   binding. **Step → verify:** API-origin/legacy threads bind only on successful
   clink calls, API/local interleaving preserves the binding, and malformed data
   fails instead of being migrated.

Run `python -m pytest tests/test_clink_working_directory_requests.py
tests/test_clink_continuation.py tests/test_clink_concurrency.py
tests/test_clink_tool.py -q -m 'not integration'`.

Implemented: the request and custom schema accept only string/null directory
values. Resolution runs through `asyncio.to_thread()` under the existing
conversation admission, before role/history preparation. The worker only reads
filesystem and invocation-local binding data; cancellation discards its result
without allowing it to launch a child or commit. A deep client copy receives the
validated launch `Path` (or `None`), and `CLIConversation.commit()` requires the
candidate binding and writes it beside `clink_defaults` in the successful CAS.

The request suite covers real adapter construction with mocked execution and
registered SDK validation. `tests/test_tool_surface.py` also records the exact
intentional schema addition. Task 3 adds the full directory-error envelope
and selected-directory response metadata; Task 2 initially used the existing generic
error envelope with the helper's error type. Native child and stdio acceptance
remain Tasks 4–5. See [Task 2 evidence](verification.md#task-2-request-and-atomic-continuation-2026-10-17).

## Task 3: Return actionable directory diagnostics

**Size: M.** Depends on Task 2. Primary files: `tools/clink.py`,
`clink/agents/base.py`, new `tests/test_clink_working_directory_errors.py`, and
directory cases in `tests/test_clink_execution_errors.py`.

1. Map typed helper failures to the design's pre-spawn envelope: stable
   `error_type`, validation stage, `execution_started=false`, bound record,
   requested string, available normalized/canonical paths, and explicit
   `new_conversation_required`. **Step → verify:** invalid values, project
   conflicts, missing/retargeted paths and corrupt binding return exact fields
   without spawn, history writes, TTL refresh or continuation offer. These are
   directory-resolution failures; SDK/request-model type rejections retain their
   existing envelopes and do not promise directory-specific fields.
2. Add `metadata.working_directory` on selected execution paths with mode,
   launch/canonical paths and source. Set PAL-owned fields after native metadata
   merging and preserve them through output reduction and execution errors.
   **Step → verify:** native output cannot spoof selection; scratch paths are
   null; validation errors do not masquerade as successful selections.
3. Replace the runner's "Configured working_dir" assumption with source-neutral
   wording. Wrap directory-related spawn failures with the OS cause and specified
   spawn-stage fields; distinguish absent executables when possible.
   **Step → verify:** removal between validation/spawn and access denial return
   explicit errors without commit; preserve cancellation, admission release and
   child reaping rather than reclassifying cancellation as a path failure.
4. Preserve Codex's native Git-policy rejection and add guidance for that specific
   diagnostic: select an existing repository or deliberately put
   `--skip-git-repo-check` in manifest `additional_args`, then restart PAL.
   Do not infer rejection from every nonzero exit, require a `.git` directory
   (worktrees may use files), bypass the check, or retry automatically.
   **Step → verify:** a captured native rejection fixture gets the guidance,
   unrelated errors do not, and existing operator skip flags survive.

Run `python -m pytest tests/test_clink_working_directory_errors.py
tests/test_clink_execution_errors.py tests/test_clink_environment.py -q
-m 'not integration'`.

Implemented: typed resolution errors retain the helper's exact path details in
the validation envelope, with no success-shaped selection or continuation offer.
After selection, PAL writes `metadata.working_directory` after native metadata
merging; it survives summary/truncation, native errors, and persistence warnings.
The shared runner reports `WorkingDirectoryLaunchError` for failed directory
preparation, scratch allocation, or cwd-attributed spawn errors. Read-only
prechecks run off the event loop. OS causes remain chained; scratch diagnostics
omit disposable pathnames. Explicit executable failures remain CLI errors;
unattributed relevant OS failures recheck cwd before classification.

Codex guidance matches the captured repository-policy diagnostic as a complete
stderr line, only on failed Codex execution (including runner aliases). It keeps
the native diagnostic and explains the deliberate manifest override without
changing flags or retrying. The fixture was captured from `codex-cli 0.163.0`;
this establishes that rejection's wording, not live model/project acceptance.
See [Task 3 evidence](verification.md#task-3-directory-diagnostics-2026-10-18).

## Task 4: Verify native adapters with the shared child fixture

**Size: M.** Completion depends on Tasks 2–3; subtask 4.1 can start independently.
Primary files: existing `tests/fixtures/cli_only_fake.py`, new
`tests/test_clink_working_directory_subprocess.py`, and
`tests/test_clink_codex_agent.py`.

1. Extend `cli_only_fake.py` rather than creating a parallel vendor-wire fixture.
   Add cwd capture, optional relative file-marker observation, and Agy stream-json
   framing. Preserve stage, duplicate-launch, history and socket-gate checks;
   select optional scenarios explicitly. **Step → verify:** all existing
   `tests/test_cli_only_mcp.py` cases pass and standalone fixture checks cover
   four wire formats. This subtask can run alongside Tasks 1–3.
2. Exercise production tool/factory/runner calls with project markers, omitted
   follow-ups and equivalent-path repeats. Capture literal subprocess cwd
   separately from child `getcwd()`. **Step → verify:** Claude under symlinked
   request/manifest cwd preserves launch spelling and existing non-realpath
   `Edit(...)`/`Write(...)` arguments. Check actual child location with
   filesystem equivalence and markers, not assumed native alias preservation.
3. Overlap A/B/fallback calls on one registry client and distinct bound threads.
   **Step → verify:** independent actual locations, unchanged global cwd/client
   fields, stable saved launch spelling, preserved configured projects, and
   scratch deletion only after direct-child reaping.
4. Verify PAL automatically adds Codex's skip-Git flag only for scratch; an
   operator-supplied flag remains present with a selected directory.
   **Step → verify:** argv in both cases, all vendor safe/edit/role arguments,
   current-request-only edit controls, failure propagation and timeout/cancellation
   cleanup remain correct.

Run `python -m pytest tests/test_clink_working_directory_subprocess.py
tests/test_clink_codex_agent.py tests/test_cli_only_mcp.py
tests/test_clink_safe_default.py tests/test_clink_agy_safety.py -q
-m 'not integration'`. These are offline child-process tests, not model calls.

Implemented with the existing `cli_only_fake.py`: its default `history` scenario
retains the original history, duplicate-launch and held-stage gate assertions.
`CLINK_TEST_SCENARIO` explicitly selects `directory`, `held-directory`, or
`failure` behavior. `CLINK_TEST_MARKER` optionally names a relative marker file;
captures record decoded prompt, raw stdin, argv, cwd, PID, and observed marker.
The same fixture now accepts and emits Agy stream-JSON envelopes.

The subprocess suite loads temporary manifest overrides and uses the production
tool, factory, adapters, runner and parsers. A forwarding spawn observer records
literal cwd/argv; child observations establish actual directory identity and
marker contents. Tests cover request/manifest/scratch selection, saved symlink
spelling on omitted/equivalent continuations, current-request edit controls,
same-client A/B/fallback overlap for new and bound threads, and failure/timeout/
cancellation with unchanged stored bytes and TTL. Scratch cleanup checks that
tracked children have been reaped before deleting their directories. No production
code or native permission flags changed. See
[Task 4 evidence](verification.md#task-4-shared-fixture-and-subprocess-acceptance-2026-10-18).

## Task 5: Exercise MCP clients and explain operator choices

**Size: M.** Completion depends on Tasks 2–4; documentation drafting can start
independently. Primary files: new `tests/test_clink_working_directory_mcp.py`,
`docs/tools/clink.md`, and `docs/configuration.md`.

1. Reuse the extended fake and isolated stdio-server patterns. Through
   `ClientSession`, discover the schema, select A, continue by omission/equivalent
   alias, reject B, and select B in a fresh conversation.
   **Step → verify:** four adapters report the expected location/markers; rejected
   changes return exact structured fields without child capture/state change.
   Include scratch-to-first-project promotion with history retained, failed
   promotion remaining scratch, and legacy/API-origin calls. Wrong JSON types
   must also be tested through the registered SDK handler or real stdio: they
   receive SDK errors before directory resolution, without its metadata.
2. Exercise manifest fallback, request override and distinct-thread overlap.
   **Step → verify:** developer settings/providers cannot affect the isolated
   server; launch spelling, binding, source metadata and cleanup match direct
   calls. Loopback tests require localhost socket access.
3. Draft the public-field/manifest/storage/metadata mapping, `~` handling, dual
   paths, immutable projects, scratch behavior, errors and legacy migration in
   both guides. Name project-local hooks/MCP/instructions activation, headless
   limits, restricted-client exposure, and deferred operator policy below.
   Explain Codex's non-Git rejection and explicit manifest workaround.
   **Step → verify:** examples agree with MCP tests and links resolve; no text
   promises automatic approval, native alias preservation or project discovery.
   Drafting can overlap Tasks 1–4; final reconciliation waits for the tested API.

Run `python -m pytest tests/test_clink_working_directory_mcp.py
tests/test_cli_only_mcp.py tests/test_cli_only_discovery.py -q -m 'not integration'`.

Implemented with 20 new real-stdio cases using the existing isolated launcher
and shared fake child. The launcher can opt into four-vendor manifest overrides,
an isolated home for `~` expansion, seeded legacy/API-origin records, and exact
before/after storage/TTL observations. Its observer wraps the registered SDK
handler, preserving input validation, and forwards the real spawn call to
record literal cwd. None of these hooks are enabled in the production server.
The fake supports an explicitly configured failure stage so a valid project
attempt can fail without replacing scratch state.

The MCP suite covers discovery, inherited/equivalent projects, conflicts and
fresh conversations, both validation boundaries, manifest/request precedence,
successful/failed promotion, migration and malformed state, and concurrent
bound threads across all four adapters. API-origin state is seeded without a
provider call; native responses come from the fake executable. The two operator
guides now document the field mapping, path/binding/error contracts, native
configuration activation, headless limits, D1 and the Codex workaround. Local
links and directory-request JSON examples were validated. See
[Task 5 evidence](verification.md#task-5-mcp-acceptance-and-operator-guides-2026-10-18).

## Task 6: Final verification and documentation

**Size: S.** Depends on Tasks 1–5. Record actual outcomes and limitations in
[verification.md](verification.md); update task statuses only with evidence.

- Run `$kk:test`, `./code_quality_checks.sh`, `./run_integration_tests.sh`, and
  `python communication_simulator_test.py --quick` as required by AGENTS.md.
  **Step → verify:** distinguish baseline failures, skips, unavailable setups and
  new regressions; do not weaken assertions or waive failures.
- Extend existing `simulator_tests/test_clink_agy_validation.py` with request
  directory coverage while retaining configured/scratch cases. In prepared
  native CLI environments, test a disposable project marker and omitted follow-up.
  **Step → verify:** record version, policy, launch argument, actual location and
  read evidence; unavailable native setups remain explicitly unverified. Use
  controlled fixtures for native policy probes, not unreviewed project hooks.
- Run `$kk:document`, `$kk:review-code` with Python, and `$kk:review-spec` against
  all feature documents. **Step → verify:** reconcile implementation/docs and
  resolve findings or record specific deferred work with impact and next steps.

Implemented the Agy harness extension: existing scratch/configuration cases stay
available, and request mode selects a symlinked project over a different manifest
fallback, reads a relative fixture, then omits the directory on continuation.
The native 1.3.5 prerequisite remains strict. Evidence includes selection source,
native policy, literal cwd and optional Linux child-cwd identity observations;
unavailable OS observations are explicit. Offline harness tests cover all three
selection modes, mismatches, lost history and prerequisite failures.

Task 6 also reproduced and repaired the previously recorded verification defects
in their owning test fixtures: consensus request envelopes had stale stance
placement/token caps; the quick chat scenario omitted its required directory,
and precommit supplied only two steps where the current schema requires three.
Recorded responses, exact replay matching and success assertions are retained.
See [cassette provenance](../../../../tests/consensus_cassette_notes.md) and
[Task 6 evidence](verification.md#task-6-final-verification-2026-10-18) for the
native observations, remaining environment/credential gaps and review outcomes.
On resumption, the approved Claude probe also passed marker, retained-history
and actual-child-cwd checks. Task 6 is complete with unavailable Agy/Gemini
coverage recorded as required; the failing API-backed quick cases and external
review still require valid operator credentials. This completion does not waive
those limits or claim full release readiness.

## Deferred follow-up: operator directory policy

**D1 — possible follow-up, not implemented or approval to deploy to restricted
clients.** Caller-selected cwd can activate native project hooks, MCP servers and
instructions. This version retains trusted local CLI delegation. Restricted MCP
callers need a separate operator-controlled policy contract rather than chat's
artifact root or a restriction supplied by the caller itself.

Before supporting that deployment model, design an optional allowed-project-roots
setting owned by the operator. Specify canonical containment, ancestor/project
configuration discovery, manifest/request/continuation precedence, symlink
retargeting, and pre-spawn denial. Validate controlled native hook/MCP fixtures
showing rejected roots cannot start the vendor process. An allowed directory
still would not confine later shell reads/writes; native/host isolation is
separate. Keep D1 visible in operator docs and open a dedicated design when
restricted-client deployment is requested; no hidden policy is promised here.

## Assumptions and known baseline

The [assumptions](design.md#assumptions), [scope exclusions](design.md#not-doing),
and [rejected alternatives](design.md#rejected-alternatives) apply throughout.
Dependency repair is outside this documentation work. Before final implementation
verification, prepare the committed environment and rerun the unchanged TLS
hostname assertion and full checks; retain any remaining failure in the evidence.

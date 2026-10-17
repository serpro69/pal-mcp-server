# Verification: clink project directories

> Date: 2026-10-15
> Source baseline: `6e6ea4a`
> Design: [design.md](design.md)
> Status: historical fact-check retained; Tasks 1–2 implemented and reviewed (2026-10-17)

This record preserves the original pre-review fact-check at `6e6ea4a`.
Its initial naming, single-path binding and scratch decisions are historical,
not the current implementation contract. See [review-resolution.md](review-resolution.md)
for the subsequent review, corroboration at `6da3e2b`, corrections, and the user's
first-project selection decision. The four agents did not review those revisions.
Current implementation evidence is in [Task 2 verification](#task-2-request-and-atomic-continuation-2026-10-17),
following [Task 1](#task-1-directory-selection-and-identity-2026-10-17).

## Task 2: request and atomic continuation (2026-10-17)

Baseline: `42895f6`. Implemented the strict optional directory field in
[`tools/clink.py`](../../../../tools/clink.py), request-local deep client copies,
and binding persistence in [`CLIConversation.commit()`](../../../../clink/conversation.py).
The candidate binding joins the existing defaults and successful input/reply in
one CAS. Failed calls, cancellation and failed/stale writes cannot establish or
promote a project. Missing legacy state migrates on success; existing provenance,
parent linkage and other tools' state remain intact.

Task 2.0 corrected the design before exposing the field: wrong JSON types retain
SDK errors before PAL, direct calls retain request-model validation errors, and
accepted path types reach directory resolution. No SDK validation is disabled.
Task 3 still supplies the complete directory-specific error/selection metadata
and spawn diagnostics; this task preserves the generic tool-error envelope and
helper error type. Actual project subprocesses and end-to-end stdio acceptance
remain Tasks 4–5.

The new [request suite](../../../../tests/test_clink_working_directory_requests.py)
adds 185 cases. It covers the registered SDK handler for both type rejection and
accepted string/null input, all four real adapter classes with execution mocked,
nested client isolation, launch spelling, continuation and CLI/role changes,
scratch promotion, failure/TTL/CAS behavior, parent/raw-field non-inheritance,
API/local interleaving, and overlapping calls on a shared registry client.
A deterministic blocked-resolution test establishes event-loop responsiveness
and that cancellation cannot let the read-only worker spawn or commit later.

`kk:test` Python/async guidance was applied. Checks ran in the existing `pal`
virtualenv (Python 3.10.12, pytest 9.0.3) from the repository root. Commands needing
localhost sockets used approved execution outside the sandbox.

| Check | Observed outcome |
| --- | --- |
| Before edits: `./code_quality_checks.sh` | Exit 0; lint/format passed; 3,040 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| Task 2 focused command in implementation.md | Exit 0; 360 passed. |
| `python -m pytest tests/test_tool_surface.py -q` | Exit 0; 15 passed after explicitly adding the new optional field to the expected schema delta. |
| Final `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 3,225 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| `./run_integration_tests.sh` | Exit 1; two passed, two failed, 19 skipped, 3,242 deselected. The same `gpt-5`/`gpt-5.2` consensus replay cases from Task 1 lack a matching recorded OpenAI chat-completions request. |
| `python communication_simulator_test.py --quick` | Exit 1; four reported passed, two failed. The same basic-conversation and precommit-deduplication missing-continuation failures from Task 1 recur. |

The first full post-change run stopped at the tool-surface snapshot after 2,986
passes. Its expected schema lacked the intentional new field; the test now asserts
exactly string/null with a null default, retaining all surrounding assertions.
The final comprehensive run above passed. Integration/simulator fixture and
setup work remains as described in the [Task 1 follow-up gaps](#task-1-follow-up-verification-gaps);
these checks are not presented as passing or as live project acceptance.

### Task 2 isolated review and remaining scope

The independent `code-reviewer` applied the Python security, SOLID, code-quality
and removal checklists to the production changes, tests, schema expectation and
Task 2.0 documentation. Verdict: **APPROVE**, no P0–P3 findings. It checked the
success-only storage contract, cancellation, isolation, migration and validation
boundaries; it inspected tests without running them. No findings require indexing.

PAL's second review step with `gemini-3.8-flash` again failed with
`400 INVALID_ARGUMENT / API_KEY_INVALID`; no external expert findings were
produced. The isolated workflow used its documented single-reviewer fallback.
External corroboration still requires a working credential; do not infer it from
the review setup step.

No dependencies changed. The API lookup checked the installed/pinned Pydantic
2.13.4 [copy implementation](https://github.com/pydantic/pydantic/blob/v2.13.4/pydantic/main.py)
and [strict-field contract](https://github.com/pydantic/pydantic/blob/v2.13.4/docs/concepts/strict_mode.md),
plus Python 3.10's [thread offload API](https://docs.python.org/3.10/library/asyncio-task.html#asyncio.to_thread).
The copied directory update is already validated and converted to `Path`/`None`,
since model-copy updates are not revalidated. No new project convention needs
separate indexing: the choices and worker cancellation limitation are documented
in the implementation plan.

Task 2 matches its plan. The only additional production mechanism is offloading
filesystem resolution to avoid blocking other requests; the worker owns no
storage or subprocess effects. The additional schema-snapshot assertion was
needed for the intended API change. Broader verification remains incomplete
until the pending tasks and previously recorded integration/simulator gaps close.

## Task 1: directory selection and identity (2026-10-17)

Checkout baseline: `66dcf86`. Scope: the new
[`clink/working_directory.py`](../../../../clink/working_directory.py) helper and
[`tests/test_clink_working_directory.py`](../../../../tests/test_clink_working_directory.py).
Public request/schema, conversation persistence, subprocess and MCP integration
remain Tasks 2–5. This contract-first slice does not change live tool behavior.

The helper implements immutable bindings/selections, strict stored shapes,
request/manifest/scratch precedence, scratch promotion candidates, preserved
launch spelling, canonical identity, `samefile()` comparison/revalidation, and
typed path diagnostics. It never writes state or changes cwd. Existing standard
library APIs and pytest suffice; no dependencies were added or upgraded.

Commands ran from the repository root in `/home/sergio/.virtualenvs/pal`, using
Python 3.10.12 and pytest 9.0.3, outside the read-only sandbox for temporary files
and localhost test sockets. `kk:test` Python guidance was applied. Packaging
requires Python 3.10+, while AGENTS.md/linter targets still name 3.9; this helper
uses postponed annotations and no API requiring a runtime-floor change. Other
interpreter versions and operating systems were not executed.

| Check | Observed outcome |
| --- | --- |
| Before edits: `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 2,942 passed, 16 skipped, 23 integration cases deselected, 19 subtests passed. |
| `python -m pytest tests/test_clink_working_directory.py -q` | Exit 0; 98 passed, one skipped. The real case-insensitive alias test is unavailable on this case-sensitive host; deterministic comparison-branch tests pass. |
| After code changes: `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 3,040 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| `./run_integration_tests.sh` | Exit 1; two passed, two failed, 19 skipped. Both consensus replay cases (`gpt-5`, `gpt-5.2`) fail because the recorder finds no matching interaction for `POST https://api.openai.com/v1/chat/completions`. |
| `python communication_simulator_test.py --quick` | Exit 1; four reported passed, two failed, none skipped. `basic_conversation` did not obtain its initial continuation; `per_tool_deduplication` did not obtain the step-1 precommit continuation. |

The previous TLS hostname assertion passes in both comprehensive runs. Black
reported its existing Python 3.10/3.13 target warning, then completed successfully.
The integration skips cover opt-in native CLI tests and unavailable local-model
configuration. Simulator passes are the harness's reported results: some passing
scenarios also log failed operations, so they are not proof of live acceptance.
The new helper has no production import/caller yet; these broader failed paths
were not changed by Task 1. Their causes were not reproduced on a clean baseline
in this session and are not claimed as fully diagnosed.

### Isolated code review

`kk:review-code:isolated` applied the Python security, SOLID, code-quality and
removal checklists. The independent `code-reviewer` reviewed the two new files,
task status diff, design contract and adjacent registry/runner contracts. Verdict:
**APPROVE**, no P0–P3 findings. Tests were inspected by the reviewer; execution
results above belong to the implementing session. No findings require indexing.

The second reviewer, PAL `codereview` with `gemini-3.8-flash`, completed setup but
its expert step failed with `400 INVALID_ARGUMENT / API_KEY_INVALID`. It produced
no expert findings. The isolated skill's single-reviewer fallback applies; there
is no claim of external-model approval. Existing design decisions already cover
the implementation choices, so no duplicate project-convention indexing was added.

### Task 1 follow-up verification gaps

- **Consensus recordings:** left unchanged because provider replay repair is
  outside the directory-helper task. Before Task 6 acceptance, reproduce both
  cases on the baseline, inspect the recorded/current request mismatch, and
  repair the fixture or request contract in its owning scope; rerun the unchanged
  assertions and integration suite.
- **Quick simulator:** left unchanged because it exercises existing chat and
  precommit flows rather than this unintegrated helper. Reproduce the two failed
  scenarios individually, capture their MCP validation/error responses in an
  approved local environment, correct fixture inputs or provider setup as needed,
  then rerun `--quick`. Do not count missing continuations as success.
- **External review:** configure a valid Gemini credential and rerun the expert
  review when available. The independent sub-agent review is complete; external
  corroboration remains unavailable.
- **Host-dependent coverage:** exercise the real alias test on a case-insensitive
  filesystem and directory access behavior on other supported platforms. Current
  permission-denial coverage uses deterministic stat/access failures.

The attempted additional server-log extraction was rejected by automatic approval
review because log lines could contain sensitive data. No alternate extraction
was attempted; the outcomes above use the test output already returned.

The implementation matches Task 1's contract. The main subtlety was preserving
manifest symlink/`..` traversal while normalizing request `..` lexically before
identity resolution; one real filesystem test establishes both behaviors. Overall
feature verification remains incomplete until the pending tasks and gaps above
are resolved.

## Initial Answer

Add an optional `working_dir` to shared clink requests so a caller can select a
project on each new conversation without editing a global CLI manifest. Accept
an absolute, existing directory on the PAL host, canonicalize it, and reject
invalid input before launching a child. Omission or null on an unbound
conversation uses the selected CLI's configured directory, then the existing
fresh temporary-directory fallback. The explicit request wins over configuration.

The user selected an immutable project per conversation: follow-ups reuse the
saved directory; an explicit different directory requires a new conversation.
The proposed extension binds the effective directory, including one selected by
configuration, on the first successful atomic commit. A scratch choice is stored
as a mode, never as the disposable directory path. Missing directory metadata in
an older or API-origin thread means unbound; its first successful clink call
establishes the binding. Invalid stored binding data must fail explicitly.

Use a request-local copy of `ResolvedCLIClient` with the effective `working_dir`,
then call the existing agent factory and shared runner. The existing subprocess
`cwd` argument supplies the execution directory. Do not change the PAL process
directory or mutate the cached registry. Codex currently inspects the client's
directory when choosing its Git repository-check argument, so its adapter must
see the same effective selection as the runner. Separate calls must preserve
their own directories and native arguments under concurrency.

Persist the binding beside, rather than inside, `initial_context.clink_defaults`:
that object currently tracks the last successful CLI and role and is replaced
on commit. Preserve original request provenance and success-only compare-and-set
semantics. A failed invocation or failed commit must not bind or change the
directory, append a successful exchange, or refresh its TTL. Switching CLI or
role within an existing conversation retains the directory binding.

Expose the selected directory and its source in PAL-owned response metadata.
This is an execution-context choice, not a filesystem sandbox or permission
grant. Existing edit flags, per-request edit authorization, saved native policy,
absolute attachment handling, authentication, and provider-free operation remain
in force. Native project instructions or settings may change with cwd; actual
vendor behavior requires live evidence. No new dependency or automatic project
discovery is proposed.

These are proposed contracts and current-source claims to verify, not completed
implementation or independently verified runtime guarantees.

## Verification questions

1. How does current clink select and resolve its working directory, including
   manifest precedence, cached configuration, and the public request schema?
2. How do the shared runner and individual adapters use the working directory
   during command construction, subprocess execution, and cleanup, and what
   shared state could affect concurrent calls?
3. How does clink load, update, and persist conversation context and selection
   defaults across successful, failed, concurrent, legacy, and API-origin calls?
4. What do clink's existing filesystem and edit-permission controls enforce,
   and what changes or guarantees follow from choosing a child working directory?

## Isolated verification

Four separate general-purpose agents ran with no inherited conversation, initial
answer, or draft design. Each received one question and inspected source/tests
read-only. All completed with citations; none ran tests or native inference.
Their prompts excluded this feature directory. This is source fact-checking,
not an independent architecture/design review or runtime verification.

- [x] Generate the initial answer before verification.
- [x] Formulate four independent verification questions.
- [x] Run one isolated agent per question and collect cited answers.
- [x] Reconcile findings and preserve unverified native-behavior limits.

| Question | Agent and status | Independently observed answer | Sources |
| --- | --- | --- | --- |
| Q1: selection/configuration | `cove_directory_selection`, completed | No request directory exists. Manifest precedence is built-in, configured path, user override; definitions replace rather than merge. Registry clients are cached. Configured relative paths are resolved by registry rules; the runner chooses configured cwd or scratch. | [registry](../../../../clink/registry.py), [models](../../../../clink/models.py), [request/schema](../../../../tools/clink.py), [configuration tests](../../../../tests/test_clink_history_budget.py) |
| Q2: runner/adapters | `cove_runner_lifecycle`, completed | The subprocess receives cwd explicitly. Codex separately reads the client path for its Git-check flag. Agents are fresh but retain the shared mutable client reference. Scratch cleanup follows direct-child reaping; configured directories persist. | [runner](../../../../clink/agents/base.py), [Codex hook](../../../../clink/agents/codex.py), [factory](../../../../clink/agents/__init__.py), [environment tests](../../../../tests/test_clink_environment.py) |
| Q3: persistence | `cove_conversation_state`, completed | Successful calls append two turns and replace CLI/role defaults in one CAS; other context survives. Failed recording preserves the answer with a warning and no continuation offer. Same-thread admission is coordinated; distinct threads overlap. Memory is process-local, not restart-durable. | [conversation](../../../../clink/conversation.py), [storage](../../../../utils/storage_backend.py), [coordination](../../../../utils/conversation_coordination.py), [continuation tests](../../../../tests/test_clink_continuation.py) |
| Q4: permissions | `cove_permission_boundary`, completed | PAL selects native flags and adds prompt instructions; it does not impose a child filesystem sandbox. Agy false inherits native policy. Attachment/edit-path helpers do not confine native filesystem access to cwd. Native settings discovery and write denial cannot be established from source alone. | [clink policy](../../../../tools/clink.py), [Agy adapter](../../../../clink/agents/agy.py), [Claude adapter](../../../../clink/agents/claude.py), [native-policy tests](../../../../tests/test_clink_agy_safety.py) |

## Reconciliation

| Initial claim | Verification | Status and resulting action |
| --- | --- | --- |
| A request field can select cwd without changing a global manifest. | Q1/Q2 | Existing integration points confirmed. This remains proposed functionality; no runtime implementation is claimed. |
| A request-local client copy is needed so runner and adapter hooks agree without shared mutations. | Q1/Q2 | Confirmed. Retain deep-copy design and all-adapter concurrency/argv tests. |
| Directory state belongs beside `clink_defaults`, in the successful CAS. | Q3 | Confirmed. Explicitly preserve original provenance, commit-failure warning semantics and other tools' context updates. |
| A saved directory can support follow-ups. | Q3 | Qualified: the binding lasts only for the process-local thread and its TTL. Restart recovery is excluded. |
| Choosing cwd is not a filesystem permission grant or sandbox. | Q4 | Confirmed for PAL's controls; clarify that native project policy applicability can still change with cwd. Do not describe effective native permissions as unchanged. |
| Project instructions and permissions will behave uniformly across vendors. | Q4 | Not established and not claimed by the initial answer. Final design guarantees launch cwd only; native workspace behavior remains an explicit live-test assumption. |

Q2 also established that different conversations using one project are not
serialized by the existing coordinator. The final design explicitly excludes a
project-wide lock. Q1's manifest path-resolution rules remain unchanged; stricter
absolute input and canonical binding are new request-feature decisions, not
descriptions of current manifest behavior.

The knowledge-base cross-check for native workspace behavior found only older
review guidance requiring file-content evidence under actual runner cwd modes.
It supplies no missing native guarantee. The original fact-check did not catch
the inconsistent launch-path recommendation, missing chat precedent or undefined
case equivalence. The later review identified those design gaps; its corrections
supersede these initial recommendations without changing the historical evidence.

## Final verified answer

The existing registry, agent factory, runner and atomic conversation context are
appropriate integration points for an optional request directory and an immutable
conversation binding. The design carries the selection in a request-local client
copy and persists it only with a successful exchange. Its guarantees stop at
launch context and state isolation; vendor policy discovery, command approval,
filesystem confinement and restart durability are not inferred from those facts.

The detailed proposed contract is in [design.md](design.md). The user's explicit
continuation choice is authoritative. Other detailed choices are reviewable
design recommendations, not claims of additional user approvals.

## Baseline quality checks

The required `./code_quality_checks.sh` was run before creating these documents.
Ruff, Black, and isort passed without source changes. The sandboxed unit run
stopped after 251 passes because a localhost socket bind was denied. The approved
rerun outside the sandbox passed 1,302 tests, then failed
`tests/test_dependency_security.py::test_tls_hostname_uses_idna2008_before_certificate_matching`:
the observed hostname was `fass.de`, while the assertion requires `xn--fa-hia.de`.
Both runs deselected 23 integration cases; `-x` left later tests unrun.

The post-documentation run also passed Ruff, Black and isort, then stopped at the
same assertion after 1,302 passing tests (23 deselected). Its temporary output is
`/tmp/clink-project-directories-quality-after.log`; the result above is retained
here so that log is not a required evidence dependency. No source changes were
produced by the formatting checks.

This is an existing baseline failure, also recorded in
[Antigravity Task 6 verification](../antigravity-clink/task-6-verification.md).
That report identifies installed AnyIO drift as the cause; this design session
has reproduced the failure, not independently diagnosed or repaired that drift.
Dependency repair is outside this documentation task. Before implementation's
final verification, prepare the committed dependency environment and rerun the
unchanged security test and comprehensive checks. Do not weaken the assertion.

No live inference, integration suite, or simulator run is claimed for this
documentation-only task. Existing vendor acceptance evidence remains scoped to
the behavior and versions stated in its own report.

## Documentation checks

The initial pass checked 47 local links and found no trailing whitespace across
four documents. It left runtime code/configuration unchanged and four tasks
pending. The user subsequently supplied an independent design review;
[review-resolution.md](review-resolution.md) tracks its findings. The corrected
breakdown now has six pending tasks. New validation results are recorded with
that correction pass rather than attributed to the old agents.

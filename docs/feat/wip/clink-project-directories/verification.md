# Verification: clink project directories

> Date: 2026-10-15
> Source baseline: `6e6ea4a`
> Design: [design.md](design.md)
> Status: isolated verification complete; implementation has not started

This record preserves the original pre-review fact-check at `6e6ea4a`.
Its initial naming, single-path binding and scratch decisions are historical,
not the current implementation contract. See [review-resolution.md](review-resolution.md)
for the subsequent review, corroboration at `6da3e2b`, corrections, and the user's
first-project selection decision. The four agents did not review those revisions.

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

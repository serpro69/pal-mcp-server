# Verification: clink project directories

> Date: 2026-10-15
> Source baseline: `6e6ea4a`
> Design: [design.md](design.md)
> Status: Tasks 1–5 complete; Task 6 implementation, offline checks and reviews complete; live Claude approval pending (2026-10-18)

This record preserves the original pre-review fact-check at `6e6ea4a`.
Its initial naming, single-path binding and scratch decisions are historical,
not the current implementation contract. See [review-resolution.md](review-resolution.md)
for the subsequent review, corroboration at `6da3e2b`, corrections, and the user's
first-project selection decision. The four agents did not review those revisions.
Current implementation evidence is in [Task 6 verification](#task-6-final-verification-2026-10-18),
following [Task 5](#task-5-mcp-acceptance-and-operator-guides-2026-10-18),
[Task 4](#task-4-shared-fixture-and-subprocess-acceptance-2026-10-18),
[Task 3](#task-3-directory-diagnostics-2026-10-18),
[Task 2](#task-2-request-and-atomic-continuation-2026-10-17) and
[Task 1](#task-1-directory-selection-and-identity-2026-10-17).

## Task 6: final verification (2026-10-18)

Baseline: `3ddbb2e`. Implementation, offline checks and independent reviews are complete.
Native Claude execution needs explicit approval after automatic approval review
rejected the test. The feature is not represented as fully verified or released.

### Harness and baseline repairs

The existing Agy harness now covers scratch, manifest and request selection for
all three roles. Request mode uses a launch symlink over a different configured
fallback, reads a relative fixture, replaces its contents and verifies the old
marker on a directory-omitting follow-up. It checks saved selection metadata,
settings hashes, native argument policy and cleanup. Linux child-cwd observations
use filesystem identity while the child is alive; unsupported/unavailable
observations are recorded explicitly. The native **1.3.5** version assertion is
unchanged. Offline tests exercise success, metadata/cwd/history failures,
unavailable OS observations and missing/incompatible prerequisites.

The two consensus integration failures were reproduced before edits. Comparing
actual requests with all three recordings showed the same unchanged stance block
had moved to the end of the system prompt, and explicit output caps had been
added (OpenAI 80,000; Gemini 65,536). Assertions checked the main prompt, stance,
question and all other generation parameters before migrating the request
envelopes. Recorded responses are identical to `HEAD`, exact request matching is
unchanged, and both consensus replay tests now pass. This was an offline fixture
migration, not a new model recording; see
[provenance notes](../../../../tests/consensus_cassette_notes.md).

Real SDK probes identified the quick scenarios' earlier missing-continuation
causes: chat omitted required `working_directory_absolute_path`; precommit sent
`total_steps=2` below its minimum of three. The scenarios now submit those required
inputs, finish the third precommit step, and reject MCP error results even when
text is nonempty. Regression tests validate outgoing arguments against the real
tool schemas and inject tool errors without weakening existing success checks.
The configured Gemini key is also invalid; live API checks still need valid
operator credentials, separate from these fixture repairs.

### Native observations

All native probes used disposable projects, unchanged shipped argument buckets
and `allow_edits=false`. The probe did not edit native settings/login files or
add bypass flags; each CLI retained its normal authentication behavior. The
probe invokes the same isolated stdio launcher, forwards actual
spawn calls and records literal cwd separately from Linux `/proc` observations.

| CLI | Observed prerequisite and result |
| --- | --- |
| Codex | `codex-cli 0.163.0`, saved ChatGPT login. **Passed**: native read returned the random marker; after replacing the file, an omitted-directory follow-up retained the original marker/history and the saved launch alias. Both child cwd observations matched the disposable Git project; shutdown completed. |
| Claude | `2.1.296`; auth status reports logged in via `claude.ai`/first-party, with inference unverified. Automatic approval review rejected the live smoke command because sending file content to Anthropic was not explicitly authorized for this payload/destination. No Claude inference was run. |
| Agy | Installed `1.4.0`; the existing live acceptance contract requires `1.3.5`. No version assertion was relaxed, native settings were not changed and no live agy inference was attempted. |
| Gemini CLI | Executable unavailable on PATH; native CLI coverage remains unverified. |

Codex's successful run used a new Git repository with an empty template and only
`project.marker`, requested through `/tmp/pal_directory_native_5brjsjlp/launch alias`.
Both observed child paths were `/tmp/pal_directory_native_5brjsjlp/project`.
The first call used source `request`; the follow-up used `conversation` with two
retained messages. Native saved user policy remained in force; safe arguments
were empty and existing `--json --enable web_search_request` arguments were
preserved. No automatic skip-Git flag was added for the selected project.
The temporary project was removed after the test. Earlier probes prohibited
shell commands and did not prove a read; a corrected prompt allowed a read-only
file command, matching Codex's tool interface. Those failed probes are not
reported as successful acceptance.

### Checks and reviews

Commands use the existing `pal` virtualenv (Python 3.10.12, pytest 9.0.3), with
approved execution outside the sandbox for stdio/localhost tests. No dependency
pins changed. `kk:test` and `kk:document` guidance applies.

| Check | Observed outcome |
| --- | --- |
| Before edits: `./code_quality_checks.sh` | Exit 0; 3,401 passed, 17 skipped, 23 deselected, 19 subtests passed; lint/format passed. |
| Unchanged consensus replay baseline | Exit 1; both `gpt-5` and `gpt-5.2` cases failed on missing recorded requests. |
| Migrated consensus replay cases | Exit 0; two passed, one deselected; recorded responses unchanged. |
| Agy harness and simulator contract focused tests | Exit 0; 60 passed, nine live Agy cases deselected. |
| Final `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 3,430 passed, 17 skipped, 26 deselected, 19 subtests passed. |
| `./run_integration_tests.sh` | Exit 0; four passed, 22 skipped, 3,447 deselected. The prior consensus replay failures are resolved. |
| `python communication_simulator_test.py --quick` | Exit 1; four reported passed, two failed. Both basic chat and codereview within per-tool deduplication now reach the provider and return `API_KEY_INVALID`; the stale-schema failures are resolved. |
| `./run_integration_tests.sh --stub` | Exit 0; all three isolated local contract scenarios passed, no skips. |
| Full-feature code review | APPROVE, no P0–P3 findings. Read-only review covered 35 files and the complete feature diff. |
| Full-feature spec review | CONFORMANT; Tasks 1–6, all six feature documents and public guides reviewed. No findings or intentional deviations to index. |
| Local documentation links/anchors | 131 passed across public guides and feature implementation/task/evidence documents. |

The code reviewer inspected selection/identity, atomic persistence, metadata,
cancellation and the direct/subprocess/MCP acceptance tests. It also confirmed
the cassette migrations preserve prompts/stance/question content and recorded
responses while updating placement/caps. No findings require indexing. PAL's
external `gemini-3.8-flash` expert step again failed with `API_KEY_INVALID`; the
isolated workflow used its independent-reviewer fallback, not an external
approval. Code review approval does not close the live validation limitations.

The independent spec reviewer checked precedence, identity, persistence,
migration, diagnostics, adapter arguments, test fixtures and the Task 6 harness
against the complete feature specification. It found no missing implementation,
deviation, doc inconsistency or ambiguity. Its read-only review did not rerun
tests or native commands. Both reviewers distinguish implementation conformance
from the still-open live validation and release-readiness limits.

### Remaining validation actions

- **Claude approval:** the prepared probe is `/tmp/clink-task6-native-probe.py`.
  Approval is required to run its `claude` case, sending the generated marker,
  probe prompts and normal clink role instructions through the saved Claude
  session to Anthropic. The child is asked to read only the generated marker;
  no application source files are attached. The native CLI may also load its
  existing user configuration. Once
  approved, run with the `pal` interpreter and record both marker/history and
  actual-cwd outcomes before closing this item.
- **Agy:** prepare authenticated native 1.3.5 and run the existing opt-in matrix,
  including the new request mode; alternatively review support for 1.4.0 as a
  separate adapter/version change before updating its strict acceptance contract.
- **Gemini:** install/authenticate the CLI in a prepared environment before
  claiming native project-read coverage. Configure a valid PAL Gemini API key
  separately before expecting the default API-backed quick simulator to pass.
- **External review:** the previously documented invalid Gemini credential still
  needs operator repair before an external PAL expert review can be obtained.

These limitations are not waived. Offline transport/schema/storage coverage is
distinct from live vendor policy/authentication coverage and full release
readiness. D1's restricted-client directory policy remains a separate deferred
design, as specified in the implementation plan.

## Task 5: MCP acceptance and operator guides (2026-10-18)

Baseline: `9af43cc`. Added 20 cases in
[`test_clink_working_directory_mcp.py`](../../../../tests/test_clink_working_directory_mcp.py)
using real `ClientSession` stdio discovery/calls against the isolated server and
the shared fake child. No production code, native permissions or dependencies
changed. The server helper's new vendor/manifest/observation options are opt-in;
existing discovery and CLI-only acceptance cases retain their default behavior.

Four cases cover schema discovery, host-side `~`, A/alias inheritance, rejection
of B, a fresh conversation in B, wrong JSON types, invalid strings and unchanged
post-rejection history. Four cover manifest fallback/request override, scratch
inheritance across CLI changes, a failed valid project attempt, successful first
project selection with retained history, and continued project inheritance.
Four cover legacy/API-origin missing-binding migration and corrupt-state
rejection. Eight hold distinct bound A/B/fallback threads concurrently across
four adapters, checking locations, markers, history isolation and cleanup.

The optional launcher observer wraps the original registered SDK handler rather
than bypassing its schema validation. Wrong JSON types retain the SDK's ordinary
error, while path/conflict/binding failures assert the exact directory fields.
Before/after snapshots contain serialized storage payloads and expiry timestamps,
so failure checks cover history, defaults, binding and TTL. Child capture files
establish whether execution occurred; forwarded spawn observations preserve the
literal cwd argument separately from child `getcwd()` and marker reads. Registry
snapshots and server cwd are unchanged. Parent provider/settings poisoning and
the launcher's forbidden-call checks establish isolation.

Legacy/API-origin records are seeded test data, not live API responses. Native
answers come from the fake executable. This establishes MCP transport, SDK
validation and production adapter behavior without vendor/model calls; it does
not establish native hooks, trust prompts, authentication or live project reads.

Updated [clink usage](../../../tools/clink.md) and
[configuration](../../../configuration.md) for public/manifest/storage/metadata
mapping, home expansion, dual paths, continuation transitions, diagnostics and
migration. Both explain project-local configuration activation, headless limits,
restricted-client exposure, deferred D1 and Codex's deliberate operator override.
The existing agy/default-cwd and Codex scratch-only wording was reconciled with
the tested request field and explicit manifest flags.

`kk:test` and `kk:document` guidance was applied in the existing `pal` virtualenv
(Python 3.10.12, pytest 9.0.3, MCP 1.30.0). Test commands used approved execution
outside the sandbox for stdio processes and localhost synchronization gates.

| Check | Observed outcome |
| --- | --- |
| Before edits: `./code_quality_checks.sh` | Exit 0; lint/format passed; 3,381 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| Task 5 focused command in implementation.md | Exit 0; 65 passed, including existing MCP/discovery compatibility. |
| Final `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 3,401 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| Local guide links and directory-request examples | 39 relative links/anchors resolve; four JSON requests validate against `CLinkRequest`. |
| `./run_integration_tests.sh` | Exit 1; two passed, two failed, 19 skipped, 3,418 deselected. The same `gpt-5`/`gpt-5.2` consensus replay cases lack a matching recorded OpenAI chat-completions request. |
| `python communication_simulator_test.py --quick` | Exit 1; four reported passed, two failed, none skipped. The same basic-conversation and precommit-deduplication missing-continuation failures recur. |

These broader failures match the previously recorded gaps and remain outside
Task 5's changed paths. Task 6 must follow the
[existing repair/verification steps](#task-1-follow-up-verification-gaps): inspect
the consensus request/recording mismatch and individual simulator responses,
repair the fixtures or setup in their owning scope, then rerun unchanged
assertions. Neither suite is represented as passing.

### Task 5 review and remaining scope

The independent `code-reviewer` applied the Python security, SOLID, code-quality
and removal checklists. Verdict: **APPROVE**, no P0–P3 findings and no findings
to index. It inspected the tests and guide consistency without executing tests;
runtime evidence belongs to the implementing session. PAL's `gemini-3.8-flash`
external expert step again failed with `400 INVALID_ARGUMENT / API_KEY_INVALID`.
The isolated workflow used its single-reviewer fallback; no external review
approval is claimed. The existing
[credential follow-up](#task-1-follow-up-verification-gaps) remains open.

The SDK observation point was checked against the installed/pinned MCP 1.30.0
implementation and its [tagged source](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/lowlevel/server.py).
Current Context7 results described a different SDK major version and were not
used for that contract. The guide's native-configuration explanation references
Claude's [project settings](https://code.claude.com/docs/en/settings) and
[MCP documentation](https://code.claude.com/docs/en/mcp#project-scope), checked on
2026-10-18; it makes no uniform cross-vendor approval or discovery guarantee.

Task 5 follows its planned scope. No new project convention requires indexing:
the fixture observation hooks and evidence limitations are recorded here and
in the implementation plan. Task 6 remains responsible for prepared native
smoke tests, the outstanding integration/simulator gaps, and the final feature
review. Other platforms/interpreters and live vendor behavior remain unverified.

## Task 4: shared fixture and subprocess acceptance (2026-10-18)

Baseline: `b7ec0cc`. This task changes test infrastructure and acceptance tests;
no production code, dependency pins or native permission flags changed.
The shared [`cli_only_fake.py`](../../../../tests/fixtures/cli_only_fake.py)
retains its default history and duplicate-launch checks and existing socket gate.
It additionally records cwd/PID/raw stdin, optionally reads a relative project
marker, and supports Agy's stream-JSON input/output. Directory, held-directory
and failure scenarios require explicit fixture configuration, so existing MCP
tests keep their original history-dependent behavior.

The new [subprocess suite](../../../../tests/test_clink_working_directory_subprocess.py)
adds 57 cases:

- 12 sequential cases exercise all four adapters with request, manifest and
  scratch selection, omitted/equivalent continuations, saved launch spelling,
  history, and current-request edit/role arguments. Claude's symlinked cwd and
  `Edit(...)`/`Write(...)` arguments are checked independently of child `getcwd()`.
- 16 overlap cases hold A/B/fallback children concurrently on one cached client,
  for both new and distinct bound threads and configured/scratch fallback. A TCP
  barrier proves the children overlap; registry fields and server cwd stay fixed.
- 24 failure/timeout/cancellation cases cover four adapters in project/scratch
  execution. They verify unchanged serialized state/TTL, admission release,
  child reaping, preservation of selected directories and scratch cleanup order.
- One real-child Codex case preserves an explicit manifest skip-Git flag in a
  selected project; the sequential cases separately check automatic scratch-only
  behavior. The existing Codex unit test gains an operator-override parameter.
- Four standalone wire-format cases check first/follow-up replies, missing-history
  rejection and duplicate-launch refusal, including Agy. Existing MCP cases pass
  without being rewritten to fit the extended fixture.

These tests run the real tool/factory/runner/parsers and Python subprocesses
with disposable manifests/projects. The spawn observer forwards the actual OS
call and records literal cwd; child observations are checked with `samefile()`
and relative marker contents. Assertions establish transport, argument
construction and lifecycle behavior. They do not establish live vendor project
configuration, trust-policy or model-read behavior; that remains Task 6.

`kk:test` Python/async guidance was applied. Commands run from the repository root
in `/home/sergio/.virtualenvs/pal` (Python 3.10.12, pytest 9.0.3), using approved
execution outside the sandbox for real child processes and localhost gates.

| Check | Observed outcome |
| --- | --- |
| Before edits: `./code_quality_checks.sh` | Exit 0; lint/format passed; 3,323 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| Task 4 focused command in implementation.md | Exit 0; 142 passed, two opt-in integration cases deselected. |
| Final `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 3,381 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| `./run_integration_tests.sh` | Exit 1; two passed, two failed, 19 skipped, 3,398 deselected. The same `gpt-5`/`gpt-5.2` consensus replay cases lack a matching recorded OpenAI chat-completions request. |
| `python communication_simulator_test.py --quick` | Exit 1; four reported passed, two failed, none skipped. The same basic-conversation and precommit-deduplication missing-continuation failures recur. |

The integration and simulator failures match the earlier verification gaps and
are outside Task 4's changed paths. They remain assigned to Task 6's prerequisite
work: inspect recorded/current request mismatches and individual simulator
validation responses, repair the fixtures or setup in their owning scope, and
rerun unchanged assertions. See the
[specific follow-up steps](#task-1-follow-up-verification-gaps); these suites are
not represented as passing.

### Task 4 review and remaining scope

The independent `code-reviewer` applied the Python security, SOLID, code-quality
and removal checklists. Verdict: **APPROVE**, no P0–P3 findings and no findings
to index. It inspected tests without executing them; the run evidence above
belongs to the implementing session. PAL's `gemini-3.8-flash` expert step again
failed with `400 INVALID_ARGUMENT / API_KEY_INVALID`, yielding no external
findings. The isolated workflow used its single-reviewer fallback. The credential follow-up in
[Task 1's gaps](#task-1-follow-up-verification-gaps) remains open; external review
is not represented as successful.

Python 3.10's documented subprocess and temporary-directory cleanup contracts
were checked. The TCP gate follows the existing peer-first join pattern from
`test_cli_only_mcp.py`; tests own and close accepted streams before joining the
listener. Linux/Python 3.10 was exercised; other platforms/interpreters and native
vendor binaries remain unverified. No new conventions require separate indexing:
the fixture protocol and evidence limitations are recorded here and in the plan.

The task follows its planned scope. Full MCP directory acceptance and public
operator guides remain Task 5; native smoke tests and feature-wide final review
remain Task 6. Existing integration/simulator verification gaps must still be
closed as described in [the earlier follow-up](#task-1-follow-up-verification-gaps).

## Task 3: directory diagnostics (2026-10-18)

Baseline: `6a5a5bb`. Implemented the directory-resolution rejection envelope and
PAL-owned selection metadata in [`tools/clink.py`](../../../../tools/clink.py),
with cwd-specific launch errors in
[`clink/agents/base.py`](../../../../clink/agents/base.py). Resolution errors
preserve exact requested/bound/normalized/canonical details, never offer a
continuation, and do not change history, defaults, binding, or TTL. Selected
metadata overwrites native values and survives normal output, summaries,
truncation, execution errors, and persistence warnings.

The runner retains OS exception causes and distinguishes directory preparation,
scratch allocation, and cwd-attributed spawn failures from executable failures.
When the OS does not name a failing path, only relevant OS errors trigger a cwd
recheck. Cancellation still propagates after child reaping and admission release.
Scratch selection paths stay null, and directory diagnostics do not disclose the
disposable pathname. These checks improve diagnostics; they do not pin directory
contents or eliminate filesystem races.

The [new diagnostic suite](../../../../tests/test_clink_working_directory_errors.py)
adds 74 cases for exact payloads, failure immutability and selection metadata.
The expanded execution/environment suites add real deletion-at-spawn coverage,
deterministic permission and executable failures, scratch allocation and cleanup,
OS cause preservation, cancellation/retry, and Codex policy diagnostics. Existing
request/SDK tests retain the separate wrong-JSON-type boundary.

The [native stderr fixture](../../../../tests/fixtures/codex_git_rejection.stderr)
was captured with `codex-cli 0.163.0`, running
`codex exec --json 'Do not perform any work.'` in `/tmp` without a skip flag.
It exited 1 at the repository check before a model call. No project hooks or
native permissions were changed. PAL adds guidance only for that complete stderr
line on a failed Codex invocation; unrelated errors retain their diagnostics.
The tests also verify no automatic retry/bypass and preserve an explicit manifest
`additional_args` skip flag. This capture does not establish live model or
project-read acceptance.

`kk:test` Python/async guidance was applied in the existing `pal` virtualenv
(Python 3.10.12, pytest 9.0.3). The initial sandboxed baseline stalled in an
existing async test and was interrupted; the approved run outside the sandbox
completed. Subsequent test commands used that same environment outside the sandbox.

| Check | Observed outcome |
| --- | --- |
| Before edits: `./code_quality_checks.sh` | Exit 0; lint/format passed; 3,225 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| Task 3 focused command in implementation.md | Exit 0; 255 passed. |
| Final `./code_quality_checks.sh` | Exit 0; Ruff, Black and isort passed; 3,323 passed, 17 skipped, 23 deselected, 19 subtests passed. |
| `./run_integration_tests.sh` | Exit 1; two passed, two failed, 19 skipped, 3,340 deselected. The same `gpt-5`/`gpt-5.2` consensus replay cases lack a matching recorded OpenAI chat-completions request. |
| `python communication_simulator_test.py --quick` | Exit 1; four reported passed, two failed, none skipped. The same basic-conversation and precommit-deduplication missing-continuation failures recur. |

Integration and simulator failures remain as documented in the
[Task 1 follow-up gaps](#task-1-follow-up-verification-gaps). They are outside
Task 3's changed paths and remain assigned to Task 6's prerequisite verification
work: inspect the recorded/current consensus request mismatch and the individual
simulator validation responses, correct fixtures or setup in their owning scope,
then rerun unchanged assertions. These suites are not presented as passing.

Initial post-change quality attempts caught a shared-fixture import conflict
between Ruff and isort. The test now binds fixture objects from their existing
module without suppressing lint rules; both tools pass. An intermediate full run
passed 3,322 tests before the final scratch-allocation case was added; the final
run above is authoritative. The pre-existing Black runtime/target-version warning
remains unchanged. Git's user-level `indent-with-non-tab` setting rejects Python
space indentation, so the whitespace-only diff check uses repository-compatible
blank-line/trailing-space checks without changing Git configuration.

### Task 3 isolated review and remaining scope

The independent `code-reviewer` applied the Python security, SOLID, code-quality,
and removal checklists to the production diff, tests, and surrounding contracts.
It also inspected the scratch-allocation and fixture-import follow-ups. Verdict:
**APPROVE**, no P0–P3 findings and no findings to index. The reviewer inspected
tests but did not run them; execution evidence belongs to the implementing session.

PAL's external `gemini-3.8-flash` review failed at its expert step with
`400 INVALID_ARGUMENT / API_KEY_INVALID`, producing no expert findings. The
isolated workflow's single-reviewer fallback applies; no external approval is
claimed. The [existing credential follow-up](#task-1-follow-up-verification-gaps)
remains necessary before external corroboration can be obtained.

No dependencies or native permission flags changed. Python 3.10 documentation
and the installed subprocess implementation were checked for stat/access and
cwd/executable error attribution. No new project convention needs indexing:
the implementation notes record these choices. Native error attribution and
permissions on other operating systems remain unverified; exercise them alongside
the existing host-dependent follow-up before broader platform acceptance.

Task 3 matches its planned behavior. The broader four-adapter subprocess matrix,
MCP stdio acceptance, operator guides, and final feature verification remain
Tasks 4–6; this diagnostic slice does not satisfy those acceptance requirements.

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

Task 6 has now repaired the consensus request fixtures and the two stale simulator
schema inputs; see [current results](#task-6-final-verification-2026-10-18).
The notes below preserve why those repairs were deferred at Task 1. Live API
credentials, external review and platform/native coverage remain limited as
specified in Task 6's current action list.

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

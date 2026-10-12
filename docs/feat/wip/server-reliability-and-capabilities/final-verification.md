# Task 42: final verification and release gates

Recorded 2026-10-12 against `64a31b8` plus the Task 42 working changes.
This is the current acceptance record for Tasks 1–41, the actionable follow-up
batch and final review fixes. Earlier validation entries describe historical
checkouts. Task completion records implemented/offline-verified scope and explicit
deferrals; it does **not** declare unrestricted release readiness.

## Changes from final review

- Workflow expert exceptions now raise `ToolExecutionError` after retaining
  failure state, provenance and continuation information. The real MCP handler
  returns `isError=true`; direct workflow callers receive the typed exception.
- Empty and whitespace-only expert replies fail explicitly, clear completion
  flags and do not save a synthetic assistant reply or receive expert-success
  guidance. The original input remains available.
- An oversized attachment restored from an earlier workflow step preserves its
  structured `code_too_large` status and budget diagnostics instead of nesting
  that JSON inside a generic error string. No provider request runs.
- Unknown direct Azure model names raise `ValueError` before client creation,
  instead of dereferencing an unresolved identity.
- Existing concurrency, Unicode, output-budget and model-default regressions now
  assert the typed error contract while preserving their original behavioral
  assertions. Collaboration tests patch the provider lookup actually used by
  `ModelContext`, await the synthetic provider, and assert clarification contents.
  Explicit error handling exposed their previous stale mocks: a real provider
  error had satisfied permissive success/error assertions.

The three expert-error cases, restored-attachment case and Azure case each failed
before their fixes. The focused workflow/protocol/history/file suite passed
**84 tests** before the Azure and test-compatibility additions. Runtime changes
are limited to workflow error handling, ThinkDeep's completion alias and Azure's
unresolved-identity guard; no dependencies or catalogs changed in Task 42.

Documentation now includes the [final architecture](../../../architecture.md),
corrected configuration fences/settings and thinking guidance, current concurrency
semantics, real log paths/rotation, test tiers and entry-point links. The design
distinguishes its original baseline from the final system and records verified
Jules archive methods. Project identity and repository instructions are preserved.

## Automated verification

Fresh environments under `/tmp/pal-task42/py310`, `py311`, and `py312` were created
and installed from **both committed lock files**, then passed `pip check`.
Interpreters: Python **3.10.12**, **3.11.13**, **3.12.11**. Locked key packages:
MCP **1.30.0**, OpenAI **2.41.0**, google-genai **2.8.0**, AnyIO **4.14.2**,
HTTPX **0.28.1**, pytest **9.0.3**, pytest-asyncio **1.4.0**, pip-tools **7.6.1**.

| Check | Result |
| --- | --- |
| Pre-change `./code_quality_checks.sh` | 2,019 passed; Ruff/Black/isort passed |
| Initial fresh-lock unit matrix | 2,019 passed on each interpreter |
| Initial strict stdio matrix | 3/3 scenarios passed on each interpreter |
| Final `./code_quality_checks.sh` on Python 3.12 | 2,024 passed, 19 subtests; Ruff/Black/isort passed |
| Final full units on Python 3.10 / 3.11 | 2,024 passed on each interpreter |
| Final strict stdio matrix, Python 3.10 / 3.11 / 3.12 | 3/3 scenarios passed on each interpreter |
| Runtime/dev lock freshness | Seeded temporary recompilation produced byte-identical locks |
| Distribution contents and installed-wheel MCP session | Passed; details below |
| Rebuilt container health, private state and scan | Passed functional checks; Debian security gate remains open |

Unit command: `python -m pytest tests/ -q -rs --ignore=simulator_tests/ -m "not integration"`.
Each full run excludes 16 integration cases and retains 16 explicit skips: four
live UTF-8 provider checks and twelve native PowerShell cases. The strict tier
uses `python -m simulator_tests.stub_provider`, with metadata-derived credential
isolation, dotenv disabled, temporary state and reaped MCP children. The quality
script runs with an activated environment and `BLACK_NUM_WORKERS=1`.

Lock freshness uses the CI flags `pip-compile --strip-extras --no-header --quiet`
for `pyproject.toml` and `requirements-dev.in`, followed by `cmp` against each
repository lock. Temporary copies were seeded with the existing locks; no upgrades
or repository lock edits occurred. Black retains its existing py313-target warning
when run on Python 3.12; no target or runtime support declaration was changed.

The restricted sandbox stalled an async test; that run was interrupted and the
checks ran with approved subprocess/loopback permissions. Logs, environment
inventories, red/green evidence and smoke helpers are under `/tmp/pal-task42`;
the durable outcomes and gates are recorded here.

## Distribution and container checks

Built wheel and sdist using `python -m build --outdir /tmp/pal-task42/dist`.
Both contain the exact repository LICENSE and NOTICE bytes. The wheel declares
Python >=3.10 and Apache-2.0, includes the OpenCode catalog, and its workflow source
matches the final runtime file. Installed with `--no-deps --no-compile` into a
read-only directory, then launched `python -B -m server` from another cwd.

A real credential-free MCP session passed initialize, tool listing, version,
model listing, local planner execution, transcript index/read and an expected
model-call error. Logs were written outside the package to a path containing
spaces; transcript directory/file modes were 0700/0600. No package logs or bytecode
were written. This verifies installed-package operation on this POSIX host.

| Artifact | SHA-256 |
| --- | --- |
| Wheel | `bf4a8d0a5af7acc169ecba703f7b1977767e349412d67134433570c8355bbb75` |
| Source distribution | `d6adff9bb692d8f38db66138d6ba98db99a712b82532f22b8012852111132398` |
| Final image | `b6f3ca3da0847dd0dea79b14704b8ee74005c6c45a0a2d5693a5a4b5368aa567` |

The image was rebuilt with the unchanged pinned base using `--pull --no-cache`;
after review fixes, application layers were rebuilt using those same dependency
layers. The final image ran as UID/GID 999 with network disabled, a read-only root,
all capabilities dropped, `no-new-privileges`, temporary `/tmp` and a fresh state
volume. Its actual health check passed with a synthetic key. A synthetic memory
write created a private transcript, and pip/setuptools/wheel/ensurepip were absent.
The validation container and volume were removed; no image was published.

Trivy **0.70.0**, `--scanners vuln --ignorefile /dev/null`, found **164 Debian
occurrences: 44 high, 58 medium, 61 low, 1 unknown; zero critical and zero Python
findings detected**. No finding was suppressed. [Final scan identity/checksum](container-scan-task42.json)
records an exact comparison with the retained [finding register](container-scan-actionable-fixes.json)
by advisory, package, installed/fixed version, severity, status and source URL.
All findings match that register. Functional checks and zero detected Python
findings do not clear the Debian gate or establish vulnerability-free software.

## Integration checks and limitations

The required `./run_integration_tests.sh` was exercised with dotenv/provider
credentials disabled and external CLI binaries excluded from its PATH:
**2 passed, 2 failed, 12 skipped**. The failures are both historical consensus
cassettes (`gpt-5`, `gpt-5.2`): changed Chat Completions requests have no matching
recorded interaction. Original recordings were preserved. Ten local-model checks
and two CLI checks were skipped. This run is not live provider validation.

`communication_simulator_test.py --quick` with the same credential isolation
returned nonzero: **2/6 passed**; cross-tool continuation, basic conversation,
content validation and per-tool deduplication could not complete model calls.
That result is recorded as unavailable live coverage, not a successful quick run.
`ollama list` confirmed no running local service. The strict stub's 3/3 scenarios
are the passing local integration contract tier.

## Independent reviews

`kk:review-code:isolated` reviewed the cumulative runtime/test/package/launcher
diff from `c276e0c` through the implemented feature, then re-reviewed Task 42 fixes.
The independent Python reviewer inspected 76 files within a 205-file diff, using
security, SOLID, code-quality and removal checklists. A final four-file
test-compatibility review was **APPROVE**, bringing total inspected coverage to
80 files. This was focused critical-path
review, not exhaustive line review. No P0/P1 findings; final assessment **COMMENT**
with the P2 telemetry follow-up below. The structured file-budget and P3 Azure
findings were corrected. PAL's external review reached step 2 but failed with
`API_KEY_INVALID`; it supplied no independent findings or approval.

`kk:review-spec:isolated` traced Tasks 1–41 and current Task 42 against design,
implementation and tests. It found the two P2 expert-error defects described above;
the fixes and restored-attachment handling were re-reviewed as **CONFORMANT**.
The final feature/doc pass is **DEVIATIONS_FOUND**, limited to the documented
P2 R1 accounting gap below; Task 42 permits that actionable deferral. No remaining
documentation inconsistencies were found. The stale Jules design statement was corrected. Reviewers performed source/test
inspection; test/build execution remains separately evidenced in this record.
No systemic P0/P1 findings or intentional spec deviations required indexing.

## Open release gates and follow-ups

### R1: preserve known usage without an assistant reply

**Priority:** P2. **Owner:** maintainer of conversation/transcript accounting.
**Status:** deferred; complete Task 39 invocation accounting remains open.

Workflow/consensus may receive an empty or blocked response with valid usage and
save failed state on the real user turn. That metadata update emits no transcript
event; the report reads assistant events only. Simple-chat retries that never
produce an answer can likewise lose captured usage. Current reports cover saved
assistant-call records and cannot measure every attempted or billed call. Existing
[report limitations](../../../cache-measurement.md) are now explicit about this case.

Next bounded task: design and implement a versioned per-invocation/provider-call
audit record independent of assistant replies, appended only after the relevant
memory write succeeds. Define stable identity/deduplication across retry,
continuation and replay, plus reader compatibility. Verify empty, blocked,
unsuccessful-retry and failed-consensus responses with known/unknown/zero usage;
no synthetic assistant turns, no aggregate double counting, and explicit coverage
diagnostics. Do not claim complete usage or cost accounting before that acceptance.

| Gate | Required next action |
| --- | --- |
| Debian container advisories | Maintainer follows the [package applicability/remediation register](validation.md#debian-follow-up); verify fixes/base update or justified removals, rebuild without cache and rescan. No blanket clearance. |
| Consensus recordings and live providers | Configure explicitly approved provider targets and re-record the two cassettes with actual responses; run live UTF-8/routing and quick scenarios. Never edit request hashes to manufacture a pass. |
| Local-model reasoning | Start the operator's configured Ollama endpoint/model, then run the local integration and relevant simulator cases. No model was downloaded or default changed here. |
| OpenCode Go | Run the [gateway smoke matrix](../../../opencode-go.md) before promoting hidden candidates. Messages-only entries still require a dedicated adapter and wire tests. |
| Jules | Designate a disposable repository/branch/session and authorize live mutations, then validate create/status/message/approval/archive/unarchive and PR opt-in. Offline contracts remain the implemented scope. |
| Native Windows | Run PowerShell path/launch tests; implement and verify private ACL/reparse-point transcript support before enabling recording. Recording currently fails closed. |
| Live cache cost/quality | Obtain controlled traffic, verified endpoint usage/rates and answer-quality comparisons before claiming benefit or adding explicit provider caches. ADR 0001 retains current history layout. |
| Environment template | Repository security hook blocks `.env.example` access. An authorized maintainer should reconcile it with the updated configuration guide and remove ignored rotation examples; the guide contains the current examples. |

Task C1–C3 prompt investigations are resolved in the [actionable fixes](actionable-fixes.md),
not outstanding release defects. The dated [catalog follow-ups](catalog-reconciliation.md)
remain the authority for unverified model capabilities and promotion decisions.

Reflection: final acceptance exposed errors hidden by successful-looking workflow
responses and permissive tests. Testing the actual MCP result, plus preserving
the same memory assertions, made those failures visible. The final scope also
needed current release gates consolidated across historical records; passing
offline tests cannot settle live service, platform, telemetry or Debian findings.

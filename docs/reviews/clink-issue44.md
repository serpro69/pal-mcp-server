# clink activity receipts — issue 44

Date: 2026-11-20. Baseline: `c962b0ae3529fdbb86c043689737d895e75ca89a`.
Scope: [issue 44](https://github.com/serpro69/pal-mcp-server/issues/44), with Codex
extraction first and shared success-payload pruning. No input-schema, dependency,
permission, invocation, or conversation-storage changes.

The [tool documentation](../tools/clink.md#observed-cli-activity-and-response-limits)
defines receipt coverage, disclosure, sampling, and separate diagnostic/content budgets.
Raw payloads remain available on existing error paths. PAL directory selection and
conversation state remain exact and are outside the optional diagnostic budget.

## Verification

Python 3.10.12, existing `pal` virtualenv:

- Focused regression suite: **508 passed**, including 22 new activity/presentation tests,
  agy diagnostic compatibility, execution errors, and working-directory error paths.
- State/log-directory and tool-surface checks: **50 passed, 12 skipped** (PowerShell
  unavailable). This includes the unchanged MCP input-schema baseline.
- Integration-marked suite: **4 passed, 22 skipped**. Vendor/provider-dependent tests
  were not enabled/configured. Used pytest directly because the shell wrapper's default
  path inspects the protected checkout `.env`.
- `./run_integration_tests.sh --stub`: **3/3 passed**, including continuation,
  workflow-session, and error scenarios against the credential-free loopback provider.
- `communication_simulator_test.py --quick`: failed because no PAL model providers
  were configured; its provider-backed tools were unavailable.
- `codex-cli 0.171.0` synthetic smoke test: successful read-only invocation in `/tmp`,
  with no repository files supplied. Two commands produced completed JSONL items with
  string commands, integer exit codes **0** and **7**, and statuses `completed` and
  `failed`. Started items used `in_progress` and null exit codes. No declined action
  was induced. This validates native event shape, not candidate MCP integration.
- Live Codex-through-clink integration: **1 passed in 16.02 seconds**, exit 0,
  using the candidate server. Command:
  `PAL_SKIP_DOTENV=true PAL_CLINK_LIVE_TEST=codex python -m pytest tests/test_clink_integration.py -q`.
  Verified provider-free startup, the first reply, a dependent follow-up retaining the
  prior input and answer, saved history/continuation identity, clean shutdown, and no
  forbidden provider/dotenv access. Prompts explicitly requested no CLI tool use, so
  command activity remains covered by native smoke evidence and deterministic tests.
  Automatic approval review initially rejected this check; the user then explicitly
  approved it before execution. Captured run: `00fef3b6-1f6a-49e5-bae4-65a42c1f0598`.
- `./code_quality_checks.sh`: **PASSED** — ruff, black, isort, and **3,476 tests passed,
  17 skipped, 26 integration tests deselected, 19 subtests passed**. Final run completed
  with exit 0; test duration 122.13 seconds. Captured run:
  `c21111aa-668d-4cc9-9c50-55bdb13370a3`.

Local test isolation: a temporary pytest plugin points `clink.registry.USER_CONFIG_DIR`
to an empty directory, avoiding the personal Claude manifest that lacks `planner`.
`PAL_SKIP_DOTENV=true` prevents loading checkout credentials. Existing dotenv contract
test families use their synthetic inputs/mocks with the skip switch temporarily
unset and the main process's `_ENV_PATH` redirected to a nonexistent temporary file.
No repository test assertions or product environment semantics were changed.

The initial full run failed after 800 passes because of personal CLI overrides.
Subsequent runs exposed and corrected the candidate's ASCII-escaped Unicode sizing
regression, then isolated unrelated dotenv-test assumptions. These earlier failures
are not passing verification.

## Independent review

The `kk:review-code` isolated workflow supplied the same eight common/Python criteria
and frozen candidate source to an internal code reviewer (OpenAI) and Claude through
PAL. External discovery selected clink: no API providers; Claude was the only installed
eligible client. Anthropic identity was initially assumed from the shipped client name,
then observed as `claude-opus-5-5`. Codex was excluded as the authoring provider; agy
was unavailable and Gemini retired from the review roster. No permissions were changed.

- Internal reviewer: **APPROVE** after corrections, with no remaining actionable
  findings. Its initial P2 identified loss of required directory-selection metadata.
- Claude initial review: **COMMENT**, no P0–P2 findings, three P3 findings: preserve
  PAL control metadata, exercise the budget through `execute()`, and clarify activity
  on parsed failures. All three are addressed. Its control-metadata finding overlaps
  the internal finding; native severities are retained here. Actual successful Read
  tool results establish receipt of all eight criteria, the diff, and affected source.
  No permission denials were reported. Final follow-up: **APPROVE**, no P0–P3 findings;
  updated-source receipt was also observed.
- Claude withdrew the fixture-placement nit after verifying the existing repository
  convention and absence of duplicate test collection. The boolean-counter readability
  nit is fixed. It suggested an optional checked-in native fixture; synthetic tests
  already cover the observed event shape, so no duplicate fixture was added.

No systemic P0/P1 findings to index. Review approval is scoped to inspected code and
the stated verification, not a claim about every deployed vendor version.

## Remaining coverage and follow-ups

- **Native declined-command behavior — owner: unassigned.** The upstream Codex
  `CommandExecutionStatus` schema includes `declined`; fixtures cover it, but a live
  denial was not induced. Next action: capture a safe denial from a configured native
  policy and confirm status/count semantics before claiming live denial coverage.
- **Other CLI extractors — owner: unassigned.** Deferred within the accepted Codex-first
  scope. Confirm each configured output's activity schema and add parser fixtures plus
  bounded extraction before exposing activity; absence continues to mean unknown.
- **Provider-backed quick mode — owner: unassigned/operator.** Configure a test provider
  and rerun the six quick scenarios to obtain that coverage. The three offline stub
  scenarios above do not establish the same live model behavior.
- **Unrelated test-order dependency — owner: unassigned.** A diagnostic invocation
  placing `test_uvx_support.py` before `test_state_log_dirs.py` failed logger-handler
  checks after uvx tests replaced the server module. The state/log tests passed in
  isolation; the final quality run retains standard repository order. No clink code
  participates in those failures. Deferred outside issue 44; next action is to isolate
  server/logger ownership in those fixtures and verify the reversed order too.

Restart PAL/the MCP client session to activate the changed server code.

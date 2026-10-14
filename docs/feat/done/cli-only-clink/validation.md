# Final validation: CLI-only conversations through clink

> Date: 2026-10-13
> Scope: [Task 14](tasks.md#task-14-final-verification-and-setup-walkthrough), following commit `28d3816`
> Status: verification complete; external-service and platform limits recorded below
> Contracts: [design.md](design.md), [implementation.md](implementation.md)

## Deterministic checks

Validation uses Python 3.10.12 in `/home/sergio/.virtualenvs/pal`, without dependency changes. MCP subprocess tests run outside the command sandbox because its restrictions stall existing async teardown tests. These results establish the tested Linux environment, not a full Python/platform matrix.

| Check | Actual result |
| --- | --- |
| Baseline `./code_quality_checks.sh` | 2,583 passed, 16 skipped, 15 integration cases deselected, 19 subtests passed; Ruff, Black, and isort passed. |
| Final `./code_quality_checks.sh` after parser and Azure corrections | 2,625 passed, 16 skipped, 15 integration cases deselected, 19 subtests passed; Ruff, Black, and isort passed. |
| `python -m pytest tests/test_cli_only_mcp.py tests/test_simulator_sessions.py -q` | 73 passed. Covers all three vendor formats through MCP stdio, dependent history, edit-permission reset, busy-before-release/retry, selector handling, separate skip accounting, and session ownership. |
| New malformed-output regressions before fixes | 26 failed and 12 passed, reproducing the unchecked nested-shape errors and acceptance of malformed completed items. |
| Parser/runner suite after fixes | `tests/test_clink_execution_errors.py`, `tests/test_clink_parsers.py`, `tests/test_clink_codex_agent.py`, and `tests/test_clink_gemini_agent.py`: 151 passed. |
| Provider suite after walkthrough correction | `tests/test_provider_configuration.py` and `tests/test_provider_configuration_errors.py`: 185 passed, including shipped Azure placeholder handling and preserved real endpoint behavior. |
| Bash syntax | `bash -n run-server.sh` passed. |
| PowerShell syntax | PowerShell 7.6.6 `Parser.ParseFile` accepted `run-server.ps1`: 8,416 tokens, no parser errors; no setup entrypoint executed. |
| Actual simulator skip/failure summaries | Both no-selector `--individual clink_cli_only` and `--tests clink_cli_only` exited 0 with zero passed/failed, one skipped, and “No tests executed.” An invalid selector exited 1 with one failure and no skip. None entered environment setup. |

## Installed CLI and broader suite results

The user authorized live checks for Claude, Codex, and Antigravity. CLI requests use temporary workspaces, existing authentication access, safe default permissions, and isolated provider-free PAL startup. Successful output does not identify billing/account class or establish a minimum supported CLI version.

| Check | Actual result |
| --- | --- |
| `PAL_CLINK_LIVE_TEST=claude python communication_simulator_test.py --individual clink_cli_only` | Passed: first request and dependent follow-up on Claude Code 2.1.296. |
| `PAL_CLINK_LIVE_TEST=codex python -m pytest tests/test_clink_integration.py -q` | Passed: one first/follow-up case on Codex 0.162.1. |
| Antigravity | Installed agy 1.3.3 has no runner/parser/manifest in this checkout; no clink conversation coverage. Implementation belongs to the separate Antigravity feature. |
| Gemini live | Not selected; no installed-vendor live coverage claimed. Its fake-CLI stdio and deterministic parser tests passed. |
| `./run_integration_tests.sh` without live selector | 2 failed, 2 passed, 11 skipped. The two OpenAI consensus cases still fail with connection errors after retries. The unselected clink case skips as designed. |
| `python communication_simulator_test.py --quick` | 4 passed, 2 failed, 0 skipped. Existing `basic_conversation` and `per_tool_deduplication` failures remain; logged invalid Gemini API credentials prevent successful API acceptance. |

The failing API checks are unmet environment-dependent checks. Follow-up: configure valid credentials/reachable endpoints outside this change, rerun those exact commands, and investigate any remaining failures using the returned payloads. Do not replace failures or unexecuted cases with passing coverage.

## Setup walkthrough

The isolated `/tmp/clink-task14-walkthrough.py` completed after the user approved reading the tracked environment example and creating temporary environment files. The initial security-hook rejection was resolved by that explicit approval. The developer's real environment file and personal login/MCP configuration were excluded.

The walkthrough extracted the current setup functions and executed Bash `main`, PowerShell `Invoke-PythonWorkflow`, and PowerShell `Invoke-DockerWorkflow` in temporary directories. Environment-file generation and import used the real functions. It substituted the existing Python environment, verified dependency imports, and intercepted installation, cache cleanup, Docker daemon, log creation, and personal client changes. Bash's client callback captured the Python/server arguments and checked the documented MCP entry shape; PowerShell used its real Python/Docker configuration builders.

The first Bash run exposed the shipped Azure endpoint being classified as incomplete. After the correction below, all three workflows produced absent provider diagnostics using the shared declarations. The walkthrough then removed those absent declaration names for Task 12's strict isolation guard and retained the harness's configuration/state overrides. This explicitly separates template classification from the credential-free MCP acceptance process.

For each setup workflow, the documented first/follow-up JSON requests were sent through the actual stdio server with fake Gemini, Claude, and Codex executables: **nine conversations passed**. Test markers were appended for the existing fake executable; the follow-up prompt contained the documented `ORCHID-42` input and prior user/reply markers once each, retained the continuation ID, and used safe argument buckets. Provider-free diagnostics and discovery were checked. The Docker-template cases used host stdio, not a running container.

This verifies setup configuration and requests against the completed harness. Fresh package installation, actual client configuration, native Windows PowerShell 5.1/macOS execution, Docker build/startup, and vendor installation/login remain unverified. Those environment-specific operations were not required or claimed as side effects of this local walkthrough.

## Review findings and corrections

Independent `kk:review-spec:isolated` reviewed completed Tasks 1–13 against all three feature documents and the production/test paths. It identified no P0/P1 findings and two corrections:

- **P2 SPEC_DEV — malformed nested vendor output lost diagnostics.** Codex now rejects non-object `item.completed.item` with `ParserError`, allowing the runner to preserve stdout/stderr in its structured error. Gemini ignores malformed optional model-statistics entries while retaining raw metadata and classifying its answer/error normally. The related nonzero-exit recovery path now serializes non-string error messages. New cases cover zero/nonzero exits and first/follow-up requests, checking unchanged stored bytes/TTL, no continuation, retained diagnostics, one launch, and no provider fallback.
- **P3 OUTDATED_DOC — missing review provenance.** The original design-review artifact is absent. The feature docs now say so and link to this final validation record rather than presenting that missing file as available evidence. This record does not reconstruct the original review.
- **P2 setup finding — shipped Azure endpoint was not treated as a placeholder.** Bash's tracked template supplies `https://your-resource.openai.azure.com/`. Azure now declares that exact required-setting placeholder, and its implicit constructor fallback uses the shared effective-value lookup so the placeholder cannot return through fallback. No key or a placeholder key produces absent diagnostics; a real key with the placeholder endpoint remains incomplete. Real endpoints retain their existing configuration behavior and explicit override precedence.

The independent spec rechecks found the original two findings and subsequent Azure correction resolved and reported **CONFORMANT**, with no remaining P0–P3, documentation, or ambiguity findings in the implemented contracts. No intentional specification deviation was introduced, so there are no deviations to index.

Independent `kk:review-code:isolated` approved the parser fixes and the additional Azure correction with no P0–P3 findings or removal candidates. No systemic findings required indexing. External PAL `gemini-3.8-flash` review was attempted and failed authentication, supplying no expert findings; the skill's independent-reviewer fallback completed. The code and spec reviewers inspected source and tests; the parent ran the checks reported above.

## Completion and follow-up

Task 14's implementation, walkthrough, review, and run-and-record work is complete. The feature is marked done with the external-service and platform limits above; this does not claim a successful API acceptance run. The setup walkthrough found a missing placeholder declaration that narrower template checks had missed, and the final spec review found malformed nested output that ordinary terminal-error cases had missed. Both were fixed within the approved contracts without dependencies or scope changes.

Remaining environment verification is explicit: rerun the two broader suites with valid credentials/reachable endpoints, opt into Gemini live coverage in an installed/authenticated environment, and test native Windows/macOS and Docker setup where those deployments are required. Antigravity remains a separate feature. No code or configuration was committed by this verification task. Optional editorial follow-up: `$kk:clarify-docs` on this validation record.

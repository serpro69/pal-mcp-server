# Task 6 verification: Antigravity clink

> Date: 2026-10-15
> Checkout: `bbaecf2` (`feat/agy_clink`), plus documentation changes only
> Status: in-progress; release acceptance incomplete
> Related: [tasks](./tasks.md), [design](./design.md), [capability history](./capability-evidence.md)

The implemented enterprise path passes all eight dedicated live cases. Final
acceptance remains incomplete: personal and missing/expired-session environments
are unavailable, the attempted ADC setup fails before inference, and required
repository-wide checks retain failures. None of these gaps is waived.

## Environment and execution

Verification used macOS arm64, Python 3.14.8 in
`/Users/sergio/.virtualenvs/pal`, and native
`/Users/sergio/.local/bin/agy` reporting 1.3.5. Commands ran from the repository
root with that environment activated. It contains MCP 1.27.0 and AnyIO 4.13.0;
the committed lockfiles require MCP 1.30.0 and AnyIO 4.14.2. No runtime source,
test assertions, dependencies, saved native settings, or credentials were changed.

The first quality run was sandboxed and stopped at a loopback bind denied by the
host sandbox. The authorized rerun and subsequent socket/network checks ran
outside that sandbox; host denials are not treated as agy permission evidence.
Native role cases start and stop their own fresh MCP servers. This proves they
loaded the checkout, not that the user's already-running MCP host was restarted.

Only the documented user settings file
`~/.gemini/antigravity-cli/settings.json` was hashed. It retained SHA-256
`86af8921ec53abfe4ca868b0f932ab991e3077351c669b1d3f56e062f3924a0d`
through all eight enterprise cases and the ADC attempt. This is not an audit of
credential files or every possible settings source. Operator-managed
Non-Workspace Access remained enabled as in the preceding evidence.

## Required commands and outcomes

Local logs listed below are temporary diagnostic artifacts, not committed
evidence dependencies. This report retains the commands, outcomes, skips, and
nonsecret observations needed after those logs expire.

| Command | Outcome | Local log |
| --- | --- | --- |
| `./code_quality_checks.sh` (initial sandbox) | Exit 1: lint/format passed; 251 tests passed before the denied loopback bind. 23 integration cases deselected. | `/tmp/agy-task6-quality-before.log` |
| `./code_quality_checks.sh` (authorized rerun) | Exit 1: Ruff/Black/isort passed; 1,302 passed before the existing TLS hostname test failed. 23 deselected. | `/tmp/agy-task6-quality-before-unrestricted.log` |
| `./code_quality_checks.sh` (after documentation updates) | Exit 1: Ruff/Black/isort passed; the same existing TLS hostname failure after 1,302 passes. 23 deselected. | `/tmp/agy-task6-quality-after.log` |
| `python -m pytest tests/ -q -m 'not integration' -ra` | Exit 1: 2,940 passed, 2 failed, 16 skipped, 23 deselected. All clink unit regressions passed. | `/tmp/agy-task6-unit.log` |
| `./run_integration_tests.sh` | Exit 1: 2 passed, 2 failed, 19 skipped, 2,958 deselected. | `/tmp/agy-task6-integration.log` |
| `python communication_simulator_test.py --quick` | Exit 1: 4 passed, 2 failed, zero skips. | `/tmp/agy-task6-quick.log` |
| `PAL_RUN_AGY_INTEGRATION=1 python -m pytest tests/test_clink_agy_safety.py tests/test_clink_agy_continuation.py -m integration -v -s` | Exit 0: all 8 enterprise cases passed, 69 unit cases deselected, 391.30 seconds. | `/tmp/agy-task6-enterprise-live.log` |
| `AGY_ADC_AUTH=1 PAL_RUN_AGY_INTEGRATION=1 python -m pytest tests/test_clink_agy_safety.py tests/test_clink_agy_continuation.py -m integration -v -s -x` | Exit 1: first scratch case failed; 69 unit cases deselected and the other seven live cases not reached. Native exit 3; missing plan-model configuration. | `/tmp/agy-task6-adc-live.log` |
| `PAL_RUN_AGY_INTEGRATION=1 python communication_simulator_test.py --individual clink_agy_validation` | Exit 0: 1 passed, zero failures/skips. | `/tmp/agy-task6-simulator.log` |

The two full-unit failures are
`test_tls_hostname_uses_idna2008_before_certificate_matching` and
`test_invalid_tls_hostname_fails_before_transport_io` in
`tests/test_dependency_security.py`. They reproduce the documented AnyIO drift:
the installed version uses the wrong IDNA behavior and reaches transport I/O for
an invalid hostname. Keep the assertions; rerun in the locked environment.
The 16 skips are four provider UTF-8 cases needing real Gemini/OpenAI access and
twelve state/log-directory cases needing native PowerShell. No new skips were added.

The integration failures are the existing consensus replay cases for `gpt-5`
and `gpt-5.2`. Skips are ten prompt integrations lacking `CUSTOM_API_URL`, one
existing clink test without its live selector, and eight agy cases without the
agy opt-in in this general run. The dedicated run executes all eight agy cases;
provider-key helper checks are not agy prerequisites. Quick mode passes
`cross_tool_continuation`, `content_validation`, `model_thinking_config`, and
`o3_model_selection`; `basic_conversation` and `per_tool_deduplication` fail with
the existing invalid Gemini API key. Quick mode does not cover agy.

### Locked environment limitation

`python -m pip install --dry-run -r requirements.txt -r requirements-dev.txt`
succeeded and found the locked packages. Creation/population of the approved
disposable `/tmp/agy-task6-venv` was blocked twice by the local PreToolUse security
hook matching `venv/`, including after explicit user approval. No installation
occurred. This is a tool-policy restriction, not a package-resolution failure or
an approval rejection by the user. Next: enable that authorized operation in the
local policy or prepare the locked environment outside the agent, then rerun the
required checks. Do not weaken the security regressions or alter the lockfiles.

## Enterprise live evidence

All eight cases use the production filtered child environment and no forwarded
provider API key or ADC selector. The account class is the user's identification;
no tokens were inspected. False adds no mode flag; true adds exactly
`--mode accept-edits`. Each required read returns a random marker present only in
the referenced file. Role follow-ups recall the original marker after the harness
has replaced it in the file, proving retained PAL history.

| Case | Project / cwd | Observation |
| --- | --- | --- |
| Native reads/edits, scratch | Project `/private/tmp/pytest-of-sergio/pytest-4/test_live_agy_reads_and_native0/project`; read/default/edit cwd `/tmp/pal-clink-ep7961uf`, `/tmp/pal-clink-om8fq4ts`, `/tmp/pal-clink-lz184rdp` | Required read and native edit passed. Default write was denied, sentinel unchanged, and the empty successful native result remained a PAL error. |
| Native reads/edits, configured | Project and all cwd values `/private/tmp/pytest-of-sergio/pytest-4/test_live_agy_reads_and_native1/project` | Same read/edit success and policy-specific default denial. |
| default, scratch | Root `/private/tmp/pal_agy_validation_o0ruk_db`; cwd `pal-clink-eady9hrs` / `pal-clink-_haahowu` | File read, history, role inheritance, and settings passed. |
| planner, scratch | Root `/private/tmp/pal_agy_validation_hdf881ah`; cwd `pal-clink-p7hra2ui` / `pal-clink-lj7xlnx0` | Same. |
| codereviewer, scratch | Root `/private/tmp/pal_agy_validation_plaw1xff`; cwd `pal-clink-ip9gv7he` / `pal-clink-mhu2zte6` | Same. |
| default, configured | Root `/private/tmp/pal_agy_validation_n6a1jz58`; both cwd `project` | Same. |
| planner, configured | Root `/private/tmp/pal_agy_validation_fc03fvh8`; both cwd `project` | Same. |
| codereviewer, configured | Root `/private/tmp/pal_agy_validation_q_1byc_r`; both cwd `project` | Same. |

For each role row the referenced file is `<root>/project/fixture.py`; cwd entries
are relative to that root. Role roots are removed after server shutdown. Native
sentinels stay outside scratch so cleanup cannot conceal writes. Observed default
denial is not a universal no-write guarantee.

The individual simulator also passed as codereviewer in scratch mode. Its root
was `/private/var/folders/vv/lnz9c5h95pnbjzsz93qmr0n80000gn/T/pal_agy_validation_caa64pnd`,
with reference `project/fixture.py` and cwd values `pal-clink-0yofs9p5` and
`pal-clink-i7hyjy0m`. It returned the file-only marker and retained history with
unchanged settings. The PAL file-inspection warning about a temporary `/var`
path did not prevent native agy from reading the absolute reference; the marker
assertion, rather than a successful answer alone, establishes that read.

## Authentication coverage

The user clarified that existing enterprise agy login and gcloud ADC credentials
are available, but agy has not been configured or used with ADC. They authorized
default discovery with test-process-only `AGY_ADC_AUTH=1`. The production runner
forwarded that selector and excluded API keys. Its first scratch read exited 3
with a structured `ERROR`: the executor lacked a valid plan model. PAL reported
the native failure without replay. No file read or ADC authentication success was
established, and this does not prove the stored credentials are invalid.

ADC attempt reference project:
`/private/tmp/pytest-of-sergio/pytest-2/test_live_agy_reads_and_native0/project`;
child cwd: `/tmp/pal-clink-en828j72`. No explicit credential-file, quota-project,
or location override was supplied. No login/logout, account switching, credential
inspection, or saved-settings mutation was attempted to manufacture evidence.

The current [enterprise instructions](https://antigravity.google/docs/enterprise)
describe ADC setup separately from cached sign-in. They were consulted on
2026-10-15; documentation is not successful 1.3.5 runtime evidence.

| Required environment | State | Concrete next step |
| --- | --- | --- |
| Cached enterprise login, both cwd modes | Verified by eight cases above | Retain evidence and repeat on the locked Python environment. |
| Personal login without API keys | Unavailable; not waived | Provide an isolated pre-authenticated personal environment, then run both live test files and the simulator. |
| Successful ADC selector/default discovery | Attempt failed before inference | Prepare a working native agy ADC model/project/location setup without changing the active enterprise login; then rerun the matrix. |
| Explicit credential-file and project/location routing | Not exercised | Provide authorized routing values and credential-file location; verify their actual native routing, not merely variable forwarding. |
| Missing and expired cached sessions | No prepared isolated environments | Supply isolated missing/expired-session fixtures; verify actionable failures through PAL without invalidating active credentials. |

## Packaging and discovery

`python -m pip wheel . --no-deps --wheel-dir /tmp/agy-task6-dist` succeeded with
standard isolated build dependencies. The initial `--no-build-isolation` attempt
failed because the active environment lacks setuptools. Wheel
`pal_mcp_server-1.3.0-py3-none-any.whl` has SHA-256
`3ce4ae2cdaf1564eae9451346429c15619e14bfbe9404813193a9169f1620f2b`.
Archive inspection confirmed the agy agent/parser, all four client manifests,
and the default/planner/codereviewer shared prompt files are included.

Fresh source-checkout `ClinkRegistry` and `CLinkTool` instances list `agy`,
`claude`, `codex`, and `gemini`, and all three agy roles resolve existing prompt
files. Installing this wheel into a disposable target was blocked by the local
PreToolUse hook matching `dist/`; installed-wheel discovery remains unverified.
Next: allow that local installation or run it outside the agent and repeat
discovery from outside the checkout. No commit was made during verification; `.capy/knowledge.db`
must remain excluded from any later commit.

## Independent reviews and documentation

`kk:review-code:isolated` reviewed `c1c2521..bbaecf2`: 25 files, 2,314 changed
lines, Python profile with all four required checklists. The independent
code-reviewer approved with no P0–P3 findings. It inspected source/tests without
independently running them. External PAL review using `gemini-3.8-flash` failed
at expert step 2 with `API_KEY_INVALID`; it supplies no corroboration. The skill's
single-reviewer fallback applies. No systemic findings required indexing.

`kk:document` reconciles current task states and the historical research banner,
retains the inherited-permissions contract, and adds the failed ADC setup to the
guide. The README's pending-release qualification remains accurate. Historical
dated test runs remain intact. Independent `kk:review-spec:isolated` reviewed all
eight feature/user documents, the completed Tasks 1–5, and Task 6's partial
completion claims. It found the implementation conformant, with no P0–P3
findings, documentation issues, or ambiguities. It inspected source and recorded
evidence without independently executing tests. No deviations required indexing;
conformance does not establish release acceptance. No new project conventions
needed indexing because the verification limitations are recorded here.

The temporary review diff `/tmp/agy-task6-review.patch` remains outside the
repository because local policy rejected its removal. It can be deleted by the
operator after review; it is not a release gate or a committed artifact.

Final local checks verified 154 relative file-link targets across nine Markdown
files and a clean `git diff --check`. Only documentation was modified; at the
verification handoff, no staged files or commit had been created.

## Remaining gates

- Task 6.1: rerun required suites on the locked dependencies; current clink unit
  behavior passes, but the full unit suite is not green.
- Task 6.3: close every authentication row above; unavailable coverage is neither
  a passing result nor a waiver.
- Repository acceptance: resolve the provider/replay failures in their own scope,
  then rerun the integration helper and quick simulator. Do not silently modify
  unrelated code or credentials within this feature's verification task.
- Task 6.7: validate installed-wheel discovery and restart the actual consuming
  MCP session before using the updated checkout there. Fresh test-owned servers
  already cover source-checkout live execution.

The approved implementation contract did not change. Verification exposed setup
and dependency limitations rather than requiring adapter edits. Default ADC
credentials alone were insufficient for this agy configuration. These findings
stay in the feature documents so a later contributor can finish acceptance.

## Pre-commit recheck, 2026-10-15

After the user requested a documentation commit, the required checks were rerun
in the same existing environment. All three exited 1 with the same recorded
failures; no acceptance gate was closed by the commit request.

| Command | Result | Local log |
| --- | --- | --- |
| `./code_quality_checks.sh` | Ruff/Black/isort passed; 1,302 tests passed before the known TLS hostname failure; 23 deselected. | `/tmp/agy-task6-precommit-quality.log` |
| `./run_integration_tests.sh` | 2 passed, the same 2 consensus replay failures, 19 skipped, 2,958 deselected. Skip reasons remain those recorded above. | `/tmp/agy-task6-precommit-integration.log` |
| `python communication_simulator_test.py --quick` | 4 passed, the same 2 invalid-Gemini-key failures, zero skips. | `/tmp/agy-task6-precommit-quick.log` |

# Antigravity clink capability evidence

> Date: 2026-10-14
> Outcome: Tasks 1–2 done; default-role agy adapter implemented; Tasks 3–6 and release acceptance pending
> Checkout tested: `3286d5f160014e162b183ab38ea826fd9a6ceab3`
> Authentication follow-up checkout: `d7e52b568d36c8d3806b0a8cd0eada39f94b0406`
> Reference: native agy 1.3.5, approved by the user on this date
> Account: enterprise, identified by the user; credentials were not inspected
> Platform: macOS 26.6.2 (25G83), arm64; Python 3.14.8
> Related: [design](./design.md), [implementation](./implementation.md), [tasks](./tasks.md)

## Contract revision and remaining work

Existing enterprise authentication and one-message/EOF transport work through the
shared runner. A configured project supports a content-dependent file read. After
the user enabled `allowNonWorkspaceAccess`, the scratch-mode external-file read also
passed. The initial denial was a configuration limitation, not an established
architectural incompatibility. See the [rerun evidence](#rerun-after-user-enabled-external-access).

Default direct writes remained denied with external access enabled. Native
`--mode accept-edits` changed the intended sentinel in both cwd modes; repeat runs
with settings held stable left the settings file byte-for-byte unchanged.
These observations establish useful behavior under the current configuration.
They do not establish read-only enforcement independently of saved write/command
permissions. The user subsequently approved dropping that guarantee for this
integration and tracking an improvement upstream.

**Accepted semantics:** false adds no PAL edit-mode flag and inherits agy's saved
permissions; it is not a read-only guarantee. True requests `--mode accept-edits`
and leaves other native tool rules in effect. PAL must not rewrite saved settings,
change authentication, or introduce a new sandbox. The existing advisory no-edit
prompt is not permission enforcement. Unsupported `editable_paths` remains rejected.

The user reported that `agy --mode default` is rejected on 1.3.5, with valid values
listed as `accept-edits`, `auto`, and `plan`, and filed
[agy #1233](https://github.com/google-antigravity/antigravity-cli/issues/1233).
The issue was open with no comments when read on 2026-10-14. This is a user-reported
flag limitation, not a live test in this investigation or proof that all possible
downgrades are unavailable. No `auto`/`plan` behavior is assumed to enforce read-only.

The former permission blocker is removed by an explicit requirement change, not
by newly passing enforcement tests. Personal login, successful ADC routing,
and missing/expired cached-login coverage remain unverified; a synthetic missing-ADC
case now verifies an explicit failure, as recorded below. The user approved moving
those unavailable authentication cases to Task 6 as release requirements and
finishing Task 1 on verified enterprise evidence. Tasks 1–2 are done;
Tasks 3–6 remain pending. Task 2 implements argument validation and default-role
transport; native edit-mode selection and request-local isolation remain Task 4.
Enforced native downgrades are a deferred enhancement under upstream #1233, not an
acceptance prerequisite. Revisit only after a documented release and tests of
saved-policy precedence, direct and command/MCP writes, both cwd modes, and
concurrency; update the adapter, schema, guide, tests, and A4 together. The earlier
blocked conclusions in the investigation/review history reflect the superseded A4.

## Environment and method

- `command -v agy` and `whence -pa agy` resolved only
  `/Users/sergio/.local/bin/agy`; its `--version` reported `1.3.5`.
  `--help` exposed the native headless protocol, mode, and sandbox flags.
  The user approved replacing the earlier 1.3.3 acceptance target; historical
  research in [verification.md](./verification.md) retains its original date/version.
- A local investigation harness at `/tmp/pal_agy_capability_probe.py` constructed
  `ResolvedCLIClient` directly, without registry or manifest changes. It reused
  `BaseCLIAgent.run()` and `_run_in_directory()`; a subclass only recorded actual cwd.
  A temporary parser required valid JSON event objects, exactly one result,
  `SUCCESS`, and a nonblank string response.
- The harness supplied one pre-encoded newline-terminated user event to the existing
  raw UTF-8 `communicate()` site. EOF, process capture, timeout, cleanup, and cwd were
  owned by the unchanged shared runner. This does not implement or test Task 2's
  proposed `_encode_stdin` hook.
- The initial capability probes and external-access reruns used `_build_environment()` with the proposed passthrough names
  `AGY_ADC_AUTH`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_QUOTA_PROJECT`, and
  `GOOGLE_CLOUD_LOCATION`. None of those four names was present in those runs.
  The actual inherited names were `COLORTERM`, `HOME`, `LANG`, `LANGUAGE`, `LC_ALL`,
  `LC_CTYPE`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `TMPDIR`, and `USER`.
  No API-key variable was forwarded. No selector, provider, or model was forced in
  those runs. The later A1a authentication probe deliberately supplied synthetic
  ADC values only to its test process, as recorded below.
- Account class is the user's identification. Successful calls demonstrate existing
  enterprise authentication in this environment, not ADC selection, personal-login
  compatibility, entitlement inspection, or coverage of other enterprise routes.
- Live calls ran outside the Codex sandbox so host restrictions did not manufacture
  agy permission denials. Input was synthetic and fixture-only. The harness did not
  initiate login/logout, copy authentication files, or edit persistent settings.
  The user's later setting changes are recorded separately below.
- During the initial probes, only the documented CLI settings file was observed for configuration preservation:
  `~/.gemini/antigravity-cli/settings.json` had SHA-256
  `4f0f66c6c0b92141dba899320d86a146df6eff3cf9e55a064b9c01c378e717f4`
  before and after each initial probe. The top-level keys `toolPermission`,
  `artifactReviewPolicy`, `allowNonWorkspaceAccess`, and `enableTerminalSandbox`
  were absent. Shared/project settings and permission-rule contents were not audited;
  their effective precedence and whether any are permissive remain unverified.

## Invocation and case matrix

The initial five model probes used exactly these argv tokens:

`/Users/sergio/.local/bin/agy --input-format stream-json --output-format stream-json`

No prompt, print flag, native continuation flag, model pin, permission override, or
output-file flag appeared in argv. The PAL timeout was 90 seconds. Local help lists
`--print-timeout` default `0s`; no CLI timeout was supplied. Successful durations
below are observations, not a guarantee about upstream deadlines. Failure duration
and exact terminal status/response were not retained by the probe's error summary.

The disposable reference project was
`/private/tmp/pal-agy-capability-20261014/project` (the canonical form of its `/tmp`
path). It contained `marker.txt`, `direct-sentinel.txt`, and `command-sentinel.txt`.
The marker value occurred only in the file, not in the read request.

| Case | Actual cwd | Expected observation | Actual observation | Result |
| --- | --- | --- | --- | --- |
| T1 smoke, scratch | `$TMPDIR/pal-clink-ba6nti94` | One complete result after stdin EOF and exit | Exit 0; one `SUCCESS` result, one turn, requested synthetic answer; runner 6.329s, CLI 3.062080s | Pass for transport and this enterprise login |
| T2 smoke, configured | Reference project | Same as T1 | Exit 0; one `SUCCESS` result, one turn; runner 5.107s, CLI 2.489452s | Pass for transport and this enterprise login |
| R1 read, configured | Reference project | Answer reports marker read only from `marker.txt` | `view_file` ACTIVE/DONE events; exact Unicode marker returned; runner 7.769s, CLI 5.607770s | Pass for this file read |
| R2 read, scratch | `$TMPDIR/pal-clink-eezrtwvh` | Same marker from the project outside cwd | `read_file` auto-denial notice; process exit 0; probe rejected unsuccessful/empty result | Fail for required file access |
| W1 direct write, configured | Reference project | Observe whether existing policy denies a targeted sentinel replacement | `write_file` auto-denial notice; process exit 0; probe rejected unsuccessful/empty result; sentinel unchanged | Denial observed; A4 unproven |

For these rows `$TMPDIR` resolved to
`/private/var/folders/vv/lnz9c5h95pnbjzsz93qmr0n80000gn/T`.
Scratch directories were managed by the runner and removed after completion. The
reference project was outside both scratch directories, so cleanup could not hide
fixture writes. T2, R1, and R2 overlapped; this is **not** a safe/edit policy-isolation
test because none selected a verified edit policy.

The original sentinel bytes remained `PAL_AGY_DIRECT_ORIGINAL` plus newline and
`PAL_AGY_COMMAND_ORIGINAL` plus newline. Their post-probe SHA-256 values were
`2fcec22c117e5373f9a9ea06be94e9d43099b75521503f91a2d8d903cf41f8ac` and
`9670b9c7c5d13aef86f1510b626613e448c9fcb6d9c785cda28db922e18a52f5`, respectively.
No command-write request was executed.

The retained [T1 event fixture](./fixtures/enterprise-1.3.5-smoke-scratch.jsonl)
is a sanitized projection in observed event order. Conversation identifiers,
tool inventories, text deltas, and optional usage fields are omitted; cwd is replaced
with a placeholder. It preserves the observed result envelope, response, and timing.
It is evidence from one live call, not a complete parser regression corpus. Raw
prompts, credentials, and complete raw streams are not committed.

## Reproduction procedure

The `/tmp` harness is an investigation artifact, not an installed client or durable
test suite. To recreate its relevant behavior after that file is removed:

1. Use a pre-authenticated native 1.3.5 executable, the existing Python dependencies,
   and disposable fixture files. Record platform/account class without reading tokens.
2. Construct a `ResolvedCLIClient` with the exact executable and protocol arguments
   above, the four proposed passthrough names, empty explicit `env`, 90-second timeout,
   and no output capture. Supply a `ResolvedCLIRole` with no role arguments. Instantiate
   `BaseCLIAgent` with an existing parser identifier, then replace only its parser with
   the temporary validator described above; do not register an agy client.
3. For T1/R2 use `working_dir=None`; for T2/R1/W1 use the fixture project's absolute
   directory. Record `cwd` at entry to `_run_in_directory()` and delegate unchanged.
4. Pass `json.dumps({"event": "user", "message": {"content": request}}) + "\n"`
   as `run(prompt=...)`, with empty file/image lists. For smoke, request a fixed
   synthetic answer without tools. For read, request only the contents of the absolute
   marker path, without revealing the marker value. For W1, request a direct-tool
   replacement of only the disposable sentinel with a distinct synthetic value.
5. Require a zero exit, valid event envelopes, one `SUCCESS` result, and nonblank text;
   separately compare file-read answers and sentinel contents. A CLI zero exit alone
   is insufficient. Compare settings hashes without publishing settings contents.

The existing local commands are `python /tmp/pal_agy_capability_probe.py smoke`,
`read`, or `write`, optionally followed by
`--cwd /tmp/pal-agy-capability-20261014/project`. Repeating W1 remains a deliberate
write attempt against a disposable fixture, not a certified read-only invocation.

## Rerun after user enabled external access

The user enabled Non-Workspace Access and requested retries. The settings file then
contained `allowNonWorkspaceAccess: true`. The unchanged harness, enterprise login,
filtered environment, reference project, and 90-second outer timeout were retained.

| Case | Actual cwd | Additional arguments | Observed outcome |
| --- | --- | --- | --- |
| R2b external read retry | `$TMPDIR/pal-clink-07ewdae_` | None | Exit 0, one `SUCCESS` result, exact file-only Unicode marker; runner 8.565s, CLI 6.182414s; no stderr. External-read requirement passes under this configuration. |
| W1b default direct-write retry | Reference project | None | `write_file` denied, exit 0 with unusable result rejected by probe; original sentinel/hash unchanged. Desired default denial under current policy. |
| E1 native opt-in | `$TMPDIR/pal-clink-txd46qky` | `--mode accept-edits` | `replace_file_content` completed; target became `PAL_AGY_DIRECT_CHANGED` plus newline; runner 21.975s, CLI 18.437446s. Settings-preservation observation confounded by concurrent user changes. |
| E2 native opt-in, stable repeat | Reference project | `--mode accept-edits` | Reset sentinel changed as intended; one `SUCCESS` result; runner 19.945s, CLI 17.401042s; all settings bytes and field hashes unchanged. |
| E3 native opt-in, stable repeat | `$TMPDIR/pal-clink-l6joyklg` | `--mode accept-edits` | Reset sentinel changed as intended; one `SUCCESS` result; runner 22.845s, CLI 20.922281s; all settings bytes and field hashes unchanged. |

R2b/W1b retained settings SHA-256
`6ba3182307daa6b69b3d4aee6a986bdda7a6d2a7a61afa4ee61f3595edf33cfb`.
During E1 that hash changed to
`542fbb1a94d8a87023d362c63139fd3fc63929e06fc26bb0236979eff1a16ce1`.
The user confirmed they were adding trusted repositories concurrently. The hash
change therefore cannot be attributed to agy or treated as evidence of a CLI
settings-mutation bug. A subsequent inspection found neither the disposable project
nor E1's scratch directory in `trustedWorkspaces`.

An automatic approval review initially rejected E2 because it suspected a
persistent permission-setting mutation. After the user's clarification and the
read-only inspection, the same probe was approved and executed. No rejected
command was run through a workaround.

The user then held settings unchanged. E2 and E3 each retained SHA-256
`86af8921ec53abfe4ca868b0f932ab991e3077351c669b1d3f56e062f3924a0d`,
including unchanged hashes for `allowNonWorkspaceAccess`, `editorMode`, and
`trustedWorkspaces`. The final edited sentinel hash was
`4a733433da13e18f6bb5aaae263d48f3a4b47f758b4f88a29930b409c8d00e21`.
The harness's settings observation was extended to hash individual fields, without
printing trusted repository paths. Only the disposable sentinel was reset between
edit probes; PAL did not change user settings.

These are native flag probes through the shared runner, not a production
`allow_edits` implementation. E2/E3 still reported `permission_mode: request-review`
despite successful edits: the init label alone cannot establish no-edit behavior.
Concurrent native restrictions and permissive saved write/command policies were
not tested; the later contract revision defers that stronger enforcement work
upstream. PAL's own argument-state isolation still needs implementation tests.
The successful external-read configuration is operator setup; these results do
not establish a need to change the cwd architecture.

## Documentary evidence and remaining uncertainties

The current [headless documentation](https://antigravity.google/docs/cli/headless/)
describes the user-event/EOF protocol, saved-policy inheritance, and permission
denials despite zero exit. These are documentary claims; only the cases above were
tested. Its workspace-write description does not establish what W1's existing
settings will permit.

[Mode documentation](https://antigravity.google/docs/cli/modes) describes plan mode
as a planning instruction and separates shell permissions from execution mode.
[Sandbox documentation](https://antigravity.google/docs/sandbox?tab=cli) retains
writable workspaces. [Permission documentation](https://antigravity.google/docs/permissions?tab=cli)
places allow/ask/deny rules in saved settings. Neither those pages nor local help
established the required per-invocation deny policy. No plan/sandbox/bypass flag was
used as a substitute for evidence, and no claim is made that all possible mechanisms
were exhaustively ruled out.

The four proposed authentication names remain supported by the current
[enterprise documentation](https://antigravity.google/docs/enterprise), but this
non-ADC enterprise session cannot verify successful ADC routing. The follow-up below
checks synthetic forwarding and a missing-ADC failure on 1.3.5; it does not establish
project/location consumption or routing precedence. Task 2 still owns durable
forwarding/override tests; successful routing requires a prepared ADC environment.

### Authentication follow-up, 2026-10-14

The user confirmed that only the existing enterprise login is available. Personal
and authenticated ADC acceptance cannot be completed in this environment. Current
[installation documentation](https://antigravity.google/docs/cli/install) describes
native OS keyring authentication; local 1.3.5 help exposes no credential-profile or
authentication-directory isolation flag. No logout, credential copying, keyring
changes, or saved-settings edits were used to manufacture missing/expired sessions.

The disposable `/tmp/pal_agy_auth_probe.py` used the unchanged shared runner and
the earlier temporary result validator. It checked version 1.3.5, then supplied
test-only parent environment values through the four proposed passthrough names:
`AGY_ADC_AUTH=true`, `GOOGLE_APPLICATION_CREDENTIALS` pointing at an intentionally
nonexistent file inside a temporary fixture directory, a synthetic quota project,
and location `global`. These values existed only during that probe and are not
production defaults. An unrelated synthetic secret was excluded and no API-key
name was forwarded. The credential-location variable follows the documented
[ADC search order](https://docs.cloud.google.com/docs/authentication/application-default-credentials).

| Case | Actual cwd | Observation | Evidence limit |
| --- | --- | --- | --- |
| A1a synthetic missing ADC | `$TMPDIR/pal-clink-hokx264l` | All four synthetic values survived `_build_environment()` unchanged; unrelated secret excluded. One invocation exited 1 after 0.450s with `authentication required`, instructions to run `agy` to log in, and `authentication failed or timed out`. Shared runner raised `CLIAgentError`; stdout was present but not retained. | Verifies an explicit failure for this missing-ADC setup. Does not prove successful ADC routing, exact credential lookup, missing cached login, or expiry handling. |
| A1b cached enterprise recheck | `$TMPDIR/pal-clink-fsdhp20r` | After removing the synthetic variables, one EOF-terminated request exited 0 with one `SUCCESS` result and `PAL_AGY_TRANSPORT_OK`; runner 6.241s, CLI 3.016700s; stderr empty. No ADC selector or API-key name was forwarded. | Existing enterprise login still works. Does not cover another account class or expired credentials. |

Both runs used `agy --input-format stream-json --output-format stream-json` with
the prompt only on stdin, outside the Codex sandbox. The failure probe had a
30-second outer deadline; the enterprise recheck retained 90 seconds. Neither
deadline was reached. The settings SHA-256 remained
`86af8921ec53abfe4ca868b0f932ab991e3077351c669b1d3f56e062f3924a0d`
before and after each run. Scratch cwd and the empty credential-path fixture were
removed by their owners. The enterprise login was not changed.

To reproduce A1a, construct the same unregistered client and validator described
above, set its timeout to 30 seconds, and temporarily populate the four passthrough
names with those synthetic values in the probe process. Confirm the credential
path does not exist, inspect only filtered names/value equality, then send one
synthetic no-tool request plus EOF through `BaseCLIAgent.run()`. Record the exit,
bounded diagnostic, actual cwd, and settings hash. Restore the probe environment
and repeat T1 for A1b. Do not count a timeout, unexpected success, or another
failure class as a passing missing-authentication case.

| Acceptance | Evidence / gap |
| --- | --- |
| A1 | T1/T2 and A1b: existing enterprise login without a forwarded API key. A1a: synthetic missing-ADC error. Personal, successful ADC routing, and missing/expired cached-login cases unverified. |
| A2 | T1/T2 transport, R1 configured read, R2b external read, and Task 2's production default-role file read below. Additional roles remain Task 5. |
| A3 | Pending Task 5; no PAL continuation tested. |
| A4 (revised) | W1/W1b denial under current settings; E2/E3 native edit opt-in in both cwd modes with unchanged settings. Task 2 verifies false has no mode flag and qualifies the schema; true is explicitly rejected until Task 4. Native edit arguments/isolation remain Task 4; stronger enforcement is deferred to #1233. |
| A5 | Task 2 adds the production agent with path restrictions unsupported; public preflight-ordering acceptance remains Task 4. |
| A6 | Task 3's controlled subprocess tests verify prompt absence from argv, command metadata, and PAL command logs on success, failure, and timeout. This does not cover native child logs or echoed model text. |
| A7 | Task 3 verifies production tool errors for nonzero exit, unsuccessful status, invalid/incomplete output, missing executables, and timeout; failure/timeout launches once per invocation. A1a supplies a live synthetic missing-ADC failure. Missing/expired cached-login acceptance remains Task 6.3. |
| A8 | Task 2 verifies exact legacy UTF-8 stdin and existing client/discovery behavior. Task 3 checks that other clients retain unbounded stderr and raw error stdout. Full unit validation retains dependency-security failures. |

No personal or authenticated ADC environment was made available. Missing/expired
cached authentication was not manufactured by logging out or changing the user's credentials. Linux, Windows,
and other enterprise authentication routes are unverified. The user approved
assigning the unavailable authentication cases to final Task 6 acceptance, without
waiving their release requirements. Stronger enforcement under permissive saved
settings is separately deferred upstream. None of these unrun cases is presented
as a passing or skipped live test.

## Repository validation

The active environment was `/Users/sergio/.virtualenvs/pal`.

Authentication follow-up validation used that same environment. The required
pre-change quality check again stopped at the sandbox's loopback-bind restriction
after 251 passes. Outside the sandbox it reached 985 passes, then failed the same
TLS hostname dependency regression below (15 deselected). A disposable environment
installation from the existing lockfiles was attempted, but the PreToolUse security
hook blocked the command with `Access to 'venv\/' is blocked by security policy`.
No replacement environment or dependency repair is claimed. The focused clink
environment/safe-default result and post-change check are recorded with the review
below.

- Before edits, `./code_quality_checks.sh` passed Ruff, Black (332 files unchanged),
  and isort. Sandboxed pytest stopped at a loopback socket bind restriction after
  251 passes. Retrying outside the sandbox reached 985 passes, then failed
  `tests/test_dependency_security.py::test_tls_hostname_uses_idna2008_before_certificate_matching`:
  actual `fass.de`, expected `xn--fa-hia.de` (15 integration cases deselected).
- Installed AnyIO was 4.13.0 versus locked 4.14.2; other observed drift included
  idna 3.12 versus 3.18 and urllib3 2.6.3 versus 2.8.0. The quality baseline is failed,
  not green. Environment repair is deferred because this task concerns capability
  evidence; next recreate/synchronize a test environment from `requirements.txt`
  and `requirements-dev.txt`, then rerun the unchanged quality command. No test or
  lockfile was weakened to hide this failure.
- `python -m pytest tests/test_clink_environment.py tests/test_clink_safe_default.py -q -m 'not integration'`:
  52 passed, one pre-existing Google SDK deprecation warning.
- Post-edit `./code_quality_checks.sh` outside the sandbox repeated the same failure:
  985 passed, one failed, 15 deselected, one warning; lint/format/import checks passed.
- Before recording the reruns, the quality command again reached the same 985 passes
  and TLS hostname failure. The agy setting does not affect that Python dependency test.
- After recording the reruns, the quality command repeated that same failure
  (985 passed, one failed, 15 deselected). Rerun evidence rows, their anchor, pending
  task states, and `git diff --check` passed validation.
- Initial fixture and task-state checks passed: five JSONL events, one blocked task,
  five pending tasks, only subtask 1.2 checked. That task-state snapshot predates the
  approved contract revision. `git diff --check` passed.
- Local-link validation found 41 valid links/anchors and five failures in the four
  active Markdown files. All failures point to pre-existing `review-design.md`,
  absent from this checkout and its inspected git history. Historical review claims
  are inherited from the existing documents, not independently revalidated here.
  Restoring that original report is deferred: recover it from the original review
  session/artifacts or run a new design review with its own date; do not fabricate
  a replacement that claims to be the missing historical report.
- The external PAL code-review attempt (`gemini-3.8-flash`) failed before expert
  analysis because its Gemini API key was rejected. This is separate from the
  successfully exercised agy enterprise login. The isolated workflow falls back
  to its independent code-reviewer agent.

## Independent review

Pre-commit checks on 2026-10-14 retained the same unit baseline failure (985 passed,
one failed). `./run_integration_tests.sh` finished with two passed, two failed,
and eleven skipped: both consensus model cases (`gpt-5`, `gpt-5.2`) returned errors
after connection failures. The skipped cases lacked their live-test opt-ins or
local-provider setup. Quick simulator mode finished with four passed and two failed
(`basic_conversation`, `per_tool_deduplication`); its log includes rejected Gemini
API-key calls and missing continuation results. These checks are not agy acceptance
evidence. Repairs are deferred from this documentation commit: synchronize the
locked test dependencies, investigate the consensus replay/transport connection
failures, configure valid simulator provider access, and rerun the failing scenarios
individually before claiming a green repository baseline.

Contract-revision validation on 2026-10-14 checked five documents, 59 local
links/anchors, upstream issue links, the retained JSONL fixture, and the revised
task states: Task 1 in progress, Tasks 2–6 pending, no permission-blocked task.
Four references to the absent historical review remain pre-existing; no new
missing targets were introduced. `git diff --check` passed. Quality checks before
and after this documentation revision each stopped at the same TLS hostname
dependency failure after 985 passes (15 deselected); lint/format checks passed.
No production implementation or additional live CLI test was performed for this
contract revision.

The initial `kk:review-code:isolated` checkpoint, before the external-access reruns,
completed with one available reviewer on 2026-10-14.
The independent code-reviewer approved the five changed documentation/fixture files
with no P0–P3 findings. It checked the temporary probe, shared runner, environment
filter, client defaults, and dependency pins. Approval concerned those documentary
changes; at that checkpoint Task 1 remained blocked under the original A4, and no
runtime acceptance was granted.

The reviewer did not rerun live probes or tests, independently reproduce timings,
marker comparisons or settings hashes, or independently retrieve upstream docs.
The unavailable external PAL reviewer supplies no corroboration. The missing
historical review, baseline test failure, and all unverified acceptance rows remain
explicit follow-ups above. No systemic findings to index and no new project
conventions were established.

The same independent reviewer checked the four-document rerun update. It found
one P3 wording issue: A6 still described all argv as protocol-only after the edit
probes added `--mode accept-edits`. That summary is corrected above. No P0–P2
findings were reported; live observations were not independently reproduced.

Those reviews predated the user's inherited-permissions contract revision. At that
checkpoint, the revision had user approval but had not received a new independent
design review; the later Task 2 review is recorded below.

Optional documentation follow-up: run `kk:clarify-docs` on this file and the updated
`design.md`, `implementation.md`, and `tasks.md` if a shorter presentation is wanted;
retain the evidence limits, accepted contract, and remaining verification gaps.

### Task 1 completion checkpoint, 2026-10-14

Task 1 is complete under the user's approved enterprise-evidence boundary. The
personal-login, successful ADC routing, and missing/expired cached-session cases
remain unchecked Task 6.3 release requirements. No runtime adapter was added.

- A1a/A1b supplied the additional live authentication observations above; all six
  revised Task 1 subtasks are complete. Tasks 2–6 remain pending.
- `python -m pytest tests/test_clink_environment.py tests/test_clink_safe_default.py -q -m 'not integration'`:
  52 passed, one existing SDK deprecation warning.
- `./code_quality_checks.sh` before and after the documentation changes passed
  Ruff, Black, and isort, then failed the existing TLS hostname regression after
  985 passes (15 deselected). Installed AnyIO/idna/urllib3 still differ from the
  lockfiles; the security-hook rejection above prevented disposable environment
  setup. The repository baseline remains failed.
- `./run_integration_tests.sh`: two passed, two failed, eleven skipped. The two
  existing consensus failures report unmatched replay requests for OpenAI chat
  completions and resulting connection errors. Ten prompt-regression cases lack
  `CUSTOM_API_URL`; the existing live clink case lacks its opt-in.
- `python communication_simulator_test.py --quick`: four passed, two failed,
  zero skipped. `basic_conversation` and `per_tool_deduplication` failed; the log
  again contains rejected Gemini API-key calls. These results do not validate agy.
- Document validation checked five files and 65 local links/anchors: no new broken
  links; four pre-existing references to the missing historical design review
  remain. The retained fixture has five JSON events and one successful result.
  `git diff --check` passed.
- `kk:review-code:isolated` completed with the independent code-reviewer as the
  available reviewer. It found no P0–P2 issue and one P3: the earlier environment
  description needed to be limited to the initial probes/reruns because A1a uses
  synthetic ADC variables. That wording is corrected; the reviewer's narrow
  follow-up approved the final documents with no remaining findings. External PAL expert analysis
  (`gemini-3.8-flash`) failed because its API key was invalid; it supplies no review
  corroboration. Live results and upstream docs were not independently reproduced.

No systemic findings or new project conventions required indexing. At the Task 1
checkpoint, the broader dependency/replay/provider failures and missing fresh
design review remained explicit follow-ups. Task completion did not assert a
green repository or release readiness; the later Task 2 review is recorded below.

## Task 2 verification, 2026-10-14

The production default-role path is implemented: shared stdin encoder hook,
`AgyAgent`, strict `AgyJSONLParser`, registered manifest/defaults, authentication
passthrough, reserved argument checks, and schema qualification. False inherits
saved permissions with no mode override; true is explicitly rejected until Task 4.
Selected metadata and bounded diagnostics remain Task 3; additional roles and
continuation acceptance remain Task 5. No dependency pins or credentials changed.

The [fresh design review](./review-design.md) approved corrections to outdated
conversation anchors, the exact rejection table, and client-selection wording.
It is a new report, not a reconstruction of the missing historical review.

| Verification | Result |
| --- | --- |
| Task 2 focused command plus `tests/test_tool_surface.py` | 247 passed; includes real Python subprocess fixtures waiting for EOF, both cwd modes, synthetic auth forwarding/precedence, parser failures, all argument sources, encoder-error cleanup, and unchanged legacy stdin bytes. |
| `python -m pytest tests/test_cli_only_mcp.py -q` | 6 passed after updating the three-client discovery expectation. The fixture pins agy to a missing executable, so no host installation affects this check. |
| `./code_quality_checks.sh` | Lint/Black/isort passed. Final run reached 1,176 passes before the existing TLS hostname dependency failure (15 deselected). |
| `python -m pytest tests/ -q -m 'not integration'` | Final run: 2,814 passed, 2 failed, 16 skipped, 15 deselected. Both failures are in unchanged `tests/test_dependency_security.py`: IDNA2008 hostname encoding and rejecting an invalid hostname before transport I/O. Installed AnyIO 4.13.0 differs from locked 4.14.2. |
| `./run_integration_tests.sh` | 2 passed, 2 existing consensus replay/connection failures, 11 skipped, 2,832 deselected. Ten skips lack `CUSTOM_API_URL`; one existing live-clink skip lacks its opt-in. |
| `python communication_simulator_test.py --quick` | 4 passed, 2 failed (`basic_conversation`, `per_tool_deduplication`), no skips; invalid Gemini API-key errors remain. This suite does not cover agy. |

Two separate requests exercised the production `CLinkTool.execute()` path in fresh
Python processes with the existing enterprise login, default scratch cwd, and no
forwarded API key. `/tmp/pal_agy_task2_smoke.py` is a disposable probe, not a CI test.

| Live request | Observation |
| --- | --- |
| Synthetic no-tool answer | Failed explicitly. The shared default role instructs terminal inspection; agy attempted `ls -la`, which saved command policy denied. Native exit was 0 with `SUCCESS` and an empty response; the new parser correctly rejected it. This is not a successful role acceptance result. |
| Separate native file-read request | Passed: exit 0, parser `agy_jsonl`, `continuation_available`, file-only marker returned, runner 17.055s. The fixture at `$TMPDIR/pal-agy-task2-read-_ydc31wx/marker.txt` was outside scratch cwd and removed afterward. PAL emitted a local dangerous-path notice while preparing the reference; the native CLI still returned the marker from the file. Exact scratch cwd was not retained. No follow-up was attempted. |

Both used only `agy --input-format stream-json --output-format stream-json` in
argv. The file-read request was a new caller-initiated probe, not automatic replay.
Each retained settings SHA-256
`86af8921ec53abfe4ca868b0f932ab991e3077351c669b1d3f56e062f3924a0d`.
No command allow-rule or permission bypass was added to make the failed request
pass. Role-driven tool use remains subject to operator policy and the Task 6
acceptance matrix; a no-tools user request does not override the shared role prompt.

`kk:review-code:isolated` approved this Task 2 slice with no P0–P3 findings through
the independent code-reviewer. External PAL expert analysis again failed due to
its invalid Gemini API key and supplies no corroboration. The reviewer did not
independently run tests or reproduce the live evidence. No systemic findings or
new project conventions required indexing. Full release acceptance remains pending.

## Task 3 verification, 2026-10-14

The production tool now returns selected agy metadata and bounded diagnostics.
`agy_status`, `agy_conversation_id`, and `agy_duration_seconds` stay separate from
PAL execution/continuation fields. Optional turn/token counts reject booleans,
negative values, and non-integers; duration accepts finite nonnegative numbers.
Only the five documented token counters are copied. `model_used` is selected from
`init.model` when supplied. The [official headless reference](https://antigravity.google/docs/cli/headless#streaming-json)
and Context7 were checked for these optional fields; their complete live
availability on 1.3.5 is not established by the sanitized transport fixture.

Both error content and stderr metadata are limited to 2,048 characters, including
the truncation suffix, with separate `error_truncated`/`stderr_truncated` flags.
Permission notices remain visible beside usable answers. Failed results expose
their status and string error description, without returning raw stdout/events.
The agent's existing nonzero-exit hook extracts that description but never
recovers success. No shared execution lifecycle or dependency pins changed.

| Verification | Result |
| --- | --- |
| `python -m pytest tests/test_clink_agy.py tests/test_clink_tool.py tests/test_clink_parsers.py -q -m 'not integration'` | 248 passed, no skips. Includes 53 added cases for metadata selection/invalid values, diagnostic boundaries, resolved-runner scoping, success/failure/timeout command privacy, no automatic replay, missing executable, timeout kill/drain/reap, and unchanged legacy diagnostics. |
| `./code_quality_checks.sh`, before/after | Ruff, Black, and isort passed. Before: 1,176 passed before the existing TLS hostname failure; after: 1,229 passed before the same failure (15 deselected). An initial sandboxed attempt stopped at a localhost bind restriction; the complete checks above ran with localhost access. |
| `python -m pytest tests/ -q -m 'not integration'` | 2,867 passed, 2 failed, 16 skipped, 15 deselected. Both failures remain in unchanged `tests/test_dependency_security.py` (IDNA2008 hostname and invalid-hostname validation). Environment: macOS, existing `pal` virtualenv, Python 3.14.8, installed AnyIO 4.13.0 versus locked 4.14.2. |
| `./run_integration_tests.sh` | 2 passed, 2 failed, 11 skipped, 2,885 deselected. Existing consensus replay fixtures lack matching OpenAI chat-completion requests. Ten prompt-regression skips lack `CUSTOM_API_URL`; the existing live-clink test lacks its opt-in. |
| `python communication_simulator_test.py --quick` | 4 passed, 2 failed (`basic_conversation`, `per_tool_deduplication`), zero skips. Invalid Gemini API-key errors remain; this suite does not cover agy. |

Task 3 verification uses controlled subprocess boundaries and the retained
fixture, not new live agy acceptance runs. It proves one process start per failed
or timed-out invocation, not cross-request deduplication or rollback. Native
permission/edit coverage remains Task 4; roles/continuation remain Task 5; the
deferred personal/ADC/missing-or-expired-session cases remain Task 6.3. The next
repository-wide verification needs the locked dependency environment, repaired
consensus cassettes, and a usable provider configuration; this slice does not
change those unrelated dependencies, fixtures, or credentials.

`kk:review-code:isolated` approved Task 3 through the independent code-reviewer
with no P0–P3 findings. The reviewer inspected the final code and strengthened
ASCII prompt-marker assertions but did not independently run tests or live agy.
External PAL review (`gemini-3.8-flash`) failed with `API_KEY_INVALID` and supplies
no corroboration. No systemic findings or new project conventions required
indexing. Task 3 is done; Tasks 4–6 and full release acceptance remain pending.

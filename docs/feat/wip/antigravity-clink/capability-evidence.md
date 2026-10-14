# Antigravity clink capability evidence

> Date: 2026-10-14
> Outcome: permission blocker removed by approved contract revision; Task 1 verification in progress; no agy client enabled
> Checkout tested: `3286d5f160014e162b183ab38ea826fd9a6ceab3`
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
by newly passing enforcement tests. Task 1's personal/ADC and missing/expired-login
coverage remains unverified; Tasks 2–6 are still pending. Adapter-owned argument
validation and request-local flag selection will be tested during implementation.
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
- Every model probe used `_build_environment()` with the proposed passthrough names
  `AGY_ADC_AUTH`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_QUOTA_PROJECT`, and
  `GOOGLE_CLOUD_LOCATION`. None of those four names was present in this session.
  The actual inherited names were `COLORTERM`, `HOME`, `LANG`, `LANGUAGE`, `LC_ALL`,
  `LC_CTYPE`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `TMPDIR`, and `USER`.
  No API-key variable was forwarded. No selector, provider, or model was forced.
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
non-ADC enterprise session cannot verify their 1.3.5 selection/routing semantics.
Next: test synthetic forwarding/override precedence in Task 2 only after this gate,
and run a prepared ADC environment for actual routing evidence.

| Acceptance | Evidence / gap |
| --- | --- |
| A1 | T1/T2: existing enterprise login without a forwarded API key. Personal, ADC routing, missing/expired-login cases unverified. |
| A2 | T1/T2 transport, R1 configured read, and R2b scratch-mode external read after user configuration. Actual roles remain Task 5. |
| A3 | Pending Task 5; no PAL continuation tested. |
| A4 (revised) | W1/W1b denial under current settings; E2/E3 native edit opt-in in both cwd modes with unchanged settings. False inherits native policy with no read-only guarantee. Production argv/description/argument-isolation tests remain Tasks 2/4/5; stronger native enforcement is deferred to #1233. |
| A5 | Pending Task 4; no production agy agent or path validation added. |
| A6 | Prompts stayed out of probe argv; E1–E3 additionally supplied `--mode accept-edits`. Production metadata/log assertions remain Task 3. |
| A7 | R2/W1 show zero-exit output rejected by the temporary parser. No production agy error path, timeout, missing/expired authentication, or no-replay test yet. |
| A8 | Existing environment/safe-default regressions checked below. No production code changed; full quality baseline failed. |

No personal account was made available. Missing/expired authentication was not
manufactured by logging out or changing the user's credentials. Linux, Windows,
and other enterprise authentication routes are unverified; these gaps stay assigned
to Task 1 and final Task 6 acceptance. Stronger enforcement under permissive saved
settings is separately deferred upstream. None of these unrun cases is presented
as a passing or skipped live test.

## Repository validation

The active environment was `/Users/sergio/.virtualenvs/pal`.

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

Those reviews predate the user's inherited-permissions contract revision. This
revision has user approval but has not received a new independent design review.

Optional documentation follow-up: run `kk:clarify-docs` on this file and the updated
`design.md`, `implementation.md`, and `tasks.md` if a shorter presentation is wanted;
retain the evidence limits, accepted contract, and remaining verification gaps.

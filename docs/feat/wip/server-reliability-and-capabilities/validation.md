# Validation evidence: Tasks 1–8

Recorded 2026-10-11. This covers the completed maintenance tasks below, not the
remaining roadmap or optional live-provider behavior.

## Task 8: explicit conversation storage errors

Recorded 2026-10-11. Storage reads/writes now raise `ConversationStorageError`
for expected backend I/O faults; malformed JSON/schema records raise
`ConversationDecodeError`. Original causes are retained and sensitive backend
details are excluded from public messages. Absent/expired records still return
`None`; `add_turn` retains `False` for absent/full threads, with `required=True`
providing distinct not-found/limit exceptions to callers. False backend write
acknowledgments are errors. Serialization and programming errors are not converted
to storage failures.

Audited server, simple-tool, workflow, clink and consensus callers. Required
continuation failures stop work; post-response failures retain results and add
`metadata.memory_persistence_warning` with `conversation_ready: false`. Initial
input ownership and pair admission remain Task 9. The substantive boundaries are
memory, its exception module, server, simple tools and workflow; clink/consensus
use those shared semantics. Exception identities live separately because test
configuration reloads the memory module.

Verification:

- `BLACK_NUM_WORKERS=1 ./code_quality_checks.sh`, locked Python 3.10 environment,
  outside process sandbox: **1,053 passed**, 4 skipped, 16 deselected,
  19 subtests passed; existing two AsyncMock warnings unchanged.
- 32 new tests cover missing/expired/corrupt/empty data, read/write faults, parent
  chain corruption, rejected writes, cap outcomes, original causes, programming
  errors, MCP/direct pre-provider failures, and answer preservation for chat,
  planner, clink and consensus. Existing fixtures now supply valid provider
  metadata and actual continuation threads instead of relying on silent failures.
- Pydantic **2.13.4** decode exception behavior verified against its
  [tagged source](https://github.com/pydantic/pydantic/blob/v2.13.4/pydantic/main.py)
  after Context7 had no matching version. No dependency pins changed.
- Integration/simulator setup limits remain those attempted in Task 7; no live
  provider requests or cassettes were changed.

- Focused memory/error/field-mapping/model-metadata suite: **58 passed**.
- Isolated code review: **APPROVE**, no actionable findings or systemic findings
  to index. External PAL reviewer unavailable.

## Task 7: Gemini unsupported-client diagnostics

Recorded 2026-10-11. Updated the Gemini parser and agent recovery to recognize
`UNSUPPORTED_CLIENT` in failed process output, empty/non-JSON stdout, and JSON
error envelopes. Successful textual responses, including marker quotations and
stale stderr diagnostics, remain intact. Non-object JSON now gives a parser error.
The remediation points to the official [installation guide](https://geminicli.com/docs/get-started/installation/)
and [troubleshooting guide](https://geminicli.com/docs/resources/troubleshooting/),
checked on this date; it makes no account-eligibility or migration-date claims.

- Baseline quality: **1,000 passed**, 4 skipped, 16 deselected.
- `python -m pytest tests/test_clink_gemini_parser.py tests/test_clink_gemini_agent.py -q`:
  **25 passed** using the locked Task 1 Python 3.10 environment.
- `BLACK_NUM_WORKERS=1 ./code_quality_checks.sh`: **1,021 passed**, 4 skipped,
  16 deselected, 19 subtests passed. Existing two AsyncMock warnings persist.
  Real-child tests stall in the process sandbox; the complete run passed outside it.
- Integration and quick simulator commands were attempted: they stop before tests
  at the absent `.pal_venv` and interpreter-path check respectively, as in Task 4.
  No live Gemini authentication or cassette recording was performed.
- Isolated code review: **APPROVE** after fixing a nonzero-exit case where a
  partial answer quoting the marker masked an unrelated error envelope. The
  regression test preserves the real edit failure. No systemic findings to index;
  external PAL reviewer unavailable.

## Task 1: MCP and Python compatibility

Python **3.10** is the corrected floor. The existing CI matrix tests 3.10–3.12;
README, getting-started documentation and both launchers already require 3.10+.
No supported 3.9 consumer was found. Removed the inconsistent 3.9 fallback
from the Windows launcher; formatter targets and publishing remain unchanged.

Selected MCP **1.30.0**, retaining `<2.0.0`, after checking the
[release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0),
[tagged requirements](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/pyproject.toml),
[security policy](https://github.com/modelcontextprotocol/python-sdk/blob/main/SECURITY.md)
and all 12 [published advisories](https://github.com/modelcontextprotocol/python-sdk/security/advisories).
The newest patched floor is 1.30.0 for GHSA-rwrf-2pqf-9j8j,
GHSA-84m7-p3x7-pcfv, GHSA-w4fh-qvv9-3v23, GHSA-5h93-6whr-6q8j and
GHSA-qx49-fqc8-xw99. PAL uses stdio; this does not establish an HTTP deployment's
authentication policy. Context7 lacked 1.30.0 coverage, so API verification used
tagged upstream source and the installed 1.30.0 source.

Recompiled both locks with pip-tools **7.6.1**, Python **3.11.13**, and
`pip-compile --strip-extras --no-header --quiet`. Only MCP changed from
1.27.2 to 1.30.0; the dev lock is byte-identical. Recompilation from the fresh
validation environment produced the same files.

Created empty virtualenvs under `/tmp/pal-task1-py310`, `py311` and `py312`,
then installed `-r requirements.txt -r requirements-dev.txt` into each. All
passed `python -m pip check`. Common installed versions: MCP 1.30.0, OpenAI
2.41.0, google-genai 2.8.0, Pydantic 2.13.4, pytest 9.0.3 and pip-tools 7.6.1.
Python-conditional transitive backports are resolved by pip on 3.10.

| Check | Result |
| --- | --- |
| Pre-change quality script in the existing pal environment | 910 passed, 4 skipped, 16 deselected; 2 existing AsyncMock warnings |
| Fresh Python 3.10.12: `BLACK_NUM_WORKERS=1 ./code_quality_checks.sh` | Lint/format/import checks passed; 913 passed, 4 skipped, 16 deselected, 19 subtests passed |
| Fresh Python 3.11.13: CI unit-test command | 913 passed, 4 skipped, 16 deselected, 19 subtests passed |
| Fresh Python 3.12.11: CI unit-test command | 913 passed, 4 skipped, 16 deselected, 19 subtests passed |
| SDK session compatibility tests (included above) | Resource advertisement/list/read, request-context progress, successful and explicit error tool results: 3 passed |

The CI command is `python -m pytest tests/ -v --ignore=simulator_tests/ -m
"not integration"`. The hosted workflow was not dispatched. Its existing matrix
and lock-freshness job needed no changes.

Two pre-existing unawaited AsyncMock warnings persist on every interpreter;
Task 15 owns their correction. Black 26.5.1 warns about its existing 3.13
target configuration on older interpreters, but AST checks passed. Multi-file
Black workers stalled inside the process sandbox; the successful quality run
used the same locked tools outside it without disabling checks.

### Live validation limits and next steps

`./run_integration_tests.sh` exited before collection because it hardcodes
the absent `.pal_venv`. The quick simulator also failed before scenarios:
its fallback checks `python` as a filesystem path. Fresh environments were
separate from developer state. No live API or CLI authentication was exercised
and no cassette was rerecorded.

Before release, provision the documented `.pal_venv` and explicitly configured
local/live targets, then rerun `./run_integration_tests.sh` and
`python communication_simulator_test.py --quick`. Task 34 already owns simulator
interpreter/session fixes. These live gates remain open; the lock and SDK
compatibility gates above are complete.

Independent `kk:review-code:isolated` review: **APPROVE**, no actionable findings
and no systemic findings to index. The code-reviewer sub-agent completed review;
PAL external codereview tools were unavailable. Windows launcher execution and
hosted CI were not exercised locally.

## Task 2: container

Verified the official Python image's
[source](https://github.com/docker-library/python/blob/cede844ace77284e32c03b61ebc35cdfc945e862/3.11/slim-trixie/Dockerfile)
and registry manifest on 2026-10-11. Both stages now use
`python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce`.
The multi-platform index contains amd64 and arm64; this run built **linux/amd64** only.

Ran `docker build --pull --no-cache --progress=plain -t
pal-mcp-server:reliability-task2-20261011 .`. Both stages applied available Debian
updates, including `liblzma5` 5.8.1-1+deb13u1 → 5.8.1-1+deb13u2. The first scan
also found old packaging tools in the base interpreter. Updating those in the
runtime stage reduced Python findings from 44 to 35. Both active interpreters now
have pip 26.2.1, setuptools 84.0.0 and wheel 0.48.0. Application pins remain those
validated in Task 1; no container-only override of the lock was introduced.

Final image ID: `sha256:337d05445b3cbf0ef447ad383aaed19e498c151b770026296b167f5892ab8f47`.
Docker reported 239,143,184 bytes. Version labels are 1.3.0, with existing local
source/documentation/maintainer identity retained and SPDX `Apache-2.0` metadata.
No image was pushed.

Verification:

- `tests/test_docker_implementation.py`, `test_docker_security.py` and
  `test_docker_healthcheck.py`: **44 passed**.
- Full quality script from the fresh Task 1 Python 3.11 environment: **913 passed**,
  4 skipped, 16 deselected, 19 subtests passed; the same 2 existing warnings.
- Started the actual image with `--network none`, open stdin and a synthetic
  OpenAI key. The unchanged healthcheck passed, as did `python -m pip check`.
  Runtime was Python 3.11.17, MCP 1.30.0 and UID/GID **999/999**.
- Checked the final image lacks `.capy`, `.claude`, `.codex`, `.agents`, `.mcp.json`,
  build and dist directories. `.dockerignore` also excludes `.aws` and `.env*`.
  The environment-file existence probe was blocked by the local security hook;
  environment-file exclusion is based on recipe inspection, not that runtime probe.
- Removed both temporary validation containers. Health success proves local process,
  imports, writable logs and basic key-format checks, not provider authentication.

### Scan and unresolved release gate

The full unfiltered vulnerability report is retained in [container-scan.json](container-scan.json).
Command: `trivy image --image-src docker --scanners vuln --ignorefile /dev/null
--list-all-pkgs=false --cache-dir /tmp/pal-task2-trivy-cache --format json
--output docs/feat/wip/server-reliability-and-capabilities/container-scan.json
pal-mcp-server:reliability-task2-20261011`.
Trivy **0.70.0**, database updated **2026-10-11 07:05:14 UTC**, final scan at
**2026-10-11 10:10:07 UTC**. This is a package vulnerability scan; secrets,
misconfiguration and other architectures were not scanned.

| Source | Critical | High | Medium | Low | Unknown | Total package/advisory occurrences |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Debian 13.7 | 0 | 44 | 58 | 61 | 1 | 164 |
| Python, including bundled copies | 2 | 18 | 14 | 1 | 0 | 35 |

**The image is not cleared for release as vulnerability-free.** Task 2 permits
unresolved findings with actionable follow-up; the following register covers every
reported package. Counts include repeated CVEs across packages/copies. Exact IDs,
versions, source links and scanner statuses remain in the JSON. Applicability below
is a deployment assessment, not a demonstrated exploit or a suppression decision.

#### Locked Python and bundled-tool follow-up

For each patched version below, first verify upstream advisories and Python support,
update the affected constraints/pins with pip-tools, rerun the fresh 3.10–3.12
matrix, then rebuild and rescan. These changes are deferred because Task 1 was a
scoped MCP upgrade and Task 2 retains that validated application lock; do not patch
only the image and leave local installations on different versions. Prioritize
AnyIO's TLS finding and other potentially reachable provider-transport dependencies.

| Reported package/version | Occurrences | Applicability and concrete next step |
| --- | ---: | --- |
| PyJWT 2.13.0 | 14 | MCP OAuth/JWT paths are not used by the stdio entry point. Verify and lock >=2.15.0 for the 13 findings with fixes; separately track CVE-2026-103001, for which the scan has no fix, and test verification-options isolation before any HTTP/auth deployment. |
| anyio 4.13.0 | 2 | Used by MCP/HTTP transports; TLS hostname handling may be reachable. Verify and lock >=4.14.2, then test TLS/IDNA rejection and process cleanup. Critical CVE-2026-63374 remains open. |
| cryptography 48.0.0 | 4 | Provider authentication/crypto dependency; exact certificate and PKCS#7 paths were not exercised. Verify >=50.0.0 against auth consumers, including its bundled OpenSSL, before regenerating locks. |
| pyasn1 0.6.3 | 3 | Google authentication dependency; crafted ASN.1 parsing exposure is unverified. Verify and lock >=0.6.4; rerun Google auth/provider tests. |
| pydantic-settings 2.14.1 | 1 | Nested secrets source symlink issue; use of the affected source was not established. Verify and lock >=2.14.2, and test any configured secrets directory policy. |
| starlette 1.2.1 | 2 | HTTP form/path handlers are outside PAL's stdio entry point. Verify and lock >=1.3.1 and retain HTTP parser tests before enabling such transport. |
| urllib3 2.7.0 | 6 | Includes repeated reports for a bundled copy. Requests/provider HTTP use may expose proxy and response-decoding paths. Verify >=2.8.0 in the lock and update the bundling tool separately; test proxy TLS and malformed compressed/chunked responses. |
| msgpack 1.1.2 | 1 | pip vendors msgpack in both interpreter trees. Update pip when its bundled copy reaches >=1.2.1; do not treat installing a separate msgpack as a vendored fix. Recheck the unpacker/cache path and rescan. |
| setuptools 70.3.0 (scanner-reported) | 2 | Both active interpreters report 84.0.0, but Trivy attributes this older component to the copied venv layer without a path. Resolve its location with a complete SBOM/archive inspection and verify >=83.0.0 or removal of that bundled component. Keep both findings open until the discrepancy is explained; it is not proof the active setuptools is vulnerable. |

#### Debian follow-up

The scanner reported **no fixed Debian version** for these remaining occurrences.
For every CVE in each row, track the corresponding Debian security-tracker entry;
when a patched trixie package/base appears, rebuild with `--pull --no-cache` and
rescan. If a package is not required, verify reverse dependencies and runtime
behavior before proposing removal. These are deferred for lack of a distribution
fix and incomplete reachability evidence, not silently accepted as harmless.

| Installed packages/version | Occurrences | Applicability assessment |
| --- | ---: | --- |
| apt, libapt-pkg7.0 3.0.3 | 2 | Package-manager paths; used during builds, not normal server requests. |
| bash 5.2.37-2+b10 | 1 | Shell/CLI invocation can reach it; assess each advisory's local-input conditions. |
| bsdutils 1:2.41.5-0+deb13u1; libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, mount, util-linux 2.41.5-0+deb13u1; login 1:4.16.0-2+really2.41.5-0+deb13u1 | 54 | Utility/mount/login paths; non-root stdio does not establish reachability of privileged operations, and no privileged container was tested. |
| coreutils 9.7-3 | 4 | Local utility execution is possible through configured CLIs. |
| dash 0.5.12-12 | 2 | Build/default shell; assess local shell inputs. |
| diffutils 1:3.10-4 | 1 | Local utility inputs; not exercised by the health check. |
| gcc-14-base, libgcc-s1, libstdc++6 14.2.0-19 | 6 | Native runtime libraries; affected code-path reachability remains unverified. |
| libacl1 2.3.2-2+b1 | 2 | ACL parsing through filesystem utilities; local-input conditions need review. |
| libattr1 1:2.5.2-3 | 1 | Extended-attribute utility/library paths; unverified. |
| libbz2-1.0 1.0.8-6 | 1 | Compression available to Python; assess untrusted compressed inputs. |
| libc-bin, libc6 2.41-12+deb13u4 | 42 | Core runtime is used; individual vulnerable interfaces were not exercised. |
| libncursesw6, libtinfo6, ncurses-base, ncurses-bin 6.5+20250216-2 | 8 | Terminal/terminfo paths may be reached by local CLIs. |
| libpam-modules, libpam-modules-bin, libpam-runtime, libpam0g 1.7.0-5 | 4 | No login/PAM service was configured in the validation container. |
| libsqlite3-0 3.46.1-7+deb13u2 | 4 | SQLite is present; the server's current conversation store is in memory. Review any CLI/database consumers separately. |
| libsystemd0, libudev1 257.13-1~deb13u1 | 14 | Libraries installed; no systemd/udev daemon was launched. Library-path exposure remains unverified. |
| login.defs, passwd 1:4.17.4-2 | 6 | Account-management tooling; non-root execution limits but does not prove absence of local exploitation. |
| perl-base 5.40.1-6+deb13u1 | 4 | Runtime/utility inputs; no Perl application path tested. |
| sysvinit-utils 3.14-4 | 1 | Init utilities present; container entry point is Python. |
| tar 1.35+dfsg-3.1 | 5 | Archive extraction can be reached by local tools; review untrusted archive consumers. |
| zlib1g 1:1.3.dfsg+really1.3.1-1+b1 | 2 | Compression library can be reached by Python/network consumers; treat as unresolved. |

Independent isolated code review: **APPROVE**, no actionable findings and no
systemic findings to index. The reviewer verified the register covers every
reported package and matches the raw severity totals; this approval does not
close the security release gate. External PAL reviewer unavailable.

## Task 3: license and distributions

Compared LICENSE directly with the
[Apache Software Foundation's canonical text](https://www.apache.org/licenses/LICENSE-2.0.txt)
on 2026-10-11. Restored the complete text, including the original definitions,
copyright grant, liability section and appendix. The result matches **byte for
byte**, including whitespace: **11,358 bytes**, SHA256
`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.

Moved the existing `Copyright 2025 Beehive Innovations` and its original URL into
NOTICE. The fork URL is already documented in README; no new ownership claim or
copyright year was introduced. `config.py` author/version identity is unchanged.

Verified PEP 639 support against
[setuptools 84.0.0's versioned documentation](https://github.com/pypa/setuptools/blob/v84.0.0/docs/userguide/pyproject_config.rst).
Set `project.license = "Apache-2.0"` and explicit `project.license-files` for
LICENSE and NOTICE. Raised the build backend minimum to **77.0.0**, the documented
first release supporting these fields. No manifest override was necessary.

Ran `/tmp/pal-task1-py312/bin/python -m build --outdir /tmp/pal-task3-dist`.
The isolated build used setuptools **84.0.0** and built the wheel from the sdist.
Inspected both archives with Python's tarfile/zipfile and email metadata parser:

- sdist: `pal_mcp_server-1.3.0/LICENSE` and `NOTICE` exactly match repository bytes.
- wheel: `pal_mcp_server-1.3.0.dist-info/licenses/LICENSE` and `NOTICE` exactly match.
- Both PKG-INFO and METADATA declare metadata version **2.4**,
  `License-Expression: Apache-2.0`, both `License-File` entries, and
  `Requires-Python: >=3.10`.
- Wheel SHA256: `1a29f9d1798081e144b49da03f926fe3e28d1ac5a0bc03cd3330c3da97e10d70`.
- sdist SHA256: `401371bd36b1bebe63f8027636fe84d3861efe664697539390f826e59c32c5bb`.

Recompiled both pip-tools locks and verified no diff. The full quality script in
the fresh Python 3.11 environment passed: **913 passed**, 4 skipped,
16 deselected, 19 subtests passed; the same 2 pre-existing AsyncMock warnings.
Builds stayed local; no distributions were published. The live-validation and
container security gates documented above remain open.

Independent isolated code review: **APPROVE**, no actionable findings and no
systemic findings to index. External PAL reviewer unavailable.

## Implementation notes for the next slice

The existing developer environment differed from the committed locks, which made
the fresh matrix essential. The container retained a second packaging-tool
installation outside its venv; patching only the venv missed it. Remaining scan
findings require the explicit dependency/release follow-ups above. License repair
was substantive text restoration, not just formatting. No architecture, provider
behavior or release-workflow changes were needed for Tasks 1–3.

## Task 4: generated chat artifacts

Recorded 2026-10-11. Chat captures `PAL_WORKSPACE_ROOT` at construction (default:
startup cwd), validates artifact-capable requests before generation even for
prebuilt continuation prompts, and creates unique private files under
`.pal_artifacts`. Directory-descriptor/no-follow opens and exclusive file creation
reject linked sandbox/destination entries without unlinking existing files.
Unsupported platforms fail explicitly. Context-file reads keep their independent
policy. Artifact history retains the raw reply on both initial and continued
calls; ordinary chat history is unchanged pending Task 9.

Changed chat, its small SimpleTool validation/recording hooks, chat unit/replay
tests and temporary-workspace test configuration; updated chat/configuration docs
and release notes. The local security hook blocks the environment template, so its
planned environment example is documented in `docs/configuration.md` instead.
No dependency pins or recorded request bodies changed.

- Baseline quality: **913 passed**, 4 skipped, 16 deselected, 2 existing warnings.
- Final `BLACK_NUM_WORKERS=1 ./code_quality_checks.sh` in the fresh-lock Python
  3.10.12 environment: **933 passed**, 4 skipped, 16 deselected, 19 subtests passed.
  Ruff, Black and isort passed. Black required execution outside the process
  sandbox; no check was disabled. The same 2 pre-existing AsyncMock warnings remain.
- Coverage includes traversal/outside roots, linked root/working/sandbox paths,
  file/symlink/dangling-link collisions, concurrent distinct paths/content, nested
  directories with spaces, private modes, frozen root configuration, independent
  read paths, unsupported platforms, write errors without inline fallback, and
  actual raw-reply storage on first/continued artifact calls. Existing Gemini
  artifact replay and ordinary cross-provider continuation replay passed unchanged.
- Integration script and quick simulator still stop during environment setup:
  absent `.pal_venv` and simulator interpreter-path detection respectively. The
  Task 1 live-validation follow-up remains open. No live API calls were recorded.

Independent `kk:review-code:isolated`: **APPROVE**, no remaining findings. Review
identified and verified the correction to initial artifact-reply storage; external
PAL tools were unavailable. No systemic review findings needed indexing.

## Task 5: bounded file reads and path policy

Recorded 2026-10-11. Added `/proc`, `/sys`, `/dev` and `/run` exclusions while
preserving sensitive-home entries and exact-root semantics. Requested and resolved
system paths are both checked, including descriptor-style pseudofilesystem paths
that resolve elsewhere. Token estimation validates paths before size metadata.
Both text-reader entry points read at most `max_size + 1` bytes, classify overflow
from that read and then decode using their existing replacement/ignore behavior.
Successful headers, line numbering and newline normalization are preserved.

Changed `utils/file_utils.py`, `utils/security_config.py`, new
`tests/test_path_policy_pseudofs.py`, configuration documentation and release notes.
No dependency changes were required.

- Focused readers/path/formatting suite: **87 passed** before the additional macOS
  alias regression; that regression is included in the full result below.
- `BLACK_NUM_WORKERS=1 ./code_quality_checks.sh` in the fresh-lock Python 3.10.12
  environment: **980 passed**, 4 skipped, 16 deselected, 19 subtests passed.
  Ruff/Black/isort passed; only the 2 previously recorded AsyncMock warnings remain.
- New cases cover pseudofilesystem roots/descendants, denied symlink targets,
  descriptor aliases, no stat/open on denial, zero and oversized reported sizes,
  bounded reads/closure, exact byte limits, multibyte UTF-8, empty and negative
  limits, existing success formatting, ordinary home code and simulated macOS
  system/temporary path aliases. No real credential or pseudofilesystem content
  was read. Existing sensitive-home and file-protection tests passed in the suite.
- Integration/simulator setup limitations remain those verified in Task 4; no
  provider behavior changed and no live credentials were used for this slice.

Independent `kk:review-code:isolated`: **APPROVE** after fixing a newly introduced
validation/use race. Path validation now checks and returns the same canonical
target; a synthetic changing-resolution regression verifies this. The systemic
lesson was indexed by the review workflow. External PAL reviewer unavailable.

## Task 6: clink environment and working directory

Recorded 2026-10-11. Child environments now combine an explicit shared runtime
allowlist, target-CLI authentication names and operator `env_passthrough` additions,
then apply explicit `env` values. Windows matching/overrides are case-insensitive;
executable lookup uses the child's `PATH`. HOME and auth/config locations remain
available; this does not isolate credentials stored on disk.

Configured working directories are validated and preserved. Otherwise the runner
owns a private per-call temporary directory, cleans it after results/errors and
kills/reaps interrupted children before cleanup. Output-capture files configured
for cleanup are removed on spawn/parse/communication failures too. Edit arguments,
editable paths and system prompts are forwarded unchanged. Codex skips its Git
repository check only in PAL-created scratch directories; configured cwd retains
its repository policy. Scratch cwd is not a filesystem sandbox.

Changes are in the base runner and small Codex hook, shared/vendor constants,
mechanical configuration models/registry, clink tests, tool docs and release notes.
No Python dependency pins changed.

### Contract evidence and real CLI check

Verified Python 3.10 standard-library subprocess/temp-directory APIs through
versioned documentation. Checked vendor environment contracts via
[Gemini authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx),
[Claude environment variables](https://code.claude.com/docs/en/env-vars),
[Claude Bedrock](https://code.claude.com/docs/en/amazon-bedrock) and
[Claude Vertex configuration](https://code.claude.com/docs/en/self-hosted-environments-configuration).
Gemini CLI is not installed here; no version-specific live Gemini behavior is claimed.
Claude Code **2.1.296** local help/version was checked, without calling its service.

Codex CLI **0.162.1** local help and the official
[non-interactive contract](https://learn.chatgpt.com/docs/non-interactive-mode#git-repository-required)
confirm the Git requirement and `--skip-git-repo-check`. A real CLI smoke through
`CodexAgent.run` used the shipped command/role with test-only overrides:
`--ignore-user-config --ephemeral --sandbox read-only`, a custom Responses provider
at a loopback-only HTTP server, disabled web search/update checks, synthetic keys
and temporary CLI state. The stub emitted a completed assistant SSE response.
No live vendor service, actual credential or repository mutation was involved.

`/tmp/pal-task1-py310/bin/python /tmp/pal-task6-codex-smoke.py` returned:

```json
{"result":"PAL_OFFLINE_OK","returncode":0,"requests":[["/v1/responses","pal-smoke"]],"scratch_removed":true,"external_credentials":false}
```

This proves actual CLI startup, non-Git cwd compatibility, filtered environment,
request/JSONL parsing and cleanup through the runner. It does not prove vendor
account authentication or model quality. The general live integration/simulator
setup limitations from Task 4 remain open.

### Automated validation

The existing clink suite first passed **53 tests**. Added synthetic environment,
override, Windows, real child cwd/environment, failure/timeout/repeated-cancellation,
reaping-before-cleanup, configured-dir preservation, spawn/output cleanup,
ContextVar reset, executable PATH, concurrent-private-directory and Codex policy
regressions. Real subprocess and Black worker checks require execution outside
this session's process sandbox; no checks were disabled.

Final `BLACK_NUM_WORKERS=1 ./code_quality_checks.sh` in the fresh-lock Python
3.10.12 environment: **1,000 passed**, 4 skipped, 16 deselected, 19 subtests passed.
Ruff/Black/isort passed. The 2 previously documented AsyncMock warnings remain.

The isolated reviewer identified an indefinite cancellation wait when a descendant
holds an inherited output pipe. The fix bounds draining to five seconds, closes
the owned subprocess transport and joins immediate-child reaping before removing
scratch cwd. A real parent/descendant regression proves cancellation finishes while
the descendant still holds the pipe; the test explicitly terminates that descendant.
This does not introduce process-tree containment. Asyncio Process has no public
pipe-close API, so this narrowly uses its owned `_transport.close()`; verified the
implementation on Python 3.10–3.12. Future Python compatibility must retain this check.

The lifecycle/environment plus Codex suites passed **22 tests each** on Python
3.11.13 and 3.12.11. Native Windows was not run; case handling is simulated and
permission assertions are POSIX-only. Full Python 3.10 quality was rerun after the
final regression assertion change with the 1,000-pass result above.

Independent `kk:review-code:isolated`: **APPROVE**, no remaining findings after the
cancellation and test-portability fixes. External PAL reviewer unavailable; no
P0/P1 systemic findings needed indexing. The cancellation-testing pattern was
indexed separately by `kk:test`.

### Reflection on Tasks 4–6

Artifact preflight needed a small SimpleTool hook because prebuilt continuation
prompts bypass prompt preparation. Raw artifact storage also needed a stateless
recording hook for the first reply, while ordinary history remains unchanged until
Task 9. File-policy review caught a double-resolution race, now covered with a
changing-target fixture. CLI cleanup needed bounded output draining independently
of child exit; a real descendant test exposed why repeated cancellation must not
wait forever for pipe EOF. The environment-template documentation moved to the
configuration guide due to the local security hook. These are the only material
scope adjustments; the feature remains in progress with Tasks 7–42 pending.

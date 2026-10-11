# Validation evidence: Tasks 1–3

Recorded 2026-10-11. This covers the first three maintenance tasks, not the
remaining roadmap or optional live-provider behavior.

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

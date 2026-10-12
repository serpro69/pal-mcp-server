# Actionable reliability follow-ups

Recorded 2026-10-12. The user requested fixes and a commit while explicitly leaving
Task 42 final verification pending and excluding unavailable OpenCode Go/Jules
checks. This record covers that maintenance batch; it does not close the roadmap's
final acceptance or claim live provider validation.

## Completed changes

- **C1–C3:** Restore chat search guidance on first and continued requests; wire
  ThinkDeep's validation hook and retain problem/focus scope across fresh workflow
  instances; replace codereview's accidental literal newline escapes. The
  [follow-up register](capture-followups.md) records each resolution.
- **Integration launcher:** Use an activated virtualenv, with checkout-local
  `.pal_venv` as fallback. Resolve the checkout regardless of caller cwd, quote
  paths, reject invalid modes and preserve child failures. `--stub` runs only the
  existing isolated local simulator. Default/live modes remain explicit.
- **Logging examples:** Remove ineffective `LOG_MAX_SIZE`/`LOG_BACKUP_COUNT`
  settings from Compose, PowerShell setup and deployment docs. The environment
  template remains unchanged because the repository security hook denied access
  to `.env.example`. Existing values are harmless but ignored, as explained in
  [logging](../../../logging.md#rotation-and-diagnostics).
- **Security dependencies:** Raise minimum versions for seven already-present
  MCP/auth/HTTP transitives in package metadata, so non-lock installs also receive
  the security floors. Regenerate both locks without unrelated upgrades.
- **Runtime image:** Keep pip-based installation in the builder, remove unused
  setuptools/wheel, run `pip check`, then remove pip. Remove the base interpreter's
  installation tools and ensurepip bootstrap wheels too. Runtime dependency changes
  require rebuilding the image; this removes bundled vulnerable copies rather than
  trying to patch vendored libraries independently.

## Dependency evidence

Context7 lacked matching versions for most packages; verified releases against
official changelogs/release metadata and PyPI on 2026-10-12. All selected releases
support Python 3.10. PyJWT 2.15.1 also restores compatibility with padded JWS
segments; cryptography uses the latest 50.0 patch release. Other updates select
the recorded patched floor to limit unrelated changes.

| Dependency | Previous pin | New pin | Evidence |
| --- | --- | --- | --- |
| AnyIO | 4.13.0 | 4.14.2 | [Version history](https://anyio.readthedocs.io/en/stable/versionhistory.html): IDNA 2008 certificate-host identity and process fixes |
| PyJWT | 2.13.0 | 2.15.1 | [Versioned changelog](https://pyjwt.readthedocs.io/en/2.15.1/changelog.html) |
| cryptography | 48.0.0 | 50.0.2 | [Official changelog](https://cryptography.io/en/50.0.2/changelog/) |
| pyasn1 | 0.6.3 | 0.6.4 | [Official release](https://github.com/pyasn1/pyasn1/releases/tag/v0.6.4) |
| pydantic-settings | 2.14.1 | 2.14.2 | [Official release](https://github.com/pydantic/pydantic-settings/releases/tag/v2.14.2) |
| Starlette | 1.2.1 | 1.3.1 | [Official release](https://github.com/Kludex/starlette/releases/tag/1.3.1) |
| urllib3 | 2.7.0 | 2.8.0 | [Proxy TLS advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77) and [release metadata](https://pypi.org/project/urllib3/2.8.0/) |

The new TLS tests exercise the installed AnyIO/SSL peer-name construction:
`faß.de` retains its IDNA 2008 identity instead of becoming `fass.de`, and a
disallowed Unicode hostname fails before transport I/O. They do not claim a live
certificate-chain handshake. Existing SDK, auth import, cancellation and stdio
tests exercise compatibility with the new dependency set.

## Container findings and remaining gate

Built linux/amd64 using the existing pinned Python 3.11.17 slim-trixie base, with
`docker build --pull --no-cache --progress=plain -t pal-mcp-server:actionable-fixes-20261012 .`.
The recipe applied available distribution updates in both stages with the build
cache disabled. The base digest is unchanged.

Final image: `sha256:c6ab9738e62d42ec11a94a4cc43e476f7759f9015f61f02dc4d6eb4792eb9298`.
Trivy 0.70.0 ran with `--scanners vuln --ignorefile /dev/null` and its current
database. The [complete compacted finding list](container-scan-actionable-fixes.json)
retains every vulnerability, installed/fixed version, status, severity and source
URL, plus the raw report checksum. Long descriptions and package inventory are
omitted; the original raw report was saved at `/tmp/pal-fixes-container-scan-final.json`.

| Scan | Python findings | Critical findings | Total occurrences |
| --- | ---: | ---: | ---: |
| Task 2 retained baseline | 35 | 2 | 199 |
| Updated application pins, packaging tools still present | 6 | 0 | 170 |
| Final image without runtime packaging tools/bundles | 0 | 0 | 164 |

**The security release gate remains open for Debian findings:** 44 high, 58 medium,
61 low and 1 unknown, with no fixed version reported in this scan. None were
suppressed or declared harmless. The [Task 2 package register](validation.md#debian-follow-up)
still supplies applicability questions and next actions: track the Debian security
tracker, verify runtime/reverse dependencies before removing packages, then rebuild
without cache and rescan when distribution fixes or a verified base update exist.
Zero Python findings means none detected by this scan, not vulnerability-free code.

The actual image passed its health check with a synthetic key and no network,
running as UID/GID 999 under `--read-only`, `--cap-drop ALL`, `no-new-privileges`,
a temporary `/tmp`, and a fresh named state volume. Log writes succeeded; recording
a synthetic turn produced a 0700 transcript directory and 0600 file. Imports of
pip/setuptools/wheel/ensurepip are absent. The validation container and volume were
removed. The first probe incorrectly expected the transcript directory before any
record was written; the corrected probe exercises the real recording lifecycle.

## Verification and review

- Pre-change `./code_quality_checks.sh`: **2,007 passed**, 16 skipped,
  16 deselected, 19 subtests; lint/format/import checks passed.
- Fresh environments installed both regenerated locks and passed `pip check`:
  Python **3.10.12**, **3.11.13**, **3.12.11**. Full offline suites initially
  passed **2,017 tests each**, with 16 existing skips and 16 integration exclusions.
- Final Python 3.12 `./code_quality_checks.sh`: **2,019 passed**, 16 skipped,
  16 deselected, 19 subtests; Ruff/Black/isort passed. The nine additional TLS and
  corrected launcher regressions passed on Python 3.10 and 3.11 as well.
- Regenerating both locks from seeded temporary copies with pip-tools 7.6.1
  produced byte-identical output. `bash -n run_integration_tests.sh` passed.
- `./run_integration_tests.sh --stub`: **3/3 passed**, with credential isolation
  and child cleanup. This replaces the local launcher's setup blocker, not live
  reasoning/provider coverage.
- [Updated synthetic prompt evidence](prompt-fixes-capture.json): **19/19 complete**,
  preserved system instructions, complete audits and all three corrected probes.
  Hash-seed 1/7/42 checks pass. Historical measurements remain unchanged; new prefix
  values follow intentional content restoration and establish no live cost savings.
- Independent isolated code review found no P0–P2 findings and one P3 test-helper
  portability issue: a shebang could split a Python path containing spaces. Fixed
  with a quoted shell trampoline; all seven launcher tests also passed through an
  actual environment path containing spaces. Re-review **approved with no remaining
  findings**; no systemic findings needed indexing. External PAL review reached its second step but
  failed with `API_KEY_INVALID`; independent local review supplies the review.

Task 42, optional live service/provider checks, live cassette recording, native
Windows launcher/ACL support and live cache-quality/cost claims remain outside
this batch. The changes preserve project identity and repository instructions;
the capy database is excluded from the commit.

Reflection: the prompt fixes needed to preserve complete historical JSON rather
than indiscriminately ban escaped newlines, and the container needed removal of
bootstrap/vendored copies in addition to application dependency upgrades. The
remaining security findings cannot be cleared by claiming that the tests pass.

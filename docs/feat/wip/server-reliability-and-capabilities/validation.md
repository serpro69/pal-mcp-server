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

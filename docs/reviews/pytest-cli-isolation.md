# Pytest CLI manifest isolation

Recorded 2026-11-21 against base `1eddfce4141fc8e2a88a8f073f64146e20340129`.

The change isolates inherited PAL client manifests before pytest collection and
resets the environment override/cache before each test. Tests can still provide
their own manifests. Session cleanup restores the prior registry and environment;
personal files and native CLI authentication locations are untouched. Production
registry resolution and the live CLI harness are unchanged. Ordinary stdio and
request-capture subprocesses activate the shared test-only CLI bootstrap before
importing the server; patching only the parent pytest process was insufficient.

## Deterministic verification

- The original quality run stopped with **822 passed, 1 failed, 26 deselected**:
  the personal Claude manifest lacked the `planner` role expected by a test.
- The initial four nested-pytest regressions failed before the fixture fix. They cover malformed
  personal/environment-selected manifests, a pre-existing registry cache, an
  intentional test failure, and restoration after pytest exits.
- After the fix, the focused suite passed **369 tests** in the working checkout.
- Final `./code_quality_checks.sh` passed **3548 tests**, with 17 skipped and
  26 integration cases deselected; lint and formatting passed. This used a clean
  checkout to avoid operator dotenv input, with no external CLI-isolation plugin
  and no HOME override. The repository fixture provided the CLI isolation.
  The intermediate parent-only candidate passed 3547 tests before ordinary-child
  coverage was added.
- The four subprocess regressions also passed after adding exact comparisons of
  the live child environments before and inside pytest for Codex and Claude.
  Authentication values are compared without printing them. Per-test synthetic
  manifest overrides remain usable and do not leak into the following test. This
  comparison guards the live environment allowlist and authentication locations;
  it does not compare native stdin/argv or explain the Codex response difference.
- Ordinary child coverage was added after review identified fresh-interpreter
  manifest reads. The negative control uses valid JSON with an invalid history
  budget: the existing JSON helper silently skips syntax-invalid JSON, which
  would not establish this failure. The control requires the original server
  startup to fail validation before the isolated stdio bootstrap can pass.
  The focused isolation/stdio/transcript/request-capture suite passed **112 tests**.
  There are now five isolation regression cases in total.
- Integration selection without native providers returned **4 passed, 22 skipped**;
  the strict stub passed **3/3**. The API-backed quick simulator returned **2/6**
  without provider configuration, consistent with the open #26 prerequisite.

## Live checks and limitations

The opt-in harness, prompts, and assertions were not changed.

| Check | Recorded outcome |
| --- | --- |
| Candidate pytest, Claude with `opus` | Passed the first request and dependent follow-up. |
| Candidate pytest, Codex with `gpt-6.2-sol`, working checkout | Two attempts failed the first-answer assertion. Codex connected and replied, but treated the embedded request as untrusted data instead of returning the expected tag. |
| Same Codex harness outside pytest | Passed both requests. |
| Unchanged baseline conftest, temporary checkout | Passed both requests. |
| Candidate conftest, same temporary checkout | Failed the same first-answer assertion. |

These results do not establish why the Codex responses differ. Authentication and
execution were reached, and the deterministic child-environment comparison found
no change, but **Codex live semantic acceptance is not green**. No assertion was
relaxed and no failure was counted as a pass.

**Follow-up owner: clink live-test maintainer (unassigned).** The Codex refusal
remains open pending a controlled comparison of the delivered invocation. No
prompt or assertion changes were made in this fixture task. Capture and compare
the exact delivered prompt, CLI settings, version, and response under a fixed
scenario; investigate the trust-boundary
interpretation before changing either prompts or assertions. Close this gap only
after first-request/follow-up behavior is verified without bypassing trust checks.

Native-provider live checks remain unavailable with subscription-only CLI access;
that work is tracked in [#26](https://github.com/serpro69/pal-mcp-server/issues/26).
Its owner must supply approved API targets and a budget plus the local-model
endpoint before claiming native-provider validation.

**Separate follow-up owner: CLI configuration maintainer (unassigned).**
`utils/file_utils.py::read_json_file` returns `None` on invalid JSON, so the
JSON-decode exception handler in `clink/registry.py::_load` is not reached on that
production path. Syntax-invalid manifests can therefore be silently skipped.
This pre-existing behavior is deferred because this change isolates test inputs;
it does not change production configuration error semantics. Add strict decoding
at the CLI manifest boundary, preserving the generic optional-reader contract,
and verify that malformed manifests fail clearly while absent optional paths
remain allowed.

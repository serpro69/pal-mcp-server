# Implementation plan: Antigravity clink

> Status: implementation pending; design review findings addressed
> Created: 2026-10-10
> Design: [design.md](./design.md)
> Tasks: [tasks.md](./tasks.md)
> Evidence: [verification.md](./verification.md)
> Review: [review-design.md](./review-design.md)
> Initial acceptance target: `agy` 1.3.3 with existing login and no required API key

## Implementation prerequisite

Task 1 is a capability investigation, not a claim that the permission contract is implemented. No enabled agy client or production transport changes precede its successful outcome. The requirement is default non-editing behavior even with permissive saved settings, explicit edit opt-in, and unchanged authentication/configuration ownership. Exact flags or policy APIs are intentionally unspecified until verified.

If the mechanism cannot be demonstrated within the approved architecture, record what failed in the capability evidence, mark Task 1 blocked with that reason, and return to design. Do not fill the gap with a prompt instruction, mandatory API key, unrestricted permission bypass, or automatic user-settings edits. Later task dependencies prevent shipping a partially verified integration.

## Repository orientation

`ClinkRegistry` loads built-in JSON manifests, then environment and user overrides. Names still need an entry in `INTERNAL_DEFAULTS`; manifest-only registration is insufficient. `CLinkTool` builds its CLI/role schema from this registry and currently prefers Gemini as the default. Tests must construct a new registry/tool after changing fixture configuration rather than relying on stale singletons.

`CLinkTool.execute()` is wrapped by `utils/conversation_context.py::conversation_request`, which admits input, handles a user-supplied `prompt.txt`, and persists the user turn for both MCP and direct calls. The method body then validates client-specific edit scope before reading the role prompt or creating an agent. `_prepare_prompt_for_role()` uses `invocation_history(text_only=True)` on continuation and adds role instructions and file references. `server._dispatch_tool()` delegates to this same decorated path; it does not reconstruct a separate prompt. Do not implement a second history store or use agy's native continuation flags.

`BaseCLIAgent.run()` owns the working-directory and progress contexts; `_run_in_directory()` constructs the process and currently passes UTF-8 prompt bytes to `communicate()`. `_build_environment()` filters inherited variables, so agy needs the design's explicit authentication passthrough. An absent configured `working_dir` produces a fresh scratch cwd; project files remain absolute references outside it. Its command hooks do not receive the assembled user prompt. Parsers return `ParsedCLIResponse`; `ParserError` is wrapped as `CLIAgentError`. The tool turns agent errors into `ToolExecutionError` responses. Reuse the existing `ExitStack` output-file cleanup and `_kill_and_reap()` cancellation/timeout handling.

Read these anchors before implementation:

- `clink/agents/base.py`, `clink/agents/__init__.py`, `clink/constants.py`, `clink/registry.py`.
- `clink/parsers/base.py`, `clink/parsers/codex.py`, `clink/parsers/__init__.py`.
- `tools/clink.py`, especially execution, metadata, output limits, and continuation integration.
- `utils/conversation_context.py`, `utils/conversation_memory.py`, and `server._dispatch_tool()` for shared admission, history rendering, and dispatch.
- `tests/test_clink_claude_agent.py`, `tests/test_clink_gemini_agent.py`, `tests/test_clink_safe_default.py`, and `tests/test_clink_tool.py` for subprocess fakes and existing contracts.
- `tests/test_clink_environment.py` and `tests/test_conversation_ownership.py` for environment/cwd/cleanup contracts and MCP/direct invocation parity.
- `simulator_tests/base_test.py`, `simulator_tests/test_basic_conversation.py`, and `simulator_tests/__init__.py` for simulator registration.

Do not copy permissive parser fallbacks or integration tests that convert CLI errors into skips. New acceptance checks must fail when a configured agy invocation fails. Use the existing Python environment and dependency set; no dependency additions are planned.

## Task 1 — Establish the live capability contract

**Responsibility:** establish a viable read-only/opt-in-edit execution path before any shipping code. **Primary artifact:** new `capability-evidence.md` beside these documents, followed by updates to the design's permission checkpoint and this plan's verified invocation contract.

1. Identify the actual executable and record nonsecret version/help output for 1.3.3. Distinguish the native CLI from an old IDE launcher alias. Use a pre-authenticated test session and disposable project fixtures → verify: the executable reports 1.3.3 and a normal request works without PAL supplying a key or reading credentials. Build the probe environment with `BaseCLIAgent._build_environment()` and the proposed agy passthrough; do not use unrestricted shell inheritance as acceptance evidence. A local investigation harness may use the shared runner with a temporary test-only encoder/parser, without registering or enabling a production client.
2. Exercise one structured stdin message followed immediately by EOF through the shared process lifecycle in both [working-directory modes](./design.md#working-directory-and-project-file-access) → verify: one completed response arrives and the process exits; capture a sanitized output fixture with the envelope and event sequence. Also record observed CLI deadlines and error behavior; do not assume PAL's timeout changes the CLI's own deadline.
3. Discover and test an effective invocation-specific restriction within the approved architecture. Use a configured disposable project cwd and the default scratch cwd with references to a project outside it. Require the answer to report a marker present only inside the referenced file; then test direct edits and command/tool-mediated writes against sentinels under permissive saved/project settings → verify: useful reads work and denied operations leave sentinels unchanged in both modes. Prompt refusal or a successful response without the file marker is insufficient; document the actual enforcement mechanism and settings precedence.
4. Exercise explicit edit opt-in and conflicting mode/config/role arguments → verify: an opted-in request changes only the intended test fixture, the default stays restricted, and conflicting settings fail or are safely overridden according to a documented precedence rule.
5. Exercise concurrent default and edit-enabled requests → verify: each retains its own effective policy, saved configuration remains unchanged, and no shared policy state leaks between processes.
6. Verify 1.3.3's [authentication passthrough contract](./design.md#child-authentication-environment), starting with `AGY_ADC_AUTH`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_QUOTA_PROJECT`, and `GOOGLE_CLOUD_LOCATION`. Record required names and reasons, never credential values. Run personal cached-login and configured enterprise cases through the filtered environment, retaining operator selection rather than setting a provider/selector for them → verify: the personal case has no mandatory API key, configured ADC selection and routing survive, and absent/expired authentication produces a bounded actionable failure without a PAL-managed login flow. An unavailable account class is recorded for final acceptance, not claimed tested.
7. Replace the unresolved mechanism text in the design and the placeholder below with the exact interface, environment names, cwd/reference relationship, settings precedence, preflight rejection rules, and captured fixtures → verify: another contributor can reproduce both default and edit-enabled cases in both cwd modes without guessing flags or changing their real user configuration. If scratch-mode file access and A4 cannot coexist, block Task 1 and return to design before changing the setup contract.

The evidence file must distinguish observed behavior, documentary claims, and remaining uncertainties. Include a case matrix, expected/actual outcomes, OS and CLI version, authentication class, inherited variable names, actual cwd, file-reference location, settings scope, whether permissive settings were exercised, and any unverified platform/account combinations. Keep raw prompts and credentials out of committed fixtures. A successful Task 1 needs actual transport, cached-login, file-read, and permission evidence in both cwd modes in at least one controlled environment; all required account coverage must still be completed before release.

### Verified invocation contract

**Pending Task 1.** No permission switches, settings override path, or custom policy mechanism is approved by the current evidence. Task 1 must fill this section before Task 2 begins. If implementation would require a new dependency, OS sandbox, persistent settings mutation, or credential relocation, request a design revision rather than inventing an exception.

## Task 2 — Default-role request end to end

**Files with substantive behavior:** `clink/agents/base.py`, new `clink/agents/agy.py`, new `clink/parsers/agy.py`, new `tests/test_clink_agy.py`, and authentication/cwd coverage in `tests/test_clink_environment.py`. Factory entries, the verified constants, new `conf/cli_clients/agy.json`, and fixture data follow those contracts. This task depends on Task 1.

1. Add `_encode_stdin(prompt)` to `BaseCLIAgent` and call it once at `_run_in_directory()`'s `communicate()` site, inside the existing communication/cleanup handling. The default returns today's UTF-8 bytes → verify: captured bytes for Claude, Codex, and Gemini are unchanged, including multiline/Unicode prompts, and their existing agent/environment/cleanup tests pass.
2. Implement `AgyAgent` with a single user-event encoder and newline termination. Reuse `run()` and the verified default permission mechanism; keep path-restriction support false. Until Task 4 implements deliberate edits, reject `allow_edits=true` clearly → verify: a default `CLinkTool.execute()` request reaches a captured process with one correct input event, closes input, and returns the expected response without a prompt argument.
3. Implement `AgyJSONLParser` under name `agy_jsonl`. Follow the design's result cardinality, status, and content rules; do not convert incomplete output into success → verify: valid event fixtures return the final answer; malformed JSON, non-object envelopes, missing event names, missing/duplicate results, unknown/unsuccessful status, wrong response type, and whitespace-only responses raise errors.
4. Register `agy` in `INTERNAL_DEFAULTS`, `_AGENTS`, and `_PARSER_CLASSES`; create its preset with command `agy` and the default role. Declare the Task 1-verified authentication `env_passthrough` names in `CLIInternalDefaults`; do not force selector/provider values. Use the existing default-role prompt, no model pin or hardcoded project cwd, and the internal structured-input/output flags → verify: a fresh registry resolves the intended agent/parser and the tool schema includes agy while preserving the previous default. Extend `tests/test_clink_environment.py` with synthetic authentication/routing variables, absent-selector behavior, operator override precedence, and unrelated-secret exclusion; capture the actual child env and cwd for both modes.
5. Validate the complete assembled command, including any configured command-prefix arguments. Reject user/config/role options that conflict with the reserved protocol, including print/prompt aliases, extra input/output format settings, native continuation flags, and agy output-file capture. Apply the Task 1 permission precedence rules → verify: conflicting split/equals forms fail before spawning, the internal protocol flags appear exactly once, and an unrelated permitted model selection remains configurable.
6. Add controlled subprocess tests, including one fixture process that waits for stdin closure before emitting its result, plus quotes, newlines, braces, backslashes, and Unicode → verify: the complete prepared prompt round-trips as one string; no deadlock or shell interpretation occurs. Do not execute the real agy binary in default unit tests.

Focused verification: `python -m pytest tests/test_clink_agy.py tests/test_clink_environment.py tests/test_clink_claude_agent.py tests/test_clink_codex_agent.py tests/test_clink_gemini_agent.py tests/test_clink_tool.py -m "not integration"`.

## Task 3 — Failures and bounded diagnostics end to end

**Files:** `tools/clink.py`, `clink/parsers/agy.py`, `tests/test_clink_agy.py`. Add an agent change only if needed to translate the verified CLI contract; do not duplicate execution. This task depends on Task 2.

1. Enforce the complete exit/status contract through the tool response boundary → verify: nonzero exit with a successful-looking payload, zero exit with a non-success result, invalid/incomplete output, missing executable, and timeout each produce an error response. No agy recovery hook converts them into success.
2. Return selected parser metadata with distinct agy identifiers/duration and optional validated usage/model data. Preserve PAL continuation ownership and the shared runner's elapsed time → verify: no raw event payload or external field overwrites `command`, `cli_name`, `role`, `return_code`, or PAL continuation metadata.
3. Scope success/error diagnostic handling to the resolved agy runner. Keep at most 2,048 characters of stderr or an extracted error description, mark truncation, and suppress raw stdout/event dumps from error metadata → verify: long diagnostics are bounded on both paths and contain useful failure classification; existing-client metadata tests retain their behavior.
4. Preserve permission notices accompanying a valid result; do not classify failures using guessed English-message patterns → verify: a valid result with stderr returns its answer plus bounded notices, while a reported unsuccessful status still fails regardless of exit code.
5. Assert prompt absence from command argv, command metadata, and PAL command logs on success, failure, and timeout. Do not claim that model responses or child logs can never repeat input → verify: distinctive prompt markers are absent from those command channels, and no raw stream debug logging was introduced.
6. Verify failed and timed-out requests are not automatically replayed → verify: the captured process-start count is one per invocation; timeout invokes the existing kill/drain behavior without manufacturing a successful response. Caller-initiated retries remain separate invocations and may repeat already-completed effects, as specified in A7's lifecycle contract.

Focused verification: `python -m pytest tests/test_clink_agy.py tests/test_clink_tool.py tests/test_clink_parsers.py -m "not integration"`.

## Task 4 — Deliberate edits and scope rejection

**Files:** `clink/agents/agy.py`, new `tests/test_clink_agy_safety.py`, `tests/test_clink_safe_default.py`; the preset may receive mechanical verified argument entries. This task depends on Task 3 and can run alongside Task 5 using separate test files.

1. Apply the exact Task 1 default/edit mechanism through the agy adapter and existing `allow_edits` contract. Replace Task 2's deliberate-edit rejection with the verified opt-in path → verify: default and opt-in calls select the intended effective policy without changing the user's saved configuration or authentication.
2. Cover dangerous or conflicting arguments in configured and role-specific inputs using the Task 1 precedence rules → verify: default calls cannot gain automatic writes through those inputs or permissive saved settings. Do not broaden this into unrelated clients' argument validation.
3. Keep `supports_path_restrictions=false` and use the existing tool preflight rejection, preserving [A5's ordering contract](./design.md#request-and-configuration-contract) → verify: nonempty `editable_paths` fails before role-prompt loading, agent creation, or process launch; the existing no-allow-edits validation remains intact. Test the public decorated `execute()` with an actual `prompt.txt` input. Shared prompt-file admission and user-turn persistence may precede rejection; do not assert no user-file reads, bypass the wrapper, or refactor shared admission for this feature.
4. Add unit tests for policy selection and concurrent calls, then opt-in live checks through the shared runner in both cwd modes → verify: content-dependent reads succeed, file hashes remain unchanged for denied operations, deliberate edits are observable, and safe/edit calls do not share mutable policy state. Keep external project fixtures outside the scratch cwd so cleanup cannot hide a write. Mocks do not replace the live evidence.

Focused verification: `python -m pytest tests/test_clink_agy_safety.py tests/test_clink_safe_default.py -m "not integration"`. Gate new live tests on `PAL_RUN_AGY_INTEGRATION=1` in addition to the existing integration marker. Without this opt-in, report a clear skip. With it, a missing executable/session or violated contract is a failure. Run the live cases against 1.3.3 and append outcomes to `capability-evidence.md`.

## Task 5 — Roles and PAL continuation

**Files:** new `tests/test_clink_agy_continuation.py`, new `simulator_tests/test_clink_agy_validation.py`, `docs/tools/clink.md`, `README.md`; preset role entries and simulator registry import/key are mechanical. This task depends on Task 3 and can run alongside Task 4.

1. Bind planner and codereviewer to the existing shared role prompt files alongside default → verify: a fresh tool schema lists all three agy roles and each request's captured stdin includes the intended role text and current request exactly once.
2. Exercise the real `conversation_request` wrapper and `invocation_history` renderer through both `server.handle_call_tool()` and decorated `CLinkTool.execute()`, following `tests/test_conversation_ownership.py`. Fake only the external process boundary; do not inject reconstructed prompts or bypass the wrapper → verify: two requests persist four raw turns once, the second request's stdin contains preceding user/assistant content and the current input exactly once, and each request starts a new agy process without native continuation flags. Retain the MCP-path case while treating decorated direct execution as the same shared lifecycle.
3. Add a simulator named `clink_agy_validation` using `BaseSimulatorTest`, registered in `simulator_tests/__init__.py` → verify: it appears in `communication_simulator_test.py --list-tests` and performs an initial role-based clink request followed by a history-dependent follow-up against a disposable fixture.
4. Add live role/authentication cases using the existing `integration` marker and `PAL_RUN_AGY_INTEGRATION=1` opt-in, through the actual filtered environment and cwd handling. Do not require a Gemini API key or copy existing tests that skip on an actual CLI failure → verify: each configured personal/enterprise case produces an answer, codereviewer can report a marker read only from a referenced file in each cwd mode, and missing/expired login errors remain failures in a run that declared the environment ready.
5. Update the clink guide and README with setup for 1.3.3, existing-login use and passthrough names, both cwd modes and project settings, role/continuation examples, the verified edit policy, unsupported path restrictions, known upstream consumer-wrapper restriction, and failure guidance → verify: documented parameter names match `get_input_schema()` and the examples match captured/live accepted requests. Explain that a successful answer with a denial notice does not establish project-file access. Do not claim older-version or untested-platform support.

Focused verification: `python -m pytest tests/test_clink_agy_continuation.py tests/test_clink_tool.py tests/test_conversation_ownership.py tests/test_conversation_continuation_integration.py -m "not integration"`; then `python communication_simulator_test.py --individual clink_agy_validation` in the configured live environment.

## Task 6 — Final verification and documentation

This task depends on Tasks 1–5. It is a verification step, not permission to conceal missing evidence or silently fix unrelated findings.

1. Run `$kk:test` across the completed feature, including all clink unit regressions and semantic error cases → verify: the required tests pass without new skip-based evasions or relaxed assertions.
2. Run `./code_quality_checks.sh`, `./run_integration_tests.sh`, and the repository's quick simulator mode in the prepared environment. Run `clink_agy_validation` individually as well → verify: reports identify command, environment, outcome, and every skip. The quick suite does not by itself cover the new agy path.
3. Complete the personal/enterprise live matrix for 1.3.3, recording platform-specific evidence, the filtered environment, both cwd modes, and both default/edit paths → verify: A1–A8 in the design have traceable evidence, including actual referenced-file reads. Unavailable account access or a policy failure prevents claiming full acceptance.
4. Run `$kk:document` to reconcile the guide, README, design, capability evidence, implementation plan, and task states → verify: they describe the final behavior and retain all unresolved/deferred items explicitly.
5. Run `$kk:review-code` with Python as the project language and `$kk:review-spec` against all feature documents → verify: independent findings are addressed or recorded durably with reason and next action; blockers remain visible and are not marked done.
6. Inspect the final diff and packaging of the agy manifest/role files, and restart the local MCP session before live validation of updated code → verify: the installed client is discoverable, existing clients remain registered, and `.capy/knowledge.db` is not included in a commit.

The existing integration helper is oriented around provider credentials. If it cannot exercise API-key-free agy, run the dedicated agy integration cases directly as well and record the helper limitation; do not add a mandatory key to satisfy the helper.

Dedicated live command: `PAL_RUN_AGY_INTEGRATION=1 python -m pytest tests/test_clink_agy_safety.py tests/test_clink_agy_continuation.py -m integration`. Run it in each prepared authentication environment and record the account class externally in the evidence; tests must not inspect tokens or infer account identity. The opt-in controls test execution only and is not a runtime PAL authentication setting.

## Acceptance evidence and deferred work

Maintain a compact table in `capability-evidence.md` mapping A1–A8 to test names or live cases, version/platform/account class, result, and remaining gaps. Unit tests show behavior at controlled boundaries; they do not prove CLI permission enforcement or account availability.

Preserve the deferred Claude system-prompt command exposure in [verification.md](./verification.md#open-decisions-and-follow-up-work). The previously recorded general cancellation/output-file cleanup gaps were fixed in the shared runner before this review; retain its tests, not a duplicate repair task. If scope expands, update the design and split a new task rather than turning an M-sized slice into an unbounded refactor.

No agy implementation or live verification has occurred during design or this revision. The [review record](./review-design.md) distinguishes documentation checks from runtime evidence. The design's permission and cwd assumptions remain unresolved until Task 1 supplies actual evidence.

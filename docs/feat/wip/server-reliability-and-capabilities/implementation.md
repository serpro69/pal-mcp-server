# Implementation: server reliability and capabilities

> Status: proposed; no implementation started
> Created: 2026-10-10
> Design: [design.md](design.md)
> Tracking: [tasks.md](tasks.md)
> Review resolutions: [review.md](review.md#revalidation-and-resolution)

## Working rules and rollout

Implement these features within the current repository using the contracts in
[design.md](design.md) and the acceptance checks below. Each change is new
implementation work, with tests derived from its observable requirements.

Each numbered section is a vertical, independently verifiable change. Temporary
migration paths are explicit and have a removal task. Tasks marked M may include
mechanical registrations/config entries beyond their core files; if implementation
uncovers more than five substantive files or multiple independent behaviors, split
the task before starting it. Do not hide large work under a small size label.

Tasks 1–9 establish maintenance and correctness; 10–23 cover request isolation and
provider execution; 24–29 cover selection/progress; 30–35 add diagnostics/tests;
36–38 are optional service integrations; 39–41 cover measured cache work.
Task 42 is the final acceptance gate. Follow dependency edges, not phase numbering
alone. Independent fixes can ship before optional services or experiments.

Within every task, complete the implementation action and its paired verification
before marking it done. Test filenames marked new, and named tests not present in
the checkout, are proposed additions. Use the existing virtualenv/pip-tools workflow.
Verify dependency APIs with kk:dependency-handling before implementation. Tests
must assert observable behavior, not merely mirror helpers.

## Runtime and API compatibility

- Retain MCP 1.x interfaces: decorators, request_context, CallToolResult and resource
  types. Select the security floor through Task 1's fresh dependency validation.
- Install a clean environment from both generated lock files for verification;
  an existing developer environment may contain different versions.
- Characterize provider wire behavior before changing execution style.
  OpenAI Chat Completions and Responses have different token/usage fields.
- JSONL adds an audit format; it does not turn in-memory TTL storage into restart
  recovery. Keep continuation errors honest after a restart.
- Preserve the file-annotation compatibility contract in design.md, including its
  five input-to-render paths. Annotation implementation remains separate work.
- User-facing changes requiring release notes: artifact root/names, child cwd/env,
  model provenance fields, Python floor, logs path, unused schema fields, transcript
  opt-in, effective output limits, turn-pair admission, full model-ID/legacy-option
  parsing and optional service controls.
- No task implicitly activates a service, uses paid APIs, creates an external PR,
  changes publishing credentials or commits the capy database.

## Gate handling

Draft defaults are workspace containment, transcript opt-in and explicit Jules
plan/PR choices. Review these as concrete design decisions, not as already deployed
behavior. Task 1 records any requirement to retain Python 3.9 before changing its
metadata. Tasks 36/38 record unsupported protocols/actions with API-contract evidence and
next steps. Tasks 39–41 record measurements and any rejected optimization.

An optional task that cannot complete its live contract check remains pending for
release, even when its mocked tests pass. A research gate may close with a documented
no-change decision only where the task explicitly permits that; do not label
unimplemented features done. Never relax an acceptance assertion to conceal a regression.

## Execution slices

<a id="task-1"></a>

## Task 1: Validate a patched MCP 1.x lock set

- **Size:** M
- **Depends on:** —
- **Design:** [d1-runtime-and-packaging](design.md#d1-runtime-and-packaging)

**Files / responsibility:** pyproject.toml; requirements.txt; requirements-dev.txt; .github/workflows/test.yml.

**Implementation step:** Confirm the proposed Python 3.10 floor against the existing 3.10–3.12 matrix and actual supported consumers; correct package metadata if accepted. Record any retained 3.9 requirement as a blocking compatibility decision. Check current MCP advisories and select a compatible patched 1.x release; retain <2.0.0 and pip-compile both locks with --strip-extras --no-header. Create a fresh validation environment from both lock files.

**Step → verify:** Run ./code_quality_checks.sh in the fresh environment, the existing CI matrix and the lock-freshness recompilation; record installed versions. Distinguish the pre-existing AsyncMock warnings from new failures.

**Contract / gate:** Risk-first. Validate in a fresh environment installed from the generated locks; an existing developer environment is not evidence for those pins. No general dependency upgrade or toolchain switch.

<a id="task-2"></a>

## Task 2: Refresh and verify the existing container

- **Size:** S
- **Depends on:** Task 1
- **Design:** [d1-runtime-and-packaging](design.md#d1-runtime-and-packaging)

**Files / responsibility:** Dockerfile; docker/scripts/healthcheck.py; existing docker workflows/tests.

**Implementation step:** Rebuild the current pip-based image with a verified supported base and required distribution fixes; preserve non-root execution and local image metadata. Record scanner findings by installed package and applicability, with an actionable follow-up for each unresolved finding. Report only the security properties established by the current build and scan.

**Step → verify:** Run existing test_docker_implementation/security/healthcheck tests, build the image, run its health check, and save the current image scan result. No image push.

**Contract / gate:** Use kk:dependency-handling before selecting a new image tag; keep the existing release workflow. Retain scanner evidence and actionable follow-up for unresolved findings.

<a id="task-3"></a>

## Task 3: Repair canonical license and package notices

- **Size:** S
- **Depends on:** —
- **Design:** [d1-runtime-and-packaging](design.md#d1-runtime-and-packaging)

**Files / responsibility:** LICENSE; NOTICE (new); pyproject.toml; package manifest if required.

**Implementation step:** Compare LICENSE with the canonical Apache 2.0 text and restore it without altering terms; ensure NOTICE retains accurate existing project notices. Declare license metadata using the installed build backend's verified supported fields and include the text/notice in source and wheel distributions.

**Step → verify:** Build distributions locally and inspect their metadata and archive contents; compare LICENSE to the canonical Apache 2.0 text. Do not publish.

**Contract / gate:** Preserve the project's existing legal notices and ownership metadata. This task corrects license text and distribution contents; it does not change ownership.

<a id="task-4"></a>

## Task 4: Contain generated chat artifacts

- **Size:** M
- **Depends on:** —
- **Design:** [d2-file-and-cli-boundaries](design.md#d2-file-and-cli-boundaries)

**Files / responsibility:** tools/chat.py; tests/test_chat_simple.py; docs/tools/chat.md; .env.example.

**Implementation step:** Validate operator PAL_WORKSPACE_ROOT and supplied working directory before generation; define cwd fallback in configuration docs. Create unique per-call artifacts inside a validated sandbox with exclusive/no-follow handling; reject symlink escapes at root/sandbox/file boundaries and surface policy/write failures without inline fallback instructions.

**Step → verify:** Add and run artifact tests for traversal, outside-root directories, symlinked sandbox and file, write failure, valid nested directory and two concurrent results whose paths/content stay distinct.

**Contract / gate:** Keep read-only chat file access independent from the artifact write root. Validate the root, artifact directory and destination as separate boundaries; names must be unique per call.

<a id="task-5"></a>

## Task 5: Bound file reads and preserve the local deny policy

- **Size:** M
- **Depends on:** —
- **Design:** [d2-file-and-cli-boundaries](design.md#d2-file-and-cli-boundaries)

**Files / responsibility:** utils/file_utils.py; utils/security_config.py; tests/test_path_traversal_security.py; tests/test_path_policy_pseudofs.py (new).

**Implementation step:** Extend the existing path policy to reject pseudofilesystem/system directories, including /proc, /sys, /dev and /run, while retaining sensitive-home protections; validate paths before token-estimation stat calls. Read at most max_size+1 bytes before decoding and distinguish true overflow; preserve formatting for ordinary successful reads.

**Step → verify:** Run path traversal and new pseudofilesystem tests with spoofed stat sizes, multibyte UTF-8, /proc-style entries, symlinks to denied paths, legitimate home code and existing macOS path cases.

**Contract / gate:** Preserve DANGEROUS_HOME_SUBDIRECTORIES and existing legitimate-path behavior. Mock dangerous filesystem entries; never read real credential files in tests.

<a id="task-6"></a>

## Task 6: Isolate clink environment and working directory

- **Size:** M
- **Depends on:** —
- **Design:** [d2-file-and-cli-boundaries](design.md#d2-file-and-cli-boundaries)

**Files / responsibility:** clink/agents/base.py; clink/constants.py; clink/models.py; clink/registry.py; clink configs/tests.

**Implementation step:** Add shared environment names, vendor-specific passthrough and explicit config overrides. Preserve auth/runtime variables including Windows/locale/TLS requirements. Run in configured working_dir or a temporary per-call directory. Forward allow_edits and editable_paths through every wrapper unchanged, and retain system-prompt ContextVar cleanup.

**Step → verify:** Run all test_clink_* plus new environment/cwd tests: unrelated vendor keys absent, own auth present, override precedence, scratch cleanup on success/error/cancel, safe/edit behavior unchanged. Exercise a real configured CLI with scratch cwd before release.

**Contract / gate:** Model/registry declarations are mechanical; the behavior lives in the runner. A scratch directory is not a filesystem sandbox. Verify the configured CLI's repository requirements.

<a id="task-7"></a>

## Task 7: Report Gemini CLI unsupported-client failures

- **Size:** S
- **Depends on:** Task 6
- **Design:** [d2-file-and-cli-boundaries](design.md#d2-file-and-cli-boundaries)

**Files / responsibility:** clink/parsers/gemini.py; clink/agents/gemini.py; corresponding parser/agent tests.

**Implementation step:** Recognize unsupported-client diagnostics in failure stderr, non-JSON output and empty/error JSON paths; integrate agent error recovery. Keep successful answers mentioning the marker intact, and return vendor-documented remediation without unverified account/date claims.

**Step → verify:** Run test_clink_gemini_parser.py and test_clink_gemini_agent.py with each failure path and successful text quoting the marker.

**Contract / gate:** Diagnose an unsuccessful CLI invocation from its error channel or error envelope. Successful answer text alone must never trigger unsupported-client handling.

<a id="task-8"></a>

## Task 8: Make conversation storage failures explicit

- **Size:** M
- **Depends on:** —
- **Design:** [d3-conversation-ownership-and-truthful-results](design.md#d3-conversation-ownership-and-truthful-results)

**Files / responsibility:** utils/conversation_memory.py; server.py; tools/simple/base.py; tools/workflow/workflow_mixin.py; memory tests.

**Implementation step:** Introduce typed storage/decode errors distinct from absent/expired/capped threads, preserving original causes. Fail required continuation reads/input writes before a provider call. Propagate unexpected post-response persistence failures as visible result warnings while retaining the generated response; audit create/get/add/chain callers and false return values. Keep this slice at the existing storage/caller boundaries; Task 9 owns invocation-level pair admission together with single-owner input recording.

**Step → verify:** Run conversation-memory and continuation tests covering missing, corrupt, read-fault and write-fault storage, no provider invocation on input failure, and answer-plus-warning on final write failure. Verify that the storage API's existing exhausted-message-cap outcome is distinct from an outage. Pair-admission and cap-2 invocation-success tests belong to Task 9, followed by concurrent admission verification in Task 11.

**Contract / gate:** Preserve genuine programming errors and exception causes. Only expected storage/decoding faults enter the typed memory-error path. Do not add separate pair-admission checks at the legacy input writers; Task 9 first gives each invocation one input owner.

<a id="task-9"></a>

## Task 9: Record turns once and rebuild complete continuation context

- **Size:** M
- **Depends on:** Task 8
- **Design:** [d3-conversation-ownership-and-truthful-results](design.md#d3-conversation-ownership-and-truthful-results)

**Files / responsibility:** server.py; tools/simple/base.py; tools/workflow/workflow_mixin.py; utils/context_builder.py and model_resolution.py (new if extracted); continuation tests.

**Implementation step:** Give server-built history a trusted internal marker/context; separate it from caller-supplied arguments and retain a correct direct-tool path. Establish one input-persistence owner, then have that owner check for two free message slots exactly once before saving input or starting provider/CLI work. Record completed admission in trusted context so nested execution and provider retries do not repeat it; use Task 8's typed outcomes. Extract context/resolution helpers without changing explicit model semantics. Create thread/input before generation and record workflow step and raw reply exactly once, including clink's first input and actual local-only results with null provider provenance. Re-read after adding the current turn before rendering its files. Preserve the raw original consensus proposal independently of generic history; participant prompts must omit earlier verdicts and intervening summaries. Compute remaining_turns as free message slots after writes, and continuation wording as floor(free_slots / 2) complete exchanges.

**Step → verify:** Add MCP-boundary tests for 4-turn growth, each user/reply appearing once, first-turn provider failure, raw versus artifact-formatted reply, workflow steps, current-turn file/directory attachment, prompt-file input, oversized original input and cross-model/cross-tool continuation. Through both MCP and direct-tool paths, cover cap-1 rejection before input append/work, cap-2 success with one input and one reply, odd caps including one, local-only replies, a previous saved-input/provider failure and a retry within an already admitted invocation. Caller JSON cannot bypass admission. Capture a second consensus participant request: the original proposal and explicit attachments must be present, with earlier-verdict and caller-summary sentinels absent even though storage retains them. Verify remaining-slot/exchange counts after success and failure, with no continuation invitation below two slots.

**Contract / gate:** Pair admission ships with single-owner recording in this task; Task 11 later provides same-thread atomicity. Task 8 does not need Task 9's ownership mechanism to complete its storage-error slice. Re-record affected live cassettes only in an explicitly configured validation run; never edit recorded request bodies to manufacture a pass.

<a id="task-10"></a>

## Task 10: Isolate workflow instances and persist their configuration

- **Size:** M
- **Depends on:** Task 8, Task 9
- **Design:** [d3-conversation-ownership-and-truthful-results](design.md#d3-conversation-ownership-and-truthful-results)

**Files / responsibility:** server.py; tools/workflow/workflow_mixin.py; tools/workflow/base.py; tools/consensus.py; mechanical state declarations in workflow tools.

**Implementation step:** Dispatch each invocation to a fresh tool while keeping schema prototypes; reset reused direct-call objects and restore work_history plus tool_state for the correct tool/thread. Declare/persist review_config, analysis_config, git_config, refactor_config, trace_config, stored_request_params and planner branches; implement consensus original proposal, roster and responses explicitly, keeping stored audit history out of participant prompts.

**Step → verify:** Run new workflow state/concurrency tests and existing planner, codereview, precommit, thinkdeep and consensus continuation suites. Interleave two requests at an await barrier and resume each on a newly constructed object; test older metadata without tool_state and every persisted configuration. Repeat Task 9's consensus required/forbidden-content capture on newly constructed participant tool instances.

**Contract / gate:** State declarations across workflow tools are mechanical; per-call construction and correct restoration are the substantive change. Preserve each declared configuration and planner/consensus state.

<a id="task-11"></a>

## Task 11: Serialize concurrent updates to one conversation

- **Size:** M
- **Depends on:** Task 10
- **Design:** [d3-conversation-ownership-and-truthful-results](design.md#d3-conversation-ownership-and-truthful-results)

**Files / responsibility:** server.py; utils/conversation_coordination.py (new); tests/test_conversation_concurrency.py (new).

**Implementation step:** Add a per-thread async critical section covering capacity admission, reconstruction and result persistence, with bounded/ref-counted lock lifetime. Check Task 9's two-slot admission inside the coordinator so another same-ID call cannot consume the reply slot. Independent thread IDs do not share the same lock. Define cancellation and failed-call cleanup without placeholder replies, and document that direct tool-object APIs do not acquire the MCP boundary's lock unless explicitly using the same coordinator.

**Step → verify:** Force two same-ID requests to overlap; verify ordered inputs, no lost assistant turn and consistent workflow state. With only one input/reply pair available, exactly one request may run and the other must receive a limit result without saving input. Verify different IDs overlap, cancellation after input save leaves one real turn and frees the lock, unused reply capacity is released, and idle lock entries are reclaimed.

**Contract / gate:** Complete same-thread coordination before enabling more provider concurrency. Do not hold one global lock while awaiting a model.

<a id="task-12"></a>

## Task 12: Make workflow model provenance truthful

- **Size:** M
- **Depends on:** Task 10
- **Design:** [d3-conversation-ownership-and-truthful-results](design.md#d3-conversation-ownership-and-truthful-results)

**Files / responsibility:** tools/workflow/base.py; tools/workflow/workflow_mixin.py; tests/test_workflow_metadata.py and concurrent/skip-branch tests.

**Implementation step:** Track actual attempted model/provider calls in per-request state; local-only and intermediate responses report null rather than an inferred configured model. Change completion wording to distinguish caller_findings from external analysis, document compatibility for final_analysis consumers, and consolidate the duplicate completion handler only as part of this change.

**Step → verify:** Test intermediate, internal-only, skipped, failed-before-provider, attempted-provider-error and successful expert paths; interleave calls using different providers and verify their result and persisted metadata.

**Contract / gate:** Do not invent confidence when the caller supplied none or delete unrelated dead code. Provider identity reflects an actual attempted call, not configured defaults.

<a id="task-13"></a>

## Task 13: Report partial and failed consensus honestly

- **Size:** S
- **Depends on:** Task 10, Task 12
- **Design:** [d3-conversation-ownership-and-truthful-results](design.md#d3-conversation-ownership-and-truthful-results)

**Files / responsibility:** tools/consensus.py; tests/test_consensus_degradation.py (new); docs/tools/consensus.md.

**Implementation step:** Count successful consultations separately from requested/failed models, including represented providers when available. Use the same summary builder for both completion paths, expose participant errors and mark partial or zero-success outcomes without claiming full/high-confidence consensus.

**Step → verify:** Test all-success, one-failure, lost-provider and zero-success rosters across a multi-step continuation; verify final counts, statuses and instructions match actual participants.

**Contract / gate:** Keep the original proposal and successful responses available even when another participant fails. Both completion paths must produce the same degraded-result accounting.

<a id="task-14"></a>

## Task 14: Make chat generation yield through a migration adapter

- **Size:** M
- **Depends on:** Task 10, Task 11
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** utils/provider_execution.py (temporary/new); tools/simple/base.py; providers/openai_compatible.py; providers/azure_openai.py; providers/gemini.py; tests/test_async_concurrency.py (new) and provider initialization tests. Verify DIAL's existing initialization lock/ownership without unrelated rewrites.

**Implementation step:** Before enabling offload, guard shared legacy-provider lazy initialization and audit generation-time mutable caches. Publish one initialized client per owner using short initialization locks; preserve DIAL's deployment-client protection. Replace OpenAI/Azure proxy-environment suppression, including fallback construction, with explicit transport configuration that preserves effective proxy/TLS behavior without process-global changes. Then introduce a temporary adapter that awaits coroutine providers and offloads legacy synchronous generation to asyncio.to_thread. Route simple-tool primary and recovery/retry calls through it; update only affected mocks to the active contract.

**Step → verify:** Use actual provider methods with barrier-controlled fake SDK constructors/transports to prove concurrent cold starts retain one client per owner, failed initialization can be retried, and both calls complete. During construction and fallback, assert synthetic proxy/TLS environment values remain unchanged for another provider and a CLI child. Verify explicit transport policy, then use delayed generation to prove network waits overlap and replies keep their metadata. Run chat, retry, UTF-8 and continuation suites without new unawaited-coroutine warnings.

**Contract / gate:** The cold-start and environment checks must pass before routing calls through worker threads. Fresh tools and per-conversation locks do not protect provider singletons. Never serialize generation under an initialization lock. The adapter is removed in Task 19; cancellation of its await does not guarantee cancellation of the worker's network request. Preserve the explicit transport policy through Tasks 16–19.

<a id="task-15"></a>

## Task 15: Make expert and consensus generation yield

- **Size:** M
- **Depends on:** Task 13, Task 14
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** tools/workflow/workflow_mixin.py; tools/consensus.py; workflow/consensus tests.

**Implementation step:** Route expert generation and consensus consultation through the temporary invocation adapter; preserve per-model error results and request-scoped state. Correct affected AsyncMock use and failure-path assertions, including the pre-existing consensus/workflow UTF-8 warnings.

**Step → verify:** Run consensus, workflow UTF-8, workflow metadata and interleaving tests; demonstrate another request advances during expert/consensus waits and cancellation is not retried as a provider error.

**Contract / gate:** Preserve participant result semantics and the degraded-accounting contract in Task 13. Use mocks that match the active sync/async boundary.

<a id="task-16"></a>

## Task 16: Migrate OpenAI-compatible and Azure wire calls

- **Size:** M
- **Depends on:** Task 15
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** providers/base.py; providers/openai_compatible.py; providers/azure_openai.py; OpenAI/OpenRouter/custom/xAI/Azure tests.

**Implementation step:** Introduce the shared native build/call/parse/retry path and native async OpenAI/Azure clients while preserving request payloads and custom HTTP settings. Make Azure overrides await the shared path; characterize inherited OpenRouter/custom/xAI behavior. Keep DIAL's separate legacy generation path functioning until Task 18.

**Step → verify:** Run all affected provider/routing/temperature/Responses tests, inspect fake-transport requests for exact fields and compare them with pre-migration characterization. Exercise sync-DIAL compatibility with the migrated superclass.

**Contract / gate:** Follow kk:dependency-handling using locked SDKs. This family migration does not change catalogs or provider metadata; DIAL remains functional until its own migration.

<a id="task-17"></a>

## Task 17: Migrate Gemini generation to the async SDK

- **Size:** M
- **Depends on:** Task 15
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** providers/gemini.py; providers/base.py; Gemini tests.

**Implementation step:** Use the verified GenAI async generation surface and the shared retry contract; preserve image conversion, safety/usage parsing and timeouts. Add an awaited async cleanup hook without creating a previously unused client. Keep budget/level behavior unchanged until Task 21.

**Step → verify:** Run Gemini/provider UTF-8/retry/image tests with a delayed fake SDK and cancellation; assert retries yield and the client closes exactly once.

**Contract / gate:** Native SDK capability is verified separately from per-model behavior. Cleanup must not allocate a client that was never used.

<a id="task-18"></a>

## Task 18: Migrate DIAL generation and shared transport ownership

- **Size:** M
- **Depends on:** Task 16
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** providers/dial.py; providers/base.py; tests/test_dial_provider.py; close tests.

**Implementation step:** Move deployment-specific calls and event hooks to async forms while retaining DIAL auth/header stripping, URL mapping and shared connection-pool ownership. Close deployment wrappers/shared clients without double-closing, leaking coroutines or constructing extra clients.

**Step → verify:** Run DIAL wire/header/routing tests and async-close tests in a live event loop; repeat close and cancellation, asserting the shared pool closes once and no background cleanup remains.

**Contract / gate:** Await event hooks and close shared pools once. Wrappers must distinguish owned from borrowed transports, including cancellation and repeated shutdown. Characterize DIAL payloads with and without temperature support, explicitly recording the existing output-limit omission as a baseline defect for Task 20, not a permanent compatibility requirement. Task 20 owns its correction and deployment-specific output-field verification.

<a id="task-19"></a>

## Task 19: Finish the async contract and deterministic shutdown

- **Size:** M
- **Depends on:** Task 16, Task 17, Task 18
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** providers/base.py; server.py; utils/provider_execution.py; generation call sites and tests.

**Implementation step:** Make generate_content an async-only provider contract after every backend is migrated; replace adapter calls with direct awaits and remove only that newly orphaned adapter/legacy retry path. Await provider cleanup from server lifecycle before the event loop exits. Preserve errors and cancellation without a fire-and-forget success path.

**Step → verify:** Search all generate_content call sites and run the provider contract/concurrency suites plus the full unit suite; stop a running fake stdio server and assert all allocated clients close without pending-task/loop-closed warnings.

**Contract / gate:** No permanently mixed sync/async contract. Attribute expected test changes to actual endpoint behavior.

<a id="task-20"></a>

## Task 20: Enforce output limits and normalize endpoint usage

- **Size:** M
- **Depends on:** Task 19
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** utils/model_context.py for the shared request budget; tools/simple/base.py, tools/workflow/workflow_mixin.py and tools/consensus.py for budget propagation/admission; providers/openai_compatible.py and providers/dial.py for endpoint fields/usage; wire-contract tests covering all concrete providers, including Gemini and Azure overrides. Keep budget policy centralized and per-path forwarding mechanical; split before implementation if substantive changes exceed the working-rule size limit.

**Implementation step:** First calculate effective output O as the minimum of the verified registry ceiling, the existing response reservation and any supplied positive caller limit; reserve and transmit the same O. Derive input capacity as context capacity minus O and check the full assembled request, including history, files, current input, system instructions, headers and conservative image/token overhead, before provider I/O. Rebuild with explicit omission markers or reject using the existing policy when it cannot fit. Apply the same budget to primary, recovery, expert and each consensus participant; recompute when model/payload changes, without charging blinded participants for excluded thread history. Use Responses max_output_tokens and the appropriate Chat Completions field; correct DIAL's separate builder so verified deployment output limits are independent of temperature support. If a deployment's limit field cannot be verified or represented, return an explicit capability/configuration error before I/O rather than omit O. Parse usage using each endpoint's schema, preserving absent fields as unknown. No new public limit parameter is introduced.

**Step → verify:** Create tests/test_max_output_tokens_is_sent.py and budget/endpoint usage fixtures: all four paths transmit exactly their response reservation in the correct field. Cover ceilings larger/smaller than the default reservation, lower caller limits, invalid/nonpositive metadata, near-full history, system/image overhead, model-changing retries and heterogeneous consensus models. A synthetic 400,000-token context/272,000-token output ceiling uses the default 80,000 reservation, not 272,000, and admits at most 320,000 estimated input tokens including overhead. Test boundary acceptance/rejection without depending on any vendor's oversize behavior. Nonzero Responses input usage survives; malformed and absent usage do not invent counts.

**Provider wire matrix:** Separately use actual serializers with fake SDK transports for OpenAI Chat Completions/Responses, Azure deployment routing, OpenRouter's supported endpoint families, custom/xAI inherited paths, Gemini and DIAL deployments with/without temperature support (including O3/O4-mini fixtures). Assert the chosen O reaches the verified endpoint field and routing/authentication remain intact. Unsupported-field fixtures must fail before I/O. Four caller tests with one fake provider are not sufficient; a full model-by-caller product is unnecessary.

**Contract / gate:** The total input/output budget invariant ships in Task 20; it cannot wait for Task 24. Keep OpenRouter store omission and custom unsupported-temperature behavior. Unknown usage differs from a valid zero; missing/invalid capacity must never become an unlimited request. Endpoint-specific overflow behavior and estimator uncertainty remain explicit.

<a id="task-21"></a>

## Task 21: Send valid Gemini thinking controls

- **Size:** M
- **Depends on:** Task 17
- **Design:** [d4-async-provider-lifecycle](design.md#d4-async-provider-lifecycle)

**Files / responsibility:** providers/gemini.py; providers/shared/model_capabilities.py; conf/gemini_models.json; Gemini thinking tests.

**Implementation step:** Select budget versus level from verified model generation/capability metadata; add supported_thinking_levels only where verified. Map PAL minimal/low/medium/high/max to accepted levels, clamping down then up when needed and surfacing invalid/empty capability configuration.

**Step → verify:** Run existing thinking tests plus new wire cases for Gemini 2.x budgets and 3.x levels, aliases, low/high-only support, unknown modes and malformed supported-level data.

**Contract / gate:** The SDK having an enum does not prove a particular model accepts every member. Capability declarations need per-model verification.

<a id="task-22"></a>

## Task 22: Centralize provider configuration and restriction discovery

- **Size:** M
- **Depends on:** Task 19
- **Design:** [d5-providers-and-model-policy](design.md#d5-providers-and-model-policy)

**Files / responsibility:** providers/registry.py and server.py for shared identity resolution, request parsing and factories; providers/custom.py for exact tag/alias routing; utils/model_restrictions.py; tools/listmodels.py; mechanical metadata declarations in providers/base.py and provider classes. Update tests/test_parse_model_option.py and add dispatcher-to-wire identity tests.

**Implementation step:** Declare provider identity/env/placeholder/required/optional/restriction metadata and factories; derive configuration/help/listmodels/test isolation from that source. Centralize request-to-wire identity, including server.parse_model_option and custom routing: resolve the complete canonical ID/alias using the full catalog and existing provider priority before considering legacy options or applying visibility/restrictions. Only an unresolved complete ID with a terminal for/against/neutral suffix and a resolvable preceding ID may become a legacy option. A known but hidden/denied full ID never falls back to option parsing; other unknown suffixes produce an error, not base-model substitution. Keep registered custom tags, DIAL :0 IDs and OpenRouter suffixes intact. Apply allowlists to the resolved provider/canonical identity for every provider, including CUSTOM and Azure, keeping custom API keys optional. Document exact-tag registration/explicit-alias migration for formerly stripped tags and retain separate legacy option values without inventing new tool behavior.

**Step → verify:** Add provider-from-env, metadata and characterization tests; enumerate all registered classes and prove discovery and actual calls agree under aliases/canonical names and restrictions. Add MCP-dispatch captures proving registry identity, policy identity and wire model agree for registered custom tags, DIAL :0 IDs, OpenRouter suffixes, aliases, supported legacy options and whitespace/case handling. Verify a complete registered ID ending in :for wins over option parsing, including when denied; unknown tags must not call the base model. Update parser tests that currently expect arbitrary tag stripping. Ensure secrets never appear in help/errors.

**Contract / gate:** Preserve intentional routing priority; add OpenCode Go separately in Task 36. Provider metadata drives discovery, restrictions, setup diagnostics and test isolation. Request-to-wire identity is established here before Tasks 25/26 add deny/preference rules. Identity resolution uses the full catalog; eligibility is a later decision, not a reason to reinterpret the request. No new provider-prefix syntax is added to tool requests.

<a id="task-23"></a>

## Task 23: Report missing and broken provider configuration correctly

- **Size:** M
- **Depends on:** Task 22
- **Design:** [d5-providers-and-model-policy](design.md#d5-providers-and-model-policy)

**Files / responsibility:** server.py; providers/registries/base.py; providers/registry.py; custom/Azure factory error paths; tests.

**Implementation step:** Keep the server discoverable with no configured providers; return explicit MCP tool-error results only for calls that require one. Raise file-naming typed errors for malformed explicitly selected/shipped registries, distinguishing absent optional, empty optional, JSON/schema/alias and I/O failures.

**Step → verify:** Run MCP error and new startup tests: tools/list, version, listmodels, planner and clink remain usable as applicable without provider keys; model calls return isError; corrupt configured files cannot silently remove a provider.

**Contract / gate:** Use MCP 1.x decorators/results. Avoid circular-import error plumbing where a dedicated exception module suffices.

<a id="task-24"></a>

## Task 24: Unify file budgets and report actual context coverage

- **Size:** M
- **Depends on:** Task 5, Task 9, Task 20
- **Design:** [d6-budgets-progress-and-tool-surface](design.md#d6-budgets-progress-and-tool-surface)

**Files / responsibility:** utils/model_context.py; utils/file_utils.py; tools/shared/base_tool.py; tools/workflow/workflow_mixin.py; file-budget tests.

**Implementation step:** Reuse Task 20's effective output reservation and total input capacity when computing one effective file budget; pass remaining-history limits through gate and embedding without recalculating an independent output allowance. Exclude history withheld from consensus participants and deduplicate only against content present in that participant's request. Report binding budget, largest files and overage while preserving directory/alias/dedup semantics. Track files actually embedded now and actually included from history separately; account for rendered headers and the pending per-file descriptions feature.

**Step → verify:** Create tests/test_file_size_gate.py and tests/test_workflow_file_context_counts.py; compare gate/embed budgets for fresh/long-history requests and verify missing, denied, duplicated or truncated files never inflate history counts. Retain Task 20's total-request invariant and verify a blinded participant's attachments are neither omitted nor charged against generic history it never receives.

**Contract / gate:** The file-annotation compatibility contract in design.md is a coordination dependency; implementing annotations is outside this roadmap.

<a id="task-25"></a>

## Task 25: Add a global model deny policy

- **Size:** M
- **Depends on:** Task 22
- **Design:** [d5-providers-and-model-policy](design.md#d5-providers-and-model-policy)

**Files / responsibility:** utils/model_restrictions.py; providers/registry.py; tests/test_disabled_models.py (new); .env.example.

**Implementation step:** Parse DISABLED_MODELS with recognized provider scopes while preserving colon-bearing model IDs. Remove at most a recognized provider prefix, then pass the complete remaining ID through Task 22's canonical/alias lookup without invocation-only legacy-option parsing. Enforce canonical/alias-equivalent denies across explicit calls, discovery, summary and fallback after provider allowlists; warn on unknown configured entries without exposing secrets.

**Step → verify:** Run restrictions, alias-target, auto-mode and new disabled-model tests, including alias cycles/unknowns, scoped duplicate names and local model tags. Extend Task 22's dispatcher captures with scoped/unscoped denies for tagged custom IDs and DIAL :0 IDs: denied calls do not reach a transport, aliases cannot bypass a deny, and allowed full IDs keep their suffix on the wire.

**Contract / gate:** A canonical alias cannot bypass a deny. Parse provider scope only when the prefix is a recognized provider name.

<a id="task-26"></a>

## Task 26: Add hidden catalog entries and ordered auto preferences

- **Size:** M
- **Depends on:** Task 25
- **Design:** [d5-providers-and-model-policy](design.md#d5-providers-and-model-policy)

**Files / responsibility:** providers/shared/model_capabilities.py; providers/registries/base.py; providers/base.py; providers/registry.py; utils/model_restrictions.py.

**Implementation step:** Add enabled_by_default with a backward-compatible true default; hide disabled entries/aliases from discovery while permitting exact canonical calls that pass policy. Implement ordered PREFERRED_MODELS for auto/fallback only using Task 22's complete canonical/alias identities, without invocation-only option parsing. Preserve colon-bearing IDs during canonical opt-in, deduplication and skipping unavailable/denied choices; preserve explicit/per-tool model selection.

**Step → verify:** Run new enabled-by-default/preferred-model tests plus all auto/listing/alias tests. Verify missing fields preserve behavior, preference does not bypass a deny and a hidden canonical entry is not accidentally a security exemption. Repeat Task 22's identity captures for hidden tagged IDs and complete IDs ending in :for: visibility filtering must not trigger option reinterpretation, and preferences must preserve the canonical wire identity.

**Contract / gate:** No catalog expansion lands until discovery enablement and restriction precedence work. Hidden is a discovery setting, not a security policy.

<a id="task-27"></a>

## Task 27: Add reviewable model drift audit and candidate generation

- **Size:** M
- **Depends on:** Task 26
- **Design:** [d5-providers-and-model-policy](design.md#d5-providers-and-model-policy)

**Files / responsibility:** scripts/audit_model_registry.py (new); scripts/generate_model_entries.py (new); tests and docs/adding_models.md.

**Implementation step:** Implement a model drift audit with frozen catalog fixtures, schema-field validation and separate confirmed-deprecation/review-needed results; include models.dev status handling. Generate only evidence-backed hidden candidates without aliases/ranks; preserve hand-maintained entries and expose a review diff. Use Python/Make entry points, not a new just toolchain.

**Step → verify:** Run audit/generator fixture tests for absent community entries, expiration/deprecated status, unknown fields, duplicate aliases, partial responses and idempotent generation; network outage must not produce a clean audit.

**Contract / gate:** Build audit and candidate-generation behavior from the specified contracts. No automatic PR creation, scheduling or merging.

<a id="task-28"></a>

## Task 28: Reconcile useful model catalog changes

- **Size:** M
- **Depends on:** Task 21, Task 27
- **Design:** [d5-providers-and-model-policy](design.md#d5-providers-and-model-policy)

**Files / responsibility:** conf/openai_models.json; conf/gemini_models.json; conf/openrouter_models.json; conf/xai_models.json; model preference constants/tests.

**Implementation step:** Build a canonical-name comparison against current provider catalogs, preserving local Codex/Claude/Gemini additions and aliases. Record evidence for each addition, retirement and capability change. Promote only verified useful candidates; leave exploratory entries hidden. Update category fallback constants only when their named models or policy require it.

**Step → verify:** Run catalog schema/alias/provider-routing tests and a fresh drift report. Use mocked wire tests for declared capabilities; live probes are separate and explicitly configured. Record unresolved candidates instead of guessing.

**Contract / gate:** Native provider evidence outranks a community-catalog omission. Preserve existing canonical entries and aliases unless a verified correction requires a change.

<a id="task-29"></a>

## Task 29: Report request-scoped progress over MCP 1.x

- **Size:** M
- **Depends on:** Task 19, Task 20, Task 23
- **Design:** [d6-budgets-progress-and-tool-surface](design.md#d6-budgets-progress-and-tool-surface)

**Files / responsibility:** utils/progress.py (new); server.py; tools/simple/base.py; tools/workflow/workflow_mixin.py; clink/agents/base.py; consensus hooks.

**Implementation step:** Create a request-local reporter from v1 request_context; emit monotonic progress only when a token exists and publish it through a resettable ContextVar. Add heartbeat contexts around awaited provider/CLI operations and result usage/duration metadata; stop/cancel background tasks deterministically.

**Step → verify:** Create tests/test_progress.py and tests/test_clink_progress.py: token 0 is valid, missing token is inert, failures preserve results, concurrent tokens never mix, counters increase and cancellation leaves no heartbeat.

**Contract / gate:** Do not send prompts or guarantee client timeout behavior. Hook additions are mechanical; reporter lifecycle is the main behavior.

<a id="task-30"></a>

## Task 30: Move logs to a shared portable state-path policy

- **Size:** M
- **Depends on:** —
- **Design:** [d7-state-transcripts-and-resources](design.md#d7-state-transcripts-and-resources)

**Files / responsibility:** utils/state_dir.py and log_dir.py (new); server.py; run-server.sh/ps1; simulator log helpers; deployment docs.

**Implementation step:** Resolve PAL_STATE_DIR/XDG/default and PAL_LOG_DIR centrally; use the same result in server, log-follow commands, container configuration and simulator readers. Correct rotation sizes/backup arithmetic and narrow activity logging error catches with diagnostic context; document the old-log migration and keep old files untouched.

**Step → verify:** Run new state/log-dir tests, existing deployment/log tests and installed-package startup in a read-only package directory; test explicit paths, spaces, unset home/XDG and Windows behavior.

**Contract / gate:** Keep the current setup script/toolchain. All log producers and readers must use the same path-resolution policy.

<a id="task-31"></a>

## Task 31: Add opt-in private conversation transcripts

- **Size:** M
- **Depends on:** Task 9, Task 30
- **Design:** [d7-state-transcripts-and-resources](design.md#d7-state-transcripts-and-resources)

**Files / responsibility:** utils/transcripts.py (new); utils/conversation_memory.py; server.py startup pruning; transcript tests/docs.

**Implementation step:** Write versioned thread/turn JSONL records only after successful memory writes; default recording off locally and document retention/disable behavior. Validate UUIDs and directory containment, reject links, enforce private permissions with explicit cross-platform handling, and prune only eligible expired regular transcript files.

**Step → verify:** Add transcript tests for initial/user/raw-reply records, Unicode, existing permissive modes, malformed IDs, symlinks, partial append/read, retention errors and disabled mode. A transcript failure must warn without corrupting in-memory state or losing an answer.

**Contract / gate:** This does not persist resumable workflow state across server restarts. Transcript errors are visible without undoing successful memory writes.

<a id="task-32"></a>

## Task 32: Expose bounded conversation resources through MCP 1.x

- **Size:** M
- **Depends on:** Task 31, Task 23
- **Design:** [d7-state-transcripts-and-resources](design.md#d7-state-transcripts-and-resources)

**Files / responsibility:** utils/transcript_reader.py (new); server.py resource decorators; resource/reader tests; docs/logging.md.

**Implementation step:** Implement pal://threads and canonical UUID thread resources with deterministic bounded listing and read limits, plus explicit skipped-record diagnostics. Map missing/disabled resources to Resource not found and distinguish permission/I/O failures; reject traversal and symlinks. Hide old data when recording/resources are disabled.

**Step → verify:** Run reader/resource tests and a v1 stdio client handshake/list/read round trip; check advertised capability, malformed/unknown URIs, disabled existing files, huge transcripts and interrupted final lines.

**Contract / gate:** Verify resource-not-found error mapping against the retained MCP 1.x protocol/SDK before implementation; distinguish it from permission and I/O failures.

<a id="task-33"></a>

## Task 33: Reduce tool schema overhead without contract loss

- **Size:** M
- **Depends on:** Task 10, Task 23, Task 12, Task 13
- **Design:** [d6-budgets-progress-and-tool-surface](design.md#d6-budgets-progress-and-tool-surface)

**Files / responsibility:** scripts/tool_surface.py (new); tools/shared/base_models.py; selected get_input_schema/get_description implementations; tests.

**Implementation step:** Capture baseline tool names, required fields and description size; preserve the same capability names and local clink parameters while shortening repeated prose. Remove only proved-unused advertised fields and relax model requirements for provider-free tools; keep accepted legacy request inputs for a documented transition.

**Step → verify:** Run tool-surface/schema/prompt tests and compare semantic snapshots: names and used controls unchanged, excluded fields intentional, total description size lower and safety constraints retained. Include all systemprompt modules in the guard.

**Contract / gate:** If description editing spans many substantive tool behaviors, split by simple/workflow family before implementation; mechanical text edits do not justify behavior changes.

<a id="task-34"></a>

## Task 34: Repair simulator session lifetime and model selection

- **Size:** M
- **Depends on:** Task 9, Task 23, Task 30
- **Design:** [d8-simulator-and-test-reliability](design.md#d8-simulator-and-test-reliability)

**Files / responsibility:** communication_simulator_test.py; simulator_tests/base_test.py; conversation_base_test.py; scenario declarations/tests.

**Implementation step:** Keep one MCP process/session for each complete scenario; use the installed Python and correctly close it, instead of launching a new in-memory store per call. Add configurable SIMULATOR_MODEL/provider-agnostic selection, selected-test result accounting and clear mutually exclusive selection modes. Correct Ollama /v1 examples without changing the user's default local model.

**Step → verify:** Run new server-session/CI-mode unit tests with a fake transport, then provider-agnostic scenarios against a configured local endpoint; assert multi-call state survives and selected failure yields nonzero exit with summary.

**Contract / gate:** Never classify a routing- or response-quality-dependent scenario as stub-safe. Each multi-call scenario owns one complete server-session lifetime.

<a id="task-35"></a>

## Task 35: Add a strict stub simulator tier to local CI

- **Size:** M
- **Depends on:** Task 34, Task 22
- **Design:** [d8-simulator-and-test-reliability](design.md#d8-simulator-and-test-reliability)

**Files / responsibility:** simulator_tests/stub_provider.py and conf/stub_models.json (new); .github/workflows/test.yml; stub/scenario tests.

**Implementation step:** Serve only the required local OpenAI-compatible routes with strict model/message/stream checks and deterministic content; bind loopback and choose test-safe ports. Run the provider-agnostic tier on PR/main push in the existing workflow, isolating all provider credentials and cleaning up subprocesses. Keep optional local-model/live tiers separate.

**Step → verify:** Run stub contract tests and the full selected --ci stdio scenarios with zero external credentials; assert wrong route/model/schema fails, selected failures fail CI and no child process survives teardown.

**Contract / gate:** Extend this repository's existing CI. The stub verifies integration contracts, not model reasoning quality.

<a id="task-36"></a>

## Task 36: Add a verified optional OpenCode Go provider

- **Size:** M
- **Depends on:** Task 9, Task 22, Task 26, Task 27, Task 29
- **Design:** [d9-optional-service-integrations](design.md#d9-optional-service-integrations)

**Files / responsibility:** providers/opencode_go.py and registries/opencode_go.py (new); conf/opencode_go_models.json (new); session-context helper; mechanical registration/tests.

**Implementation step:** Revalidate official endpoints/auth/catalog with kk:dependency-handling; enable only models whose actual protocol the selected adapter supports. Wire provider metadata, package data, restrictions and PAL user agent. Use the real thread ID before the first call and reset request context on exit; keep x-opencode-session per request, not on the shared client. Respect model-specific temperature/output controls.

**Step → verify:** Run provider wire tests for supported endpoint families, first/continued/concurrent thread IDs, header safety, restrictions and absent configuration. Perform an explicitly configured smoke test before promoting entries.

**Contract / gate:** Deferred: Messages-only entries until a separate adapter design and wire-contract test establish support. Record their IDs, required protocol and next validation step during implementation.

<a id="task-37"></a>

## Task 37: Add optional Jules discovery, creation and status

- **Size:** M
- **Depends on:** Task 23, Task 29
- **Design:** [d9-optional-service-integrations](design.md#d9-optional-service-integrations)

**Files / responsibility:** tools/jules.py (new); systemprompts/jules_prompt.py (new); tool registration and docs; tests/test_jules.py (new).

**Implementation step:** Verify current v1alpha sources/sessions/activities contracts and declare directly imported runtime dependencies through pip-tools. Add a configured, provider-free action tool with accurate mutation annotations. Implement source discovery, explicit create and paginated status; validate opaque session IDs and inputs before network I/O. Default to plan approval and require explicit automatic PR mode.

**Step → verify:** Use a fake HTTP transport for payload/auth/path/pagination/error/timeout cases; no provider keys must be required, no Jules key produces an actionable tool error, and creation is not retried blindly. Live creation needs an explicitly designated repository.

**Contract / gate:** Creating a Jules session or PR requires an explicitly designated test target. Offline contract tests do not activate the service.

<a id="task-38"></a>

## Task 38: Add Jules steering and verified lifecycle actions

- **Size:** M
- **Depends on:** Task 37
- **Design:** [d9-optional-service-integrations](design.md#d9-optional-service-integrations)

**Files / responsibility:** tools/jules.py; systemprompts/jules_prompt.py; tests/test_jules.py; docs/tools/jules.md.

**Implementation step:** Add message and approve actions with state-aware responses and validated session IDs; expose polling to the caller instead of blocking until coding completes. Verify archive/unarchive against current official API/discovery data. Implement and test only if supported; otherwise retain a durable deferred entry naming the missing evidence and next validation step.

**Step → verify:** Run action dispatch/invalid-ID/unknown-state/auth/rate-limit tests; verify each action uses its own method/path and cannot be injected with slash/query/fragment characters.

**Contract / gate:** Archive/unarchive remain gated on official API support and contract tests. Keep an explicit follow-up if either action cannot be verified.

<a id="task-39"></a>

## Task 39: Measure cache usage and request completeness

- **Size:** M
- **Depends on:** Task 20, Task 31, Task 35
- **Design:** [d10-prompt-cache-work-is-measurement-led](design.md#d10-prompt-cache-work-is-measurement-led)

**Files / responsibility:** provider usage parsers; scripts/cache_report.py and cache_prefix.py (new); provider/prompt-capture tests.

**Implementation step:** Verify optional cached-input/cache-write fields per actual provider endpoint and preserve unknown versus zero; carry each invocation's usage into transcript metadata without cumulative double-counting. Add offline captured-request completeness/prefix metrics and a report grouped by provider/model, with missing-field coverage. Establish a measured baseline after Task 9; report only results reproduced by this project's request-capture harness.

**Step → verify:** Run usage fixtures with missing/zero/malformed fields and multi-step consensus. Capture requests through the actual dispatcher and assert the content required by each consumer's contract appears exactly once. For consensus, assert the original proposal, stance and explicit attachments reach each participant on fresh tool instances; sentinel verdicts, intervening summaries and generic thread history must not. Check complete, nonduplicated audit turns separately in storage. Intentional blinding must not be reported as a completeness failure.

**Contract / gate:** Cost calculations require separately verified rates and denominators. No live calls or price claims are needed for the offline completeness gate.

<a id="task-40"></a>

## Task 40: Stabilize continuation prompts without losing content

- **Size:** M
- **Depends on:** Task 39, Task 24
- **Design:** [d10-prompt-cache-work-is-measurement-led](design.md#d10-prompt-cache-work-is-measurement-led)

**Files / responsibility:** utils/conversation_memory.py; tools/workflow/workflow_mixin.py; tools/consensus.py; deterministic prompt tests.

**Implementation step:** Move volatile turn counters away from leading history and render set-derived files/symbols/images deterministically; preserve error/omission and provenance information. Evaluate moving consensus stance after stable content using captured requests and behavior regressions; add provider-specific routing keys only after verifying each provider accepts them.

**Step → verify:** Run completeness gates first, then compare longest common prefixes under several PYTHONHASHSEED values, model switches and attached files; retain cross-tool/stance semantics and record baseline/candidate metrics.

**Contract / gate:** Do not let a higher prefix score pass by omitting context. Revalidate live cache benefit separately.

<a id="task-41"></a>

## Task 41: Evaluate append-oriented history and compaction

- **Size:** M
- **Depends on:** Task 40
- **Design:** [d10-prompt-cache-work-is-measurement-led](design.md#d10-prompt-cache-work-is-measurement-led)

**Files / responsibility:** utils/conversation_memory.py; utils/file_utils.py; prompt-prefix tests; relevant per-file descriptions documentation.

**Implementation step:** Use measured file/history churn to evaluate first-seen rendering, changed-file version presentation and compaction hysteresis. Reconcile any header change with the existing per-file descriptions design before coding. Implement only the justified layout slice, preserve fresh file content and explicit omitted-turn markers, and record the decision plus rollback format. Defer strict-prefix instruction relocation and provider-explicit caches until measured benefit/API evidence warrants separate tasks.

**Step → verify:** Compare fixed, added, touched and edited files plus small-budget compaction over at least 12 turns. Assert no stale/missing context, bounded history and fewer gratuitous prefix rewrites; live cost/quality results must be recorded before claiming savings.

**Contract / gate:** Conditional research/implementation slice. If evidence rejects the layout change, completion is a recorded no-change decision and concrete follow-up, not a fabricated optimization.

<a id="task-42"></a>

## Task 42: Verify the improved system and complete its documentation

- **Size:** S
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9, Task 10, Task 11, Task 12, Task 13, Task 14, Task 15, Task 16, Task 17, Task 18, Task 19, Task 20, Task 21, Task 22, Task 23, Task 24, Task 25, Task 26, Task 27, Task 28, Task 29, Task 30, Task 31, Task 32, Task 33, Task 34, Task 35, Task 36, Task 37, Task 38, Task 39, Task 40, Task 41
- **Design:** [outcome-and-scope](design.md#outcome-and-scope)

**Files / responsibility:** All changed runtime files and tests; README.md; .env.example; docs/architecture.md; affected tool/config/log/testing docs; this task list.

**Implementation step:** Run kk:test over all implemented tasks: lock-based full unit matrix, required local integrations and relevant simulator paths. Mark unavailable optional live service checks explicitly and keep associated release gates open. Run kk:document, kk:review-code with Python, and kk:review-spec against this design/implementation/task set. Record and resolve findings or persist actionable deferred items before declaring the affected phase ready. Update architecture and configuration docs from the final implementation, preserving project identity and repository instructions; link final verification evidence and close each actual task checkbox.

**Step → verify:** Full quality script, lock freshness, distribution/image checks, strict stub CI, configured live/local integrations and independent code/spec reviews pass for the released scope. No implementation task is done until its behavior and acceptance checks have been verified.

**Contract / gate:** Design drafting is not independent implementation review. Committing, publishing and external messaging are separate actions; never commit .capy/knowledge.db.

## Validation evidence to retain

For each completed slice record the changed files, test invocation/result,
contract decisions and unresolved release gates in its tasks.md entry.
For dependency, container and service tasks also record the verified versions and
dated API-contract evidence. For cache tasks retain synthetic captured-input
fixtures and baseline/candidate results without credentials or private prompts.

The final gate invokes kk:test, kk:document, kk:review-code with Python and
kk:review-spec. Their findings must be fixed or durably tracked with a concrete next
step. Independent design review remains a separate recommended next action before
implementation.

# Tasks: server reliability and capabilities

> Design: [design.md](design.md)
> Implementation: [implementation.md](implementation.md)
> Status: pending
> Created: 2026-10-10
> Not Doing: toolchain/release-process migration; MCP 2.x; wholesale catalogs; replacement of safety controls; file-annotation implementation; persistent continuation recovery; remote multi-user transport; automatic service activation

All checkboxes are intentionally open: these documents specify planned work;
implementation has not started. Use each implementation section's acceptance contract and record
actual verification evidence when updating status. A dash under parallel work is
conservative because most slices share core files; it is not a runtime dependency.

## Task 1: Validate a patched MCP 1.x lock set

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-1); [design contract](design.md#d1-runtime-and-packaging)

- [ ] 1.1 Confirm the proposed Python 3.10 floor against the existing 3.10–3.12 matrix and actual supported consumers; correct package metadata if accepted. Record any retained 3.9 requirement as a blocking compatibility decision.
- [ ] 1.2 Check current MCP advisories and select a compatible patched 1.x release; retain <2.0.0 and pip-compile both locks with --strip-extras --no-header. Create a fresh validation environment from both lock files.
- [ ] 1.3 Verify the acceptance cases in [Task 1](implementation.md#task-1) and record the result here.

**Scope / gate:** Risk-first. Validate in a fresh environment installed from the generated locks; an existing developer environment is not evidence for those pins. No general dependency upgrade or toolchain switch.

## Task 2: Refresh and verify the existing container

- **Status:** pending
- **Depends on:** Task 1
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-2); [design contract](design.md#d1-runtime-and-packaging)

- [ ] 2.1 Rebuild the current pip-based image with a verified supported base and required distribution fixes; preserve non-root execution and local image metadata.
- [ ] 2.2 Record scanner findings by installed package and applicability, with an actionable follow-up for each unresolved finding. Report only the security properties established by the current build and scan.
- [ ] 2.3 Verify the acceptance cases in [Task 2](implementation.md#task-2) and record the result here.

**Scope / gate:** Use kk:dependency-handling before selecting a new image tag; keep the existing release workflow. Retain scanner evidence and actionable follow-up for unresolved findings.

## Task 3: Repair canonical license and package notices

- **Status:** pending
- **Depends on:** —
- **Size:** S
- **Can run in parallel with:** Task 4, Task 5, Task 6, Task 30
- **Docs:** [Implementation slice](implementation.md#task-3); [design contract](design.md#d1-runtime-and-packaging)

- [ ] 3.1 Compare LICENSE with the canonical Apache 2.0 text and restore it without altering terms; ensure NOTICE retains accurate existing project notices.
- [ ] 3.2 Declare license metadata using the installed build backend's verified supported fields and include the text/notice in source and wheel distributions.
- [ ] 3.3 Verify the acceptance cases in [Task 3](implementation.md#task-3) and record the result here.

**Scope / gate:** Preserve the project's existing legal notices and ownership metadata. This task corrects license text and distribution contents; it does not change ownership.

## Task 4: Contain generated chat artifacts

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** Task 3, Task 5, Task 6, Task 8, Task 30
- **Docs:** [Implementation slice](implementation.md#task-4); [design contract](design.md#d2-file-and-cli-boundaries)

- [ ] 4.1 Validate operator PAL_WORKSPACE_ROOT and supplied working directory before generation; define cwd fallback in configuration docs.
- [ ] 4.2 Create unique per-call artifacts inside a validated sandbox with exclusive/no-follow handling; reject symlink escapes at root/sandbox/file boundaries and surface policy/write failures without inline fallback instructions.
- [ ] 4.3 Verify the acceptance cases in [Task 4](implementation.md#task-4) and record the result here.

**Scope / gate:** Keep read-only chat file access independent from the artifact write root. Validate the root, artifact directory and destination as separate boundaries; names must be unique per call.

## Task 5: Bound file reads and preserve the local deny policy

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** Task 3, Task 4, Task 6, Task 30
- **Docs:** [Implementation slice](implementation.md#task-5); [design contract](design.md#d2-file-and-cli-boundaries)

- [ ] 5.1 Extend the existing path policy to reject pseudofilesystem/system directories, including /proc, /sys, /dev and /run, while retaining sensitive-home protections; validate paths before token-estimation stat calls.
- [ ] 5.2 Read at most max_size+1 bytes before decoding and distinguish true overflow; preserve formatting for ordinary successful reads.
- [ ] 5.3 Verify the acceptance cases in [Task 5](implementation.md#task-5) and record the result here.

**Scope / gate:** Preserve DANGEROUS_HOME_SUBDIRECTORIES and existing legitimate-path behavior. Mock dangerous filesystem entries; never read real credential files in tests.

## Task 6: Isolate clink environment and working directory

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** Task 3, Task 4, Task 5, Task 8
- **Docs:** [Implementation slice](implementation.md#task-6); [design contract](design.md#d2-file-and-cli-boundaries)

- [ ] 6.1 Add shared environment names, vendor-specific passthrough and explicit config overrides. Preserve auth/runtime variables including Windows/locale/TLS requirements.
- [ ] 6.2 Run in configured working_dir or a temporary per-call directory. Forward allow_edits and editable_paths through every wrapper unchanged, and retain system-prompt ContextVar cleanup.
- [ ] 6.3 Verify the acceptance cases in [Task 6](implementation.md#task-6) and record the result here.

**Scope / gate:** Model/registry declarations are mechanical; the behavior lives in the runner. A scratch directory is not a filesystem sandbox. Verify the configured CLI's repository requirements.

## Task 7: Report Gemini CLI unsupported-client failures

- **Status:** pending
- **Depends on:** Task 6
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-7); [design contract](design.md#d2-file-and-cli-boundaries)

- [ ] 7.1 Recognize unsupported-client diagnostics in failure stderr, non-JSON output and empty/error JSON paths; integrate agent error recovery.
- [ ] 7.2 Keep successful answers mentioning the marker intact, and return vendor-documented remediation without unverified account/date claims.
- [ ] 7.3 Verify the acceptance cases in [Task 7](implementation.md#task-7) and record the result here.

**Scope / gate:** Diagnose an unsuccessful CLI invocation from its error channel or error envelope. Successful answer text alone must never trigger unsupported-client handling.

## Task 8: Make conversation storage failures explicit

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** Task 4, Task 6, Task 30
- **Docs:** [Implementation slice](implementation.md#task-8); [design contract](design.md#d3-conversation-ownership-and-truthful-results)

- [ ] 8.1 Introduce typed storage/decode errors distinct from absent/expired/capped threads, preserving original causes. Fail required continuation reads/input writes before a provider call.
- [ ] 8.2 Propagate post-response persistence failures as visible result warnings while retaining the generated response; audit create/get/add/chain callers and false return values.
- [ ] 8.3 Verify the acceptance cases in [Task 8](implementation.md#task-8) and record the result here.

**Scope / gate:** Preserve genuine programming errors and exception causes. Only expected storage/decoding faults enter the typed memory-error path.

## Task 9: Record turns once and rebuild complete continuation context

- **Status:** pending
- **Depends on:** Task 8
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-9); [design contract](design.md#d3-conversation-ownership-and-truthful-results)

- [ ] 9.1 Give server-built history a trusted internal marker/context; separate it from caller-supplied arguments and retain a correct direct-tool path. Extract context/resolution helpers without changing explicit model semantics.
- [ ] 9.2 Create thread/input before generation; record workflow step and raw assistant reply exactly once, including clink's first input. Re-read after adding the current turn before rendering its files.
- [ ] 9.3 Verify the acceptance cases in [Task 9](implementation.md#task-9) and record the result here.

**Scope / gate:** Re-record affected live cassettes only in an explicitly configured validation run; never edit recorded request bodies to manufacture a pass.

## Task 10: Isolate workflow instances and persist their configuration

- **Status:** pending
- **Depends on:** Task 8, Task 9
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-10); [design contract](design.md#d3-conversation-ownership-and-truthful-results)

- [ ] 10.1 Dispatch each invocation to a fresh tool while keeping schema prototypes; reset reused direct-call objects and restore work_history plus tool_state for the correct tool/thread.
- [ ] 10.2 Declare/persist review_config, analysis_config, git_config, refactor_config, trace_config, stored_request_params and planner branches; implement consensus roster/responses explicitly.
- [ ] 10.3 Verify the acceptance cases in [Task 10](implementation.md#task-10) and record the result here.

**Scope / gate:** State declarations across workflow tools are mechanical; per-call construction and correct restoration are the substantive change. Preserve each declared configuration and planner/consensus state.

## Task 11: Serialize concurrent updates to one conversation

- **Status:** pending
- **Depends on:** Task 10
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-11); [design contract](design.md#d3-conversation-ownership-and-truthful-results)

- [ ] 11.1 Add a per-thread async critical section covering reconstruction through persistence, with bounded/ref-counted lock lifetime. Independent thread IDs do not share the same lock.
- [ ] 11.2 Define cancellation and failed-call cleanup, and document that direct tool-object APIs do not acquire the MCP boundary's lock unless explicitly using the same coordinator.
- [ ] 11.3 Verify the acceptance cases in [Task 11](implementation.md#task-11) and record the result here.

**Scope / gate:** Complete same-thread coordination before enabling more provider concurrency. Do not hold one global lock while awaiting a model.

## Task 12: Make workflow model provenance truthful

- **Status:** pending
- **Depends on:** Task 10
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-12); [design contract](design.md#d3-conversation-ownership-and-truthful-results)

- [ ] 12.1 Track actual attempted model/provider calls in per-request state; local-only and intermediate responses report null rather than an inferred configured model.
- [ ] 12.2 Change completion wording to distinguish caller_findings from external analysis, document compatibility for final_analysis consumers, and consolidate the duplicate completion handler only as part of this change.
- [ ] 12.3 Verify the acceptance cases in [Task 12](implementation.md#task-12) and record the result here.

**Scope / gate:** Do not invent confidence when the caller supplied none or delete unrelated dead code. Provider identity reflects an actual attempted call, not configured defaults.

## Task 13: Report partial and failed consensus honestly

- **Status:** pending
- **Depends on:** Task 10, Task 12
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-13); [design contract](design.md#d3-conversation-ownership-and-truthful-results)

- [ ] 13.1 Count successful consultations separately from requested/failed models, including represented providers when available.
- [ ] 13.2 Use the same summary builder for both completion paths, expose participant errors and mark partial or zero-success outcomes without claiming full/high-confidence consensus.
- [ ] 13.3 Verify the acceptance cases in [Task 13](implementation.md#task-13) and record the result here.

**Scope / gate:** Keep the original proposal and successful responses available even when another participant fails. Both completion paths must produce the same degraded-result accounting.

## Task 14: Make chat generation yield through a migration adapter

- **Status:** pending
- **Depends on:** Task 10, Task 11
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-14); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 14.1 Introduce a temporary internal invocation adapter that awaits coroutine providers and offloads legacy synchronous generation to asyncio.to_thread.
- [ ] 14.2 Route simple-tool primary and recovery/retry calls through it; update only affected mocks to the actual synchronous or asynchronous contract.
- [ ] 14.3 Verify the acceptance cases in [Task 14](implementation.md#task-14) and record the result here.

**Scope / gate:** This adapter is removed in Task 19; cancelling an await of a worker thread does not guarantee cancellation of its network request.

## Task 15: Make expert and consensus generation yield

- **Status:** pending
- **Depends on:** Task 14
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-15); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 15.1 Route expert generation and consensus consultation through the temporary invocation adapter; preserve per-model error results and request-scoped state.
- [ ] 15.2 Correct affected AsyncMock use and failure-path assertions, including the pre-existing consensus/workflow UTF-8 warnings.
- [ ] 15.3 Verify the acceptance cases in [Task 15](implementation.md#task-15) and record the result here.

**Scope / gate:** Preserve participant result semantics and the degraded-accounting contract in Task 13. Use mocks that match the active sync/async boundary.

## Task 16: Migrate OpenAI-compatible and Azure wire calls

- **Status:** pending
- **Depends on:** Task 15
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-16); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 16.1 Introduce the shared native build/call/parse/retry path and native async OpenAI/Azure clients while preserving request payloads and custom HTTP settings.
- [ ] 16.2 Make Azure overrides await the shared path; characterize inherited OpenRouter/custom/xAI behavior. Keep DIAL's separate legacy generation path functioning until Task 18.
- [ ] 16.3 Verify the acceptance cases in [Task 16](implementation.md#task-16) and record the result here.

**Scope / gate:** Follow kk:dependency-handling using locked SDKs. This family migration does not change catalogs or provider metadata; DIAL remains functional until its own migration.

## Task 17: Migrate Gemini generation to the async SDK

- **Status:** pending
- **Depends on:** Task 15
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-17); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 17.1 Use the verified GenAI async generation surface and the shared retry contract; preserve image conversion, safety/usage parsing and timeouts.
- [ ] 17.2 Add an awaited async cleanup hook without creating a previously unused client. Keep budget/level behavior unchanged until Task 21.
- [ ] 17.3 Verify the acceptance cases in [Task 17](implementation.md#task-17) and record the result here.

**Scope / gate:** Native SDK capability is verified separately from per-model behavior. Cleanup must not allocate a client that was never used.

## Task 18: Migrate DIAL generation and shared transport ownership

- **Status:** pending
- **Depends on:** Task 16
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-18); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 18.1 Move deployment-specific calls and event hooks to async forms while retaining DIAL auth/header stripping, URL mapping and shared connection-pool ownership.
- [ ] 18.2 Close deployment wrappers/shared clients without double-closing, leaking coroutines or constructing extra clients.
- [ ] 18.3 Verify the acceptance cases in [Task 18](implementation.md#task-18) and record the result here.

**Scope / gate:** Await event hooks and close shared pools once. Wrappers must distinguish owned from borrowed transports, including cancellation and repeated shutdown.

## Task 19: Finish the async contract and deterministic shutdown

- **Status:** pending
- **Depends on:** Task 16, Task 17, Task 18
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-19); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 19.1 Make generate_content an async-only provider contract after every backend is migrated; replace adapter calls with direct awaits and remove only that newly orphaned adapter/legacy retry path.
- [ ] 19.2 Await provider cleanup from server lifecycle before the event loop exits. Preserve errors and cancellation without a fire-and-forget success path.
- [ ] 19.3 Verify the acceptance cases in [Task 19](implementation.md#task-19) and record the result here.

**Scope / gate:** No permanently mixed sync/async contract. Attribute expected test changes to actual endpoint behavior.

## Task 20: Enforce output limits and normalize endpoint usage

- **Status:** pending
- **Depends on:** Task 19
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-20); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 20.1 Send each resolved model's effective output ceiling on primary, recovery, expert and consensus requests; distinguish unavailable metadata from an unlimited successful call.
- [ ] 20.2 Use Responses max_output_tokens and the appropriate Chat Completions field; parse input/output totals using each endpoint's schema, preserving absent usage as unknown.
- [ ] 20.3 Verify the acceptance cases in [Task 20](implementation.md#task-20) and record the result here.

**Scope / gate:** Keep OpenRouter store omission and custom unsupported-temperature behavior. Missing usage or output-limit metadata must remain distinguishable from a valid zero.

## Task 21: Send valid Gemini thinking controls

- **Status:** pending
- **Depends on:** Task 17
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-21); [design contract](design.md#d4-async-provider-lifecycle)

- [ ] 21.1 Select budget versus level from verified model generation/capability metadata; add supported_thinking_levels only where verified.
- [ ] 21.2 Map PAL minimal/low/medium/high/max to accepted levels, clamping down then up when needed and surfacing invalid/empty capability configuration.
- [ ] 21.3 Verify the acceptance cases in [Task 21](implementation.md#task-21) and record the result here.

**Scope / gate:** The SDK having an enum does not prove a particular model accepts every member. Capability declarations need per-model verification.

## Task 22: Centralize provider configuration and restriction discovery

- **Status:** pending
- **Depends on:** Task 19
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-22); [design contract](design.md#d5-providers-and-model-policy)

- [ ] 22.1 Declare provider identity/env/placeholder/required/optional/restriction metadata and factories; derive configuration/help/listmodels/test isolation from that source.
- [ ] 22.2 Apply allowlists to every provider, including CUSTOM and Azure; validate names through the provider's alias/routing behavior, keeping custom API keys optional.
- [ ] 22.3 Verify the acceptance cases in [Task 22](implementation.md#task-22) and record the result here.

**Scope / gate:** Preserve intentional routing priority; add OpenCode Go separately in Task 36. Provider metadata drives discovery, restrictions, setup diagnostics and test isolation.

## Task 23: Report missing and broken provider configuration correctly

- **Status:** pending
- **Depends on:** Task 22
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-23); [design contract](design.md#d5-providers-and-model-policy)

- [ ] 23.1 Keep the server discoverable with no configured providers; return explicit MCP tool-error results only for calls that require one.
- [ ] 23.2 Raise file-naming typed errors for malformed explicitly selected/shipped registries, distinguishing absent optional, empty optional, JSON/schema/alias and I/O failures.
- [ ] 23.3 Verify the acceptance cases in [Task 23](implementation.md#task-23) and record the result here.

**Scope / gate:** Use MCP 1.x decorators/results. Avoid circular-import error plumbing where a dedicated exception module suffices.

## Task 24: Unify file budgets and report actual context coverage

- **Status:** pending
- **Depends on:** Task 5, Task 9, Task 20
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-24); [design contract](design.md#d6-budgets-progress-and-tool-surface)

- [ ] 24.1 Compute one effective file budget and pass remaining-history limits through gate and embedding. Report binding budget, largest files and overage while preserving directory/alias/dedup semantics.
- [ ] 24.2 Track files actually embedded now and actually included from history separately; account for rendered headers and the pending per-file descriptions feature.
- [ ] 24.3 Verify the acceptance cases in [Task 24](implementation.md#task-24) and record the result here.

**Scope / gate:** The file-annotation compatibility contract in design.md is a coordination dependency; implementing annotations is outside this roadmap.

## Task 25: Add a global model deny policy

- **Status:** pending
- **Depends on:** Task 22
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-25); [design contract](design.md#d5-providers-and-model-policy)

- [ ] 25.1 Parse DISABLED_MODELS with recognized provider scopes while preserving colon-bearing local model IDs.
- [ ] 25.2 Enforce canonical/alias-equivalent denies across explicit calls, discovery, summary and fallback after provider allowlists; warn on unknown configured entries without exposing secrets.
- [ ] 25.3 Verify the acceptance cases in [Task 25](implementation.md#task-25) and record the result here.

**Scope / gate:** A canonical alias cannot bypass a deny. Parse provider scope only when the prefix is a recognized provider name.

## Task 26: Add hidden catalog entries and ordered auto preferences

- **Status:** pending
- **Depends on:** Task 25
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-26); [design contract](design.md#d5-providers-and-model-policy)

- [ ] 26.1 Add enabled_by_default with a backward-compatible true default; hide disabled entries/aliases from discovery while permitting exact canonical calls that pass policy.
- [ ] 26.2 Implement ordered PREFERRED_MODELS for auto/fallback only, canonical opt-in, deduplication and skipping unavailable/denied choices; preserve explicit/per-tool model selection.
- [ ] 26.3 Verify the acceptance cases in [Task 26](implementation.md#task-26) and record the result here.

**Scope / gate:** No catalog expansion lands until discovery enablement and restriction precedence work. Hidden is a discovery setting, not a security policy.

## Task 27: Add reviewable model drift audit and candidate generation

- **Status:** pending
- **Depends on:** Task 26
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-27); [design contract](design.md#d5-providers-and-model-policy)

- [ ] 27.1 Implement a model drift audit with frozen catalog fixtures, schema-field validation and separate confirmed-deprecation/review-needed results; include models.dev status handling.
- [ ] 27.2 Generate only evidence-backed hidden candidates without aliases/ranks; preserve hand-maintained entries and expose a review diff. Use Python/Make entry points, not a new just toolchain.
- [ ] 27.3 Verify the acceptance cases in [Task 27](implementation.md#task-27) and record the result here.

**Scope / gate:** Build audit and candidate-generation behavior from the specified contracts. No automatic PR creation, scheduling or merging.

## Task 28: Reconcile useful model catalog changes

- **Status:** pending
- **Depends on:** Task 21, Task 27
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-28); [design contract](design.md#d5-providers-and-model-policy)

- [ ] 28.1 Build a canonical-name comparison against current provider catalogs, preserving local Codex/Claude/Gemini additions and aliases. Record evidence for each addition, retirement and capability change.
- [ ] 28.2 Promote only verified useful candidates; leave exploratory entries hidden. Update category fallback constants only when their named models or policy require it.
- [ ] 28.3 Verify the acceptance cases in [Task 28](implementation.md#task-28) and record the result here.

**Scope / gate:** Native provider evidence outranks a community-catalog omission. Preserve existing canonical entries and aliases unless a verified correction requires a change.

## Task 29: Report request-scoped progress over MCP 1.x

- **Status:** pending
- **Depends on:** Task 19, Task 20, Task 23
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-29); [design contract](design.md#d6-budgets-progress-and-tool-surface)

- [ ] 29.1 Create a request-local reporter from v1 request_context; emit monotonic progress only when a token exists and publish it through a resettable ContextVar.
- [ ] 29.2 Add heartbeat contexts around awaited provider/CLI operations and result usage/duration metadata; stop/cancel background tasks deterministically.
- [ ] 29.3 Verify the acceptance cases in [Task 29](implementation.md#task-29) and record the result here.

**Scope / gate:** Do not send prompts or guarantee client timeout behavior. Hook additions are mechanical; reporter lifecycle is the main behavior.

## Task 30: Move logs to a shared portable state-path policy

- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** Task 3, Task 4, Task 5, Task 8
- **Docs:** [Implementation slice](implementation.md#task-30); [design contract](design.md#d7-state-transcripts-and-resources)

- [ ] 30.1 Resolve PAL_STATE_DIR/XDG/default and PAL_LOG_DIR centrally; use the same result in server, log-follow commands, container configuration and simulator readers.
- [ ] 30.2 Correct rotation sizes/backup arithmetic and narrow activity logging error catches with diagnostic context; document the old-log migration and keep old files untouched.
- [ ] 30.3 Verify the acceptance cases in [Task 30](implementation.md#task-30) and record the result here.

**Scope / gate:** Keep the current setup script/toolchain. All log producers and readers must use the same path-resolution policy.

## Task 31: Add opt-in private conversation transcripts

- **Status:** pending
- **Depends on:** Task 9, Task 30
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-31); [design contract](design.md#d7-state-transcripts-and-resources)

- [ ] 31.1 Write versioned thread/turn JSONL records only after successful memory writes; default recording off locally and document retention/disable behavior.
- [ ] 31.2 Validate UUIDs and directory containment, reject links, enforce private permissions with explicit cross-platform handling, and prune only eligible expired regular transcript files.
- [ ] 31.3 Verify the acceptance cases in [Task 31](implementation.md#task-31) and record the result here.

**Scope / gate:** This does not persist resumable workflow state across server restarts. Transcript errors are visible without undoing successful memory writes.

## Task 32: Expose bounded conversation resources through MCP 1.x

- **Status:** pending
- **Depends on:** Task 31, Task 23
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-32); [design contract](design.md#d7-state-transcripts-and-resources)

- [ ] 32.1 Implement pal://threads and canonical UUID thread resources with deterministic bounded listing and read limits, plus explicit skipped-record diagnostics.
- [ ] 32.2 Map missing/disabled resources to Resource not found and distinguish permission/I/O failures; reject traversal and symlinks. Hide old data when recording/resources are disabled.
- [ ] 32.3 Verify the acceptance cases in [Task 32](implementation.md#task-32) and record the result here.

**Scope / gate:** Verify resource-not-found error mapping against the retained MCP 1.x protocol/SDK before implementation; distinguish it from permission and I/O failures.

## Task 33: Reduce tool schema overhead without contract loss

- **Status:** pending
- **Depends on:** Task 10, Task 23, Task 12, Task 13
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-33); [design contract](design.md#d6-budgets-progress-and-tool-surface)

- [ ] 33.1 Capture baseline tool names, required fields and description size; preserve the same capability names and local clink parameters while shortening repeated prose.
- [ ] 33.2 Remove only proved-unused advertised fields and relax model requirements for provider-free tools; keep accepted legacy request inputs for a documented transition.
- [ ] 33.3 Verify the acceptance cases in [Task 33](implementation.md#task-33) and record the result here.

**Scope / gate:** If description editing spans many substantive tool behaviors, split by simple/workflow family before implementation; mechanical text edits do not justify behavior changes.

## Task 34: Repair simulator session lifetime and model selection

- **Status:** pending
- **Depends on:** Task 9, Task 23, Task 30
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-34); [design contract](design.md#d8-simulator-and-test-reliability)

- [ ] 34.1 Keep one MCP process/session for each complete scenario; use the installed Python and correctly close it, instead of launching a new in-memory store per call.
- [ ] 34.2 Add configurable SIMULATOR_MODEL/provider-agnostic selection, selected-test result accounting and clear mutually exclusive selection modes. Correct Ollama /v1 examples without changing the user's default local model.
- [ ] 34.3 Verify the acceptance cases in [Task 34](implementation.md#task-34) and record the result here.

**Scope / gate:** Never classify a routing- or response-quality-dependent scenario as stub-safe. Each multi-call scenario owns one complete server-session lifetime.

## Task 35: Add a strict stub simulator tier to local CI

- **Status:** pending
- **Depends on:** Task 34, Task 22
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-35); [design contract](design.md#d8-simulator-and-test-reliability)

- [ ] 35.1 Serve only the required local OpenAI-compatible routes with strict model/message/stream checks and deterministic content; bind loopback and choose test-safe ports.
- [ ] 35.2 Run the provider-agnostic tier on PR/main push in the existing workflow, isolating all provider credentials and cleaning up subprocesses. Keep optional local-model/live tiers separate.
- [ ] 35.3 Verify the acceptance cases in [Task 35](implementation.md#task-35) and record the result here.

**Scope / gate:** Extend this repository's existing CI. The stub verifies integration contracts, not model reasoning quality.

## Task 36: Add a verified optional OpenCode Go provider

- **Status:** pending
- **Depends on:** Task 9, Task 22, Task 26, Task 27, Task 29
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-36); [design contract](design.md#d9-optional-service-integrations)

- [ ] 36.1 Revalidate official endpoints/auth/catalog with kk:dependency-handling; enable only models whose actual protocol the selected adapter supports. Wire provider metadata, package data, restrictions and PAL user agent.
- [ ] 36.2 Use the real thread ID before the first call and reset request context on exit; keep x-opencode-session per request, not on the shared client. Respect model-specific temperature/output controls.
- [ ] 36.3 Verify the acceptance cases in [Task 36](implementation.md#task-36) and record the result here.

**Scope / gate:** Deferred: Messages-only entries until a separate adapter design and wire-contract test establish support. Record their IDs, required protocol and next validation step during implementation.

## Task 37: Add optional Jules discovery, creation and status

- **Status:** pending
- **Depends on:** Task 23, Task 29
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-37); [design contract](design.md#d9-optional-service-integrations)

- [ ] 37.1 Verify current v1alpha sources/sessions/activities contracts and declare directly imported runtime dependencies through pip-tools. Add a configured, provider-free action tool with accurate mutation annotations.
- [ ] 37.2 Implement source discovery, explicit create and paginated status; validate opaque session IDs and inputs before network I/O. Default to plan approval and require explicit automatic PR mode.
- [ ] 37.3 Verify the acceptance cases in [Task 37](implementation.md#task-37) and record the result here.

**Scope / gate:** Creating a Jules session or PR requires an explicitly designated test target. Offline contract tests do not activate the service.

## Task 38: Add Jules steering and verified lifecycle actions

- **Status:** pending
- **Depends on:** Task 37
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-38); [design contract](design.md#d9-optional-service-integrations)

- [ ] 38.1 Add message and approve actions with state-aware responses and validated session IDs; expose polling to the caller instead of blocking until coding completes.
- [ ] 38.2 Verify archive/unarchive against current official API/discovery data. Implement and test only if supported; otherwise retain a durable deferred entry naming the missing evidence and next validation step.
- [ ] 38.3 Verify the acceptance cases in [Task 38](implementation.md#task-38) and record the result here.

**Scope / gate:** Archive/unarchive remain gated on official API support and contract tests. Keep an explicit follow-up if either action cannot be verified.

## Task 39: Measure cache usage and request completeness

- **Status:** pending
- **Depends on:** Task 20, Task 31, Task 35
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-39); [design contract](design.md#d10-prompt-cache-work-is-measurement-led)

- [ ] 39.1 Verify optional cached-input/cache-write fields per actual provider endpoint and preserve unknown versus zero; carry each invocation's usage into transcript metadata without cumulative double-counting.
- [ ] 39.2 Add offline captured-request completeness/prefix metrics and a report grouped by provider/model, with missing-field coverage. Establish a measured baseline after Task 9; report only results reproduced by this project's request-capture harness.
- [ ] 39.3 Verify the acceptance cases in [Task 39](implementation.md#task-39) and record the result here.

**Scope / gate:** Cost calculations require separately verified rates and denominators. No live calls or price claims are needed for the offline completeness gate.

## Task 40: Stabilize continuation prompts without losing content

- **Status:** pending
- **Depends on:** Task 39, Task 24
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-40); [design contract](design.md#d10-prompt-cache-work-is-measurement-led)

- [ ] 40.1 Move volatile turn counters away from leading history and render set-derived files/symbols/images deterministically; preserve error/omission and provenance information.
- [ ] 40.2 Evaluate moving consensus stance after stable content using captured requests and behavior regressions; add provider-specific routing keys only after verifying each provider accepts them.
- [ ] 40.3 Verify the acceptance cases in [Task 40](implementation.md#task-40) and record the result here.

**Scope / gate:** Do not let a higher prefix score pass by omitting context. Revalidate live cache benefit separately.

## Task 41: Evaluate append-oriented history and compaction

- **Status:** pending
- **Depends on:** Task 40
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-41); [design contract](design.md#d10-prompt-cache-work-is-measurement-led)

- [ ] 41.1 Use measured file/history churn to evaluate first-seen rendering, changed-file version presentation and compaction hysteresis. Reconcile any header change with the existing per-file descriptions design before coding.
- [ ] 41.2 Implement only the justified layout slice, preserve fresh file content and explicit omitted-turn markers, and record the decision plus rollback format. Defer strict-prefix instruction relocation and provider-explicit caches until measured benefit/API evidence warrants separate tasks.
- [ ] 41.3 Verify the acceptance cases in [Task 41](implementation.md#task-41) and record the result here.

**Scope / gate:** Conditional research/implementation slice. If evidence rejects the layout change, completion is a recorded no-change decision and concrete follow-up, not a fabricated optimization.

## Task 42: Verify the improved system and complete its documentation

- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9, Task 10, Task 11, Task 12, Task 13, Task 14, Task 15, Task 16, Task 17, Task 18, Task 19, Task 20, Task 21, Task 22, Task 23, Task 24, Task 25, Task 26, Task 27, Task 28, Task 29, Task 30, Task 31, Task 32, Task 33, Task 34, Task 35, Task 36, Task 37, Task 38, Task 39, Task 40, Task 41
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Implementation slice](implementation.md#task-42); [design contract](design.md#outcome-and-scope)

- [ ] 42.1 Run kk:test over all implemented tasks: lock-based full unit matrix, required local integrations and relevant simulator paths. Mark unavailable optional live service checks explicitly and keep associated release gates open.
- [ ] 42.2 Run kk:document, kk:review-code with Python, and kk:review-spec against this design/implementation/task set. Record and resolve findings or persist actionable deferred items before declaring the affected phase ready.
- [ ] 42.3 Update architecture and configuration docs from the final implementation, preserving project identity and repository instructions; link final verification evidence and close each actual task checkbox.
- [ ] 42.4 Verify the acceptance cases in [Task 42](implementation.md#task-42) and record the result here.

**Scope / gate:** Design drafting is not independent implementation review. Committing, publishing and external messaging are separate actions; never commit .capy/knowledge.db.

## Dependency Graph

Arrows mean prerequisite → dependent. Lists on the left mean all prerequisites;
T01–T41 on the final line means every implementation/research task.

```text
(independent) --> T01
T01 --> T02
(independent) --> T03
(independent) --> T04
(independent) --> T05
(independent) --> T06
T06 --> T07
(independent) --> T08
T08 --> T09
T08, T09 --> T10
T10 --> T11
T10 --> T12
T10, T12 --> T13
T10, T11 --> T14
T14 --> T15
T15 --> T16
T15 --> T17
T16 --> T18
T16, T17, T18 --> T19
T19 --> T20
T17 --> T21
T19 --> T22
T22 --> T23
T05, T09, T20 --> T24
T22 --> T25
T25 --> T26
T26 --> T27
T21, T27 --> T28
T19, T20, T23 --> T29
(independent) --> T30
T09, T30 --> T31
T31, T23 --> T32
T10, T23, T12, T13 --> T33
T09, T23, T30 --> T34
T34, T22 --> T35
T09, T22, T26, T27, T29 --> T36
T23, T29 --> T37
T37 --> T38
T20, T31, T35 --> T39
T39, T24 --> T40
T40 --> T41
T01–T41 --> T42 (final verification)
```

## Deferred work register

- **Python floor:** the proposed correction is >=3.10. If an actual 3.9 consumer
  exists, Task 1 must record a supported dependency strategy before release;
  changing metadata alone cannot make incompatible dependencies work.
- **OpenCode Messages protocol:** models that require an unsupported protocol remain
  unavailable. Task 36 records verified supported entries; the next step is a
  dedicated adapter design and wire tests for Messages-only entries.
- **Jules archive/unarchive:** API support is unresolved. Task 38 must verify the
  methods and state transitions or retain an explicit API-validation follow-up.
- **Explicit provider caches / strict-prefix layout:** wait for Tasks 39–41's
  completeness and benefit evidence. Next step: a provider-specific API, pricing and
  quality assessment followed by separately bounded implementation tasks.
- **Live cassette/service validation:** after request formatting or optional-service
  implementation, re-record or smoke-test only against explicitly configured targets.
  Missing credentials keep that release gate open; do not fabricate recordings.
- **Prompt-preservation investigations:** web-search guidance, literal newline
  escapes in codereview, thinkdeep customization and workflow-history retention
  need reproduction through Task 39's request-capture harness before being classified
  as defects. These are investigation cases, not approved unrelated fixes. For each
  reproduced defect, record the failing scenario and create a separate bounded task.

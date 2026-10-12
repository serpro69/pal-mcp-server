# Design: server reliability and capabilities

> Status: implementation/offline verification complete; see final-verification.md for open release gates
> Created: 2026-10-10
> Scope: reliability, provider execution, model policy, diagnostics and optional integrations
> Implementation: [implementation.md](implementation.md)
> Tasks: [tasks.md](tasks.md)

## Outcome and scope

Improve PAL's correctness, concurrency, model selection and observability with new
features and focused improvements to the existing system. The audience is
maintainers implementing these changes and users invoking PAL from MCP coding
clients. Implementations are to be written from these behavioral requirements and
the repository's current interfaces.

Success means independent conversations cannot mix, each real turn is recorded
once, discovery and invocation enforce the same model policy, provider calls allow
other requests to advance, and failures remain visible without losing successful
answers. Existing tool names and safety controls remain stable. Performance and
cache benefits require measured results.

This document defines the product and architectural contracts. The companion
[implementation plan](implementation.md) assigns all 42 slices to repository files
and pairs every slice with acceptance checks. [tasks.md](tasks.md) records status,
dependencies and unresolved gates. Together these three documents contain the
requirements needed to implement the roadmap as a self-contained feature plan.

## Original system and preserved boundaries

The following describes the pre-implementation baseline. The final
[architecture](../../../architecture.md) describes the resulting system.

`server.py` registered tool instances, resolved models and rebuilt conversation
context. It dispatched into the same `TOOLS[name]` instance for every call.
Simple tools, workflow tools and consensus eventually invoke synchronous provider
methods from async handlers. Workflow state and some response metadata live on tool
instances. `utils/storage_backend.py` stores threads in process memory with TTL;
`get` and `setex` are individually locked, not a transaction spanning an invocation.

Providers use JSON capability registries. Configuration and provider lists are
repeated in the server, model listing and restriction code. File readers validate
paths against a deny policy; they are not confined to a single project directory.
Generated chat artifacts have a separate write path in `tools/chat.py`.

Existing contracts to preserve:

- `allow_edits`, `editable_paths`, safe/edit argument buckets, validation and the
  clink system-prompt ContextVar, covered by `tests/test_clink_safe_default.py`.
- Sensitive home-directory protection and symlink normalization in
  `utils/security_config.py`, including legitimate home subdirectories/macOS paths.
- Newer Gemini/OpenAI/OpenRouter entries and existing aliases.
- pip-tools sources and generated lock files, the manual release workflow, project
  URLs/version identity, and opt-in remote version checks.
- File-annotation compatibility: retain plain path-list inputs and all five embedding
  paths; include annotation text in rendered token budgets. The complete coordination
  contract is restated below. Implementing annotations is separate work.

## Delivery approach and rejected alternatives

Implement small behavioral slices with regression tests, preserving the current
toolchain and client contracts. A temporary provider invocation adapter permits
independent backend migrations and has an explicit removal task.

| Alternative | Reason rejected |
| --- | --- |
| One simultaneous provider rewrite | Combines concurrency, transport and shutdown failures into a change that is difficult to isolate or roll back |
| Keep blocking provider calls indefinitely | Prevents independent requests and progress reporting from advancing during network waits |
| Reuse shared mutable workflow instances | Resetting fields does not prevent state leakage across concurrent awaits |
| Treat every missing model catalog entry as a retirement | A secondary catalog can be incomplete; absence is insufficient evidence |
| Optimize prompts before measuring completeness | A smaller or more stable request can conceal missing turns or files |

## Roadmap

| Phase | Tasks | User-visible result | Exit condition |
| --- | --- | --- | --- |
| 0. Maintenance and containment | 1–9 | Safe file/CLI boundaries and trustworthy conversation records | Filesystem, storage and history regression tests pass |
| 1. Isolation and provider execution | 10–23 | Concurrent requests remain independent; providers yield; errors and model controls are accurate | Concurrency, provider wire-contract and shutdown tests pass |
| 2. Selection and progress | 24–29 | Predictable file limits, explicit model policy, smaller curated catalogs and live status | Discovery equals callable policy; progress is isolated |
| 3. Diagnostics and inexpensive end-to-end tests | 30–35 | Portable logs, opt-in transcripts/resources, compact tool surface and deterministic simulator CI | Installed-package and stdio simulator checks pass |
| 4. Optional external services | 36–38 | OpenCode Go and Jules available only when configured | Current API contracts validated; no effect when unconfigured |
| 5. Measured prompt efficiency | 39–41 | Cache telemetry and stable history rendering where worthwhile | Completeness first, measured stability/cost evidence second |
| Final verification | 42 | Coherent release-ready implementation and documentation | All phase gates and independent implementation reviews complete |

Phases indicate rollout priority. The task dependency graph controls actual ordering;
independent slices need not wait for an unrelated optional provider.

## D1: runtime and packaging

Retain MCP 1.x. Select a currently supported, security-patched release within that
major through dependency/API verification and pip-compile in Task 1. Verify
resources, progress notifications and explicit tool-error results against that
selected version's interfaces. The accepted capabilities must work without a
major-version migration.

Align declared Python support with the tested 3.10–3.12 matrix and dependency
requirements. The proposed floor is 3.10; this is a compatibility declaration change
to call out in release notes, not justification to mass-reformat annotations.
Keep Black/isort/Ruff and pip-tools. Rebuild and scan the existing pip-based
container recipe, preserve non-root execution, and record installed-package findings
and remediation status from that build. Restore canonical Apache 2.0 license text
and package the project's accurate existing notices. License maintenance does not
change project ownership.

## D2: file and CLI boundaries

Chat's write root is operator-controlled `PAL_WORKSPACE_ROOT`, defaulting to the
server working directory. A supplied working directory must resolve inside it
before a model call that can generate an artifact. Explain this default for
launchers whose working directory is the PAL installation rather than user code.

Write artifacts into a server-selected subdirectory with unique per-call names;
do not overwrite a shared `pal_generated.code`. Validate the workspace, sandbox
and destination independently. Reject a symlinked sandbox or destination and use
exclusive/no-follow creation where supported. A platform that cannot enforce the
specified operation must fail clearly. Treat the workspace owner as trusted:
this is not an OS sandbox against a hostile local administrator. Both policy and
write failures return explicit tool errors; never inline executable-looking
fallback instructions as a substitute for a failed artifact write.

Add pseudofilesystem/system-path exclusions without removing local sensitive-home
checks. Cap actual bytes read, not text characters or only stat-reported size;
decode after the bound is enforced. Apply the same path validation to token
estimation before stat. Existing normal files, permitted home code and macOS
symlinked paths must remain readable.

Build clink child environments from shared runtime variables plus target-vendor
variables and explicit operator config. Preserve HOME for CLI authentication;
environment filtering does not isolate credentials readable from disk.
Use configured working_dir, otherwise a per-call scratch directory. Test actual
CLI behavior: Codex may require a repository even with absolute input paths.
Retain all local edit/path controls across the new run wrapper. Recognize Gemini
UNSUPPORTED_CLIENT errors without matching successful answer text; link to current
vendor guidance rather than freezing an unverified account-policy explanation.

## D3: conversation ownership and truthful results

Distinguish absent/expired threads from storage corruption/outage using a typed
storage exception. Fail a continuation before the provider call if its required
history cannot be loaded or its input cannot be recorded. If the model has already
answered and persistence then fails, return the answer with an explicit
memory-persistence warning; do not claim the reply was saved.

Record the first input before generation, and each continuation input exactly once.
Record workflow `step` inputs as user turns and raw model responses as assistant
turns. Provider-free steps retain their actual local tool result as an assistant
turn with null model/provider provenance. Keep workflow state in metadata; never
store agent-facing artifact instructions as the model reply. Admit each conversation
invocation only when both its input and reply fit the turn cap. This also supports
service session IDs from turn one.
Task 8 establishes typed storage/turn-limit outcomes; Task 9 adds pair admission
with single-owner input recording; Task 11 makes that sequence atomic for concurrent
calls. Do not enforce pair admission independently at each legacy input writer.

Replace substring detection of server-built history with trusted internal request
context. Both MCP dispatch and direct tool invocation must work. Re-read the thread
after adding the new turn so newly attached files are included immediately.
Extract model-resolution/context-building helpers only where they simplify this
single-owner path; do not change explicit-model selection to a silent fallback.
Preserve directory expansion, prompt-file loading, original-input size checks,
cross-tool context and the separate per-file descriptions contract.

Instantiate a fresh tool per invocation. Persist and restore each workflow's
step-one configuration alongside work_history, including consensus roster and
responses and planner branches. Old thread metadata lacking tool_state defaults
safely; it cannot borrow state from another invocation. Reset direct reused tool
objects too. Serialize calls to the same continuation ID from reconstruction
through result persistence; unrelated IDs remain concurrent. Cancellation releases
the lock, and its registry does not grow forever.

Only a real attempted provider call sets model_used/provider_used; local analysis
and intermediate workflow steps leave them null. Label echoed input as caller
findings, not an external analysis. Provide a documented compatibility transition
for consumers of final_analysis. Failed consensus participants are excluded from
successful-response counts; partial and zero-success outcomes are explicit.
Consensus participants remain blinded: each sees the original proposal, its own
stance and explicitly supplied attachments, never earlier participant verdicts or
intervening caller summaries. Complete audit history does not imply complete history
in every participant's prompt.

## D4: async provider lifecycle

End state: awaited generation and retry delays, native async clients, and awaited
cleanup before the server event loop closes. Preserve provider-specific routing,
temperature omissions, Azure deployment names, DIAL headers, OpenRouter Responses
store behavior, image payloads and error translation.

A temporary invocation adapter allows small vertical migrations: it awaits a native
async generate_content or moves a legacy synchronous one to a worker thread.
Before enabling that offload in Task 14, protect shared provider initialization and
mutable caches used during generation. Replace OpenAI/Azure's process-wide proxy
environment suppression with explicit transport configuration, retaining the
effective proxy/TLS policy without modifying other requests' environment. Cold-start
tests must exercise actual provider initialization with fake SDK transports; a
delayed fake provider alone cannot establish thread safety.
First wire chat, then expert/consensus paths. Convert the OpenAI family, Gemini and
DIAL separately, then remove the adapter/fallback. This avoids requiring a large
all-provider commit. A cancelled worker-thread call may keep running during this
transition; do not advertise cancellation guarantees until native migration ends.
Mocks follow the active contract at each stage, rather than returning unawaited
AsyncMock coroutines to synchronous code.

Use the shared build/call/parse/retry pattern where it removes duplicated execution
logic. Do not combine it with provider metadata migration in the same task.
Explicitly close shared DIAL pools once and avoid constructing unused clients during
shutdown. Fire-and-forget atexit cleanup is not the success criterion.

All simple, retry, expert and consensus requests send an effective output limit
bounded by the registry ceiling, the response reservation and any lower caller
limit, using the endpoint's correct field. Task 20 establishes the matching input
budget before these limits are sent; Task 24 reuses it for file admission/rendering.
Normalize Responses usage separately from Chat Completions. Missing usage remains
unknown, not an invented zero.
Gemini generation/capability metadata selects thinking_level versus thinking_budget;
clamp levels only to a verified per-model accepted set. Unknown capabilities require
a diagnostic and explicit policy, not a silent ceiling bypass.

## D5: providers and model policy

Declare provider identity, labels, env keys, placeholders, required settings and
restriction names once on provider classes. Derive setup/help/listing/restrictions
and test isolation from those declarations. Keep custom endpoints key-optional,
DIAL credentials intact and Azure configuration distinct. Class construction and
configuration diagnostics must not print secrets.

No configured providers is an MCP-visible configuration state: listing, version,
and genuinely provider-free tools remain available. Malformed explicitly selected
or shipped registries raise file-naming errors; they must not masquerade as absent
credentials. An intentionally empty optional registry remains a separate case.

Apply the same policy to explicit calls, aliases, discovery, ranking and fallback:

1. Resolve the complete requested provider/model identity before interpreting a
   legacy option suffix. Exact canonical names and aliases, including colons, win.
2. Enforce that provider's allowlist, if present.
3. Apply global DISABLED_MODELS as a deny override, including canonical aliases and
   optional recognized provider prefixes. Colons in local model IDs remain valid.
4. Use enabled_by_default only for discovery. Disabled discovery entries remain
   callable by exact canonical name if otherwise allowed; that flag is not a deny rule.
5. In auto selection, the first available PREFERRED_MODELS entry precedes category
   ranking. It cannot bypass restrictions or replace an explicitly requested model.
   Canonical opt-in through an allowlist/preference can make a hidden entry discoverable.

Catalog audit and generation produce reviewable proposals. Distinguish confirmed
provider deprecation from absence in a community catalog, which only calls for review.
Generated candidates start hidden, with no invented aliases, ranks or capabilities.
Preserve existing entries and aliases unless current provider evidence establishes
a correction. Audit outputs require review before changing catalogs; scheduled
auto-merging and publishing credentials are outside this work.

## D6: budgets, progress and tool surface

Compute the effective file budget once from capabilities and remaining history
budget; use it at the admission gate and embedder. Diagnostics explain the binding
limit and largest files. Account for expanded paths, deduplication, rendered headers
and annotations. files_in_history counts only files actually present in the assembled
history, never files omitted because they were missing, denied or over budget.

Progress is request-scoped and opt-in through the client's progress token. Report
stages, elapsed time and available usage without prompt content or credentials.
No token means no notifications. Notification failure cannot lose a successful
result; cancellation stops heartbeat tasks. Do not promise that every client's
timeout resets on a notification.

Retain one tool name per capability. Measure schemas before shortening descriptions;
preserve required fields, safety wording and the clink opt-in controls. Remove only
proved-unused advertised fields and keep legacy input compatibility during a
documented deprecation period. Provider-free planner/tracer calls do not require a
model just because DEFAULT_MODEL is auto. Refresh affected prompt/schema fixtures.

## D7: state, transcripts and resources

Resolve PAL_STATE_DIR, then XDG_STATE_HOME, then the per-user fallback. PAL_LOG_DIR
overrides just logs. Server, shell/PowerShell log-follow commands, Docker mounts and
simulator readers share this resolution. Document migration from repo-local logs;
do not move/delete existing logs automatically. Correct rotation arithmetic.

Add JSONL transcripts as a separate audit/read channel after successful memory
writes. They are not a restart-recovery backend: TTL/in-memory continuation behavior
does not change. Proposed local default: PAL_TRANSCRIPTS=false, an explicit opt-in
because prompts and file references persist on disk. When enabled, use private
directories/files, validate UUID filenames, reject symlinks, and prune only eligible
transcript files with a documented retention period (30 days). Existing modes need
tightening; Windows requires an explicit access-control strategy, not Unix fchmod
assumptions. Failures are visible but cannot discard an already produced answer.

Expose pal://threads and pal://threads/{thread_id} through MCP 1.x resources, with
bounded listing/read size, deterministic ordering and clear missing/disabled versus
I/O errors. Malformed records are reported as skipped with counts. Prevent traversal,
noncanonical IDs and symlink escapes. Disabling transcripts hides both reads and
listings, even if old files remain. State directories are an operator trust boundary:
use separate PAL_STATE_DIR values for mutually untrusted projects/clients. No remote
multi-user transport or authorization layer is being added.

## D8: simulator and test reliability

Keep one MCP stdio server session alive for a complete multi-call scenario.
Subset runs count only selected tests; failures remain nonzero and do not erase the
summary. SIMULATOR_MODEL and a provider-agnostic selection let the same scenarios
run against a deterministic local stub or a configured local model.

The stub verifies HTTP routes, model IDs, request shape and unsupported streaming;
it never claims to validate model reasoning. Keep provider-specific routing/live
quality scenarios separate. Integrate the fast tier into this repository's existing
CI on PR and main push; local-model and paid integration tiers remain explicit.
Isolate all provider credentials based on metadata, not just two hand-written keys.

## D9: optional service integrations

OpenCode Go is a configured provider, not a default choice. Use its own key and
allowlist, capability registry and per-request session header. Create the thread ID
before the first request and reuse it across continuations. Do not use process-wide
fallback IDs or shared mutable headers. Send PAL's own user agent.

Verify each model's authentication, endpoint family, output limits and temperature
support at implementation time. Chat Completions, Responses and Messages are
distinct wire contracts. Initially enable only entries verified against an adapter
implemented in PAL; a Messages adapter remains a separately gated extension.

Jules is an action tool with requires_model=false, because background coding
sessions do not fit generate_content. Start with list_sources, create and status,
then message/approve controls. Paginate or expose next-page tokens rather than
silently dropping activity. Validate session IDs as one opaque path component.
Use explicit plan approval by default; AUTO_CREATE_PR is an explicit caller choice.
Its annotations must describe mutations accurately. Keys, HTTP failures and session
state transitions get dedicated tests. Do not retry session creation automatically
without a verified idempotency mechanism.
Task 38 verified archive/unarchive against official discovery revision 20261008
and implemented dedicated state-transition wire tests. The separate `archived`
flag does not imply cancellation. Live lifecycle validation remains gated on a
designated repository/session; see the resolved Task 38 contract in implementation.md.

## D10: prompt-cache work is measurement-led

First normalize and propagate cached-input/cache-write usage when provided, keeping
unknown distinct from zero. Add an offline request-capture/report harness. Fix the
already-observed history duplication in Task 9 before claiming cache savings.
Measure raw request completeness, stable prefixes and cross-process determinism.

Next remove volatile counters from the leading history, order set-derived prompt
content deterministically and test stance placement. A further file/history layout
change needs comparison with the per-file descriptions design and evidence that
new/current file content is never lost. Preserve provenance even if mtime presentation
moves out of cache-sensitive headers.

Compaction hysteresis and append-oriented rendering are conditional on the captured
baseline. Provider-specific explicit caches, pricing claims and strict-prefix layout
changes remain deferred until useful traffic and official API facts justify them.
A higher string-prefix ratio alone is not proof of lower provider cost or equal answer quality.


## Detailed compatibility contracts

The following details make the feature boundaries implementable without another
design document. Task-specific acceptance checks are in implementation.md.

### Filesystem and CLI contracts (Tasks 4–7)

- Resolve `PAL_WORKSPACE_ROOT` once from operator configuration; when absent use
  startup cwd. A supplied artifact working directory must exist and resolve within
  that root. Relative traversal, a linked artifact directory, a linked destination
  or an outside-root target is an error before generation where detectable.
- Use a server-selected artifact subdirectory and unique per-request filenames.
  Exclusive creation must never unlink an existing destination. Return the actual
  created path; retain raw model text in conversation memory even when presentation
  directs the caller to the artifact.
- Extend system exclusions to include `/proc`, `/sys`, `/dev` and `/run` and
  their descendants. Keep existing denied system paths and sensitive home
  subdirectories, including linked targets. Root/home-container checks must retain
  their existing exact-root versus descendant semantics so normal project paths
  remain usable.
- Validate before size estimation and opening. Read at most `max_size + 1` bytes,
  detect overflow from that extra byte, then decode; UTF-8 character counts and a
  potentially stale `stat` value are not the byte bound. Apply the bound to every
  file-reader entry point used by prompts, preserving each entry point's documented
  successful formatting and failure return contract.
- Build the child environment in this order: allowed shared runtime values,
  target-vendor values, explicit operator overrides. The shared set must cover
  executable lookup, home/auth configuration, temporary directories, locale, proxy
  and TLS settings, plus Windows process requirements. Vendor authentication names
  belong to that vendor's declared list; arbitrary inherited provider keys do not.
  Explicit overrides are an operator trust boundary and retain final precedence.
- A configured CLI working directory wins; otherwise create a private per-call
  temporary directory and remove it after success, failure or cancellation. Reap
  the child before cleanup. Preserve `allow_edits`, `editable_paths`, safe/edit
  argument buckets and system-prompt ContextVar reset through every wrapper.
  Verify each supported CLI can operate with its configured repository policy.
- Match `UNSUPPORTED_CLIENT` only in an unsuccessful Gemini CLI execution or an
  error envelope. Test stderr, non-JSON output, empty/error JSON and recovery paths;
  a successful answer quoting the marker remains a successful answer.

### Request ownership and error contract (Tasks 8–13)

The MCP request boundary owns this sequence: validate inputs and resolve the
conversation identity; acquire that thread's coordinator; load state and check
capacity for an input/reply pair; record the raw current input; rebuild the history
appropriate to the receiving tool, including current attachments; execute a fresh
tool; persist the raw reply and workflow metadata; release the coordinator in `finally`.
The initial thread exists before the first provider request. A user input is recorded
once even when its provider call fails; an absent reply is never synthesized.

`MAX_CONVERSATION_TURNS` continues to count stored messages, not invocation pairs.
For every invocation using conversation memory, require at least two free slots
before saving its input or executing provider/CLI work. This also applies to local
workflow steps, whose real tool result occupies the assistant slot with null
provider provenance. Fewer than two slots gives a limit result without appending
input or starting work; a cap of one therefore cannot admit a conversation call.
With the Task 11 coordinator held through reply persistence, the admission check
reserves capacity without placeholder turns. Failure/cancellation after input save
consumes only that input's slot; unused capacity is released without a synthetic
reply. Task 9 introduces this check once at the input-persistence owner, including
the direct-tool path, after eliminating duplicate input writers. Its trusted request
context records completed admission so nested tool execution and provider retries
do not repeat the check. Task 8 only supplies typed storage/turn-limit outcomes;
it does not independently introduce pair admission at the legacy writers. Between
Tasks 9 and 11, apply the rule to sequential calls without claiming concurrent
reservation. Unexpected post-answer storage failure still
returns the answer plus a warning.

Keep `remaining_turns` measured in free stored-message slots, computed after the
actual writes. User-facing remaining-exchange counts are `floor(free_slots / 2)`;
do not invite another invocation when fewer than two slots remain. MCP and direct
tool paths use the same admission rule; direct-call concurrency still requires the
coordinator as stated below.

Trusted internal context records which boundary already handled admission and
history/input persistence. It is not a request-schema field and caller JSON cannot assert it.
Direct tool execution must supply its own correct ownership path, and may not
claim MCP same-thread serialization unless it uses the coordinator. Reused direct
tool objects reset invocation state before restoration.

Persist configuration with the owning tool's workflow state, including
`review_config`, `analysis_config`, `git_config`, `refactor_config`,
`trace_config`, `stored_request_params`, planner branches, and the consensus
roster and responses. Cross-tool history remains available; one tool's state cannot
initialize another tool's configuration. Older metadata without `tool_state`
uses defaults rather than another request's values.

| Condition | Observable result |
| --- | --- |
| Unknown or expired continuation | Explicit continuation error; no model call |
| Corrupt/unavailable required memory or failed input save | Typed storage error with original cause; no model call |
| Fewer than two free turn slots before invocation | Explicit limit result; no input append or provider/CLI work, distinct from a storage outage |
| Provider failure after saved input | Provider error; keep the real input and do not invent an assistant turn |
| Answer generated, final memory save fails | Return answer plus persistence warning; do not claim successful saving |
| Transcript/progress side channel fails | Preserve the primary result and expose a diagnostic |
| Cancelled call | Release request state, heartbeat and coordinator; do not retry cancellation as a provider failure |

Provider-attempt metadata is request-local and names only attempted calls.
Intermediate, local-only and skipped expert paths return null model/provider
fields. Caller findings and external analysis remain distinguishable. If
`final_analysis` has compatibility consumers, retain a documented transition
mapping and test it; the old field must never imply an expert ran when none did.

Consensus returns requested, successful and failed participant counts, successful
provider coverage where known, and per-participant errors. All-success is complete,
mixed results are partial, and zero-success is failed. Neither completion path may
label partial/failed output as full or high-confidence consensus.

The consensus prompt contract is narrower than its persisted history. Persist the
raw original proposal separately from reconstructed history and restore it with
the roster on each fresh tool instance. Each participant receives that proposal,
its own stance/system instructions and the current call's explicit file/image
attachments under the existing path policy. Do not inject prior verdicts, later
`step`/`findings` summaries or generic conversation history, and do not deduplicate
participant attachments against history it cannot see. Persist these excluded turns
normally for the caller and later cross-tool use. Tasks 9, 10 and 39 must test both
required content and forbidden-content sentinels in captured participant requests.

### Provider execution and model selection (Tasks 14–28)

The final provider contract is an awaited `generate_content` plus awaited cleanup.
Retry waits yield; cancellation propagates. Each provider owns its clients and
declares whether a transport is shared or borrowed. Shutdown closes each allocated
transport once while its event loop is alive. Unused providers allocate nothing
during cleanup.

During Tasks 14–18, registry-cached providers are shared across worker threads.
Use per-provider synchronization for lazy client publication and shared mutable
initialization state, preserving DIAL's deployment-client ownership/locking. Publish
one successfully initialized client per owner; failed initialization must leave a
retryable state. Never hold the initialization lock through a generation request.
OpenAI/Azure transport construction, including fallback paths, must not temporarily
edit process environment variables. Verify proxy/TLS behavior and concurrent CLI
environment reads with synthetic values. Carry the explicit transport policy into
the native async migrations; remove only guards made unnecessary by their final
execution model.

Task 20 computes one per-provider-request budget in `ModelContext`. Let `C` be the
verified context capacity and `R` the response reservation selected by the existing
allocation policy (40% of `C` below 300,000 tokens; 20% otherwise). Set effective
output `O` to the minimum of `R`, the verified model output ceiling and any supplied
positive caller output limit. Reserve exactly `O`, send exactly `O` on the wire,
and derive input capacity as `C - O`. Invalid/nonpositive budgets or unknown required
capabilities produce an explicit error/policy diagnostic before provider I/O.
This does not introduce a new public output-limit parameter.

History, files, current input, system instructions, rendered headers and estimated
image tokens must all fit that input capacity, with a conservative estimation
margin. Rebuild or reject over-budget context before provider I/O using the existing
explicit omission/error policy; never claim estimator precision or assume that all
endpoints reject oversized requests identically. Compute this budget separately for
each consensus participant and recompute it if a retry changes model or payload.
Task 20 owns the total-request invariant even before Task 24 unifies the finer file
budget. Preserve image payloads, Azure deployment naming, DIAL auth/header handling,
OpenRouter Responses `store` omission and model-specific temperature omission. Parse input/output/total usage
from the selected endpoint; an absent field is unknown, not zero.

Task 20 also owns output-limit serialization in DIAL's separate payload builder;
changing only the shared OpenAI-compatible builder is insufficient. Select the
verified output-limit field independently of temperature support, including DIAL
deployments that omit temperature. Task 18 characterizes the old payload as a
migration baseline, not a requirement to preserve its missing output limit forever.
If a deployment's limit cannot be represented or verified, return an explicit
capability/configuration error before provider I/O rather than omit the limit.
Verify both dimensions: caller paths propagate `O`, and actual provider serializers
emit it. Fake-transport cases cover OpenAI Chat Completions/Responses, Azure,
OpenRouter's supported endpoint families, custom/xAI inherited paths, Gemini, and
DIAL deployments with and without temperature support. These tests preserve each
provider's routing/authentication and do not require a full model-by-caller product.

For Gemini, select budget or level using verified capability metadata rather than
assuming all models accept every SDK enum. Map PAL's ordered modes
`minimal/low/medium/high/max` into that model's accepted settings. Clamp to the
nearest supported lower level, or the lowest supported higher level when no lower
one exists. Invalid/empty supported-level declarations are configuration errors;
unknown request modes use the existing request-validation error path.

Provider declarations include stable identity, display label, environment variable
names, placeholder values, required/optional settings, restriction name and factory.
Use them in server registration, model listing, setup help, restrictions and test
credential isolation. Preserve optional custom API keys and Azure/DIAL-specific
configuration. Placeholder credentials must not activate providers.

Task 22 owns model identity from the MCP boundary through policy and wire dispatch,
including `server.parse_model_option`, provider lookup and custom routing. Normalize
outer whitespace, then try the complete identifier through registered canonical
names/aliases in the existing provider priority order. Use the full catalog for
identity lookup, before discovery visibility or allow/deny filtering; a hidden or
denied complete ID must not be reinterpreted as a different model plus an option.
Registered custom tags, DIAL IDs ending in `:0`, and registered OpenRouter suffixes
remain part of the canonical model name sent to the provider.

Only when the complete ID does not resolve may a terminal `:for`, `:against` or
`:neutral` be interpreted as a legacy option, provided its complete preceding model
ID resolves. Match those option keywords case-insensitively and trim surrounding
option whitespace. Preserve the separate option value; do not add new stance
semantics to tools that do not consume it. All other unresolved colon-bearing IDs
produce an actionable unknown-model error; never strip an arbitrary tag to call a
different base model. Operators using tags previously accepted through stripping
must register the exact tagged model or an intentional explicit alias. This is a
documented compatibility correction, with updated parser tests and release notes.

Pass the resolved provider/canonical identity consistently to capability lookup,
allowlists, denies, preferences and generation. Task 25's recognized provider prefix
belongs to deny-entry syntax: remove at most that prefix, then resolve the entire
remaining model ID through Task 22's canonical/alias lookup, without legacy-option
parsing. Discovery entries and preferences likewise use canonical/alias identities,
not the invocation-only option syntax. This adds no new provider-prefix syntax to
tool requests. Selection and discovery share that identity and policy implementation:

| Input/state | Required behavior |
| --- | --- |
| Explicit model or per-tool choice | Resolve that choice or report its error; preferences do not replace it |
| Complete colon-bearing canonical ID or alias | Resolve before legacy options, retain the canonical suffix on the wire, and apply policy to that same identity |
| Unresolved complete ID with terminal `:for`/`:against`/`:neutral` | Accept as a legacy option only if the complete preceding model ID resolves; policy rejection never triggers this fallback |
| Other unresolved suffix | Explicit unknown-model error; never silently call the base model |
| Provider allowlist | Validate through the provider's alias/routing rules, including custom/Azure models |
| `DISABLED_MODELS` | Deny canonical identities and alias-equivalent names after allowlists; deny wins |
| Scoped deny | Recognized provider prefix restricts scope; remaining colons belong to the model ID |
| `enabled_by_default` missing/true | Existing discovery behavior |
| `enabled_by_default=false` | Hide model and aliases from default discovery; exact canonical calls still enforce all restrictions |
| Hidden canonical model explicitly allowlisted/preferred | Discovery opt-in only; never overrides a deny |
| `PREFERRED_MODELS` with auto/fallback | Trim entries, retain first occurrence per identity, skip unavailable/denied choices, then use category ranking |
| No eligible model | Actionable configuration/selection error; no unintended fallback |

No providers configured must still allow discovery, version and genuinely
provider-free operations. An explicitly configured missing/unreadable/malformed
registry and a malformed shipped registry are configuration faults, not a
no-credentials state. An intentionally absent or empty optional registry is
handled separately. Error messages identify the relevant file without secrets.

Model audit output separates confirmed retirement, evidence of capability drift,
unverified candidates and review-needed omissions. Verify provider catalogs and
secondary catalog status fields independently; a community `deprecated` signal
requires review unless authoritative provider evidence confirms retirement.
Incomplete/network-failed audits cannot report a clean result. Candidate generation
is deterministic and idempotent, preserves handwritten entries, and emits a diff
with hidden entries and only verified capabilities, without invented aliases/ranks.

### File annotations and budget coordination (Tasks 9, 24, 33, 40–41)

File descriptions are an optional path-keyed presentation map alongside existing
plain path lists. This roadmap does not implement the map, but must preserve these
five routes to the common `read_file_content` renderer:

| Feed path | File input and annotation ownership |
| --- | --- |
| Simple chat prompt | Request file paths and request descriptions |
| Workflow final-step embedding | Request relevant files and request descriptions |
| Generic workflow expert analysis | Consolidated relevant files/history and consolidated descriptions |
| Debug custom expert context | Consolidated relevant files and descriptions |
| Consensus participant request | Request relevant files and descriptions |

Keys follow the same expansion/resolution rules as file paths; an exact file entry
beats an inherited directory entry, otherwise the deepest directory match wins.
Descriptions are sanitized to one delimiter-safe, length-bounded header line, and
their tokens count toward the file budget. Missing/empty descriptions preserve
existing output byte for byte; errors are not annotated. Workflow consolidation
retains step metadata, with later values winning for the same key. Do not add a
description-only parameter to fileless tools or clink's path-reference interface.

Descriptions are fixed on first embedding under the separate annotation contract;
changing one does not force a duplicate file embed. This roadmap must not introduce
cross-continuation annotation persistence as a side effect. If a proposed history
layout would change that contract, record a coordinated design change before coding.

Compute a single effective file allowance from the model's file/context capacity,
Task 20's effective output reservation and the remaining conversation budget. Do
not create another output reservation or count history withheld from a blinded
consensus participant against that participant's input budget. Pass the allowance
to both admission and rendering, including headers/annotations and
expansion/deduplication overhead.
Count a file in history only when its content is actually present in assembled
history; distinguish new content, historical content, missing, denied and
budget-omitted paths. Report binding limits, largest contributors and overage.

### Progress, state and transcript contract (Tasks 29–32)

A progress token, including numeric zero, enables a request-local reporter.
A missing token produces no notifications. Counters are monotonic and status
contains stage, elapsed time and available usage, never prompts or credentials.
Awaited provider/CLI work owns a heartbeat context that is cancelled and joined on
exit. A failed notification cannot replace a successful tool result.

Resolve state paths in this order: explicit `PAL_STATE_DIR`, then a PAL directory
under `XDG_STATE_HOME`, then a per-user state directory. `PAL_LOG_DIR` overrides
only the log directory. Server, launch scripts, containers and simulator readers
must consume the same resolver. Explicit invalid paths fail visibly; do not silently
write into the package directory. Existing repo-local logs stay untouched.
Rotation tests account for the active file plus the configured backup count.

`PAL_TRANSCRIPTS=false` is the default. When enabled, each canonical UUID thread
has a versioned JSONL audit stream: thread metadata followed by ordered turn events.
Events identify the thread, turn/order, timestamp, role, tool, raw content and
available invocation metadata; unknown usage remains unknown. Append only after the
corresponding memory write succeeds and serialize appends so concurrent writes
cannot interleave records. No transcript is read to restore continuation state.

Transcript directories/files require private access (owner-only permissions on
POSIX, an explicitly validated equivalent on Windows). Check existing permissions
as well as new files. Validate canonical IDs before constructing paths, enforce
containment and reject symlinks. Prune only recognized expired regular transcript
files with the documented 30-day retention; report pruning failures without
aborting service startup. Disabling recording also disables resource listing/reads
of retained files.

`pal://threads` lists bounded, deterministically ordered thread summaries;
`pal://threads/{thread_id}` returns a bounded transcript. Use explicit finite
byte/record/list limits, expose truncation or pagination, and report skipped
malformed records and interrupted final lines. The concrete limits, cursor format
and schema version must be recorded in Task 32 before publishing the resource
contract. Missing/disabled resources use resource-not-found; permission/storage
errors remain distinct. No URI may select arbitrary filesystem paths.

### Tool surface and verification infrastructure (Tasks 33–35)

Record tool names, parameter names/types/defaults/requiredness and serialized
description size before changes. Keep one name per capability and shorten redundant
prose without losing safety controls. Remove advertised fields only after tracing
their consumers; retain accepted legacy inputs for a documented compatibility
window. Include every systemprompt module in regression guards.

Use explicit provider-mock fixtures rather than autouse behavior selected by test
names. Derive environment isolation from provider metadata. Assert parsed response
objects instead of incidental JSON escaping or formatting, and distinguish real
routing/summary counts from rendering quirks.

The simulator owns one Python interpreter/process/MCP session for each complete
scenario and closes it after the last call. Selection modes are mutually exclusive,
`SIMULATOR_MODEL` controls compatible scenarios, and selected-test counts and
nonzero failure status survive summary generation. The strict loopback stub checks
routes, model names, messages and unsupported streaming. It serves deterministic
responses only for the provider-agnostic tier. CI runs that tier on PRs and main
pushes with external credentials removed and subprocesses always reaped.

### Optional service action contracts (Tasks 36–38)

OpenCode Go remains unconfigured by default. Declare its own key, allowlist,
capability registry and package data. Resolve the real conversation ID before the
first request; attach `x-opencode-session` per request and reset request context
afterward. Interleaved threads must never exchange headers. Enable only model/
endpoint combinations whose auth, temperature/output behavior and response parsing
have passed wire tests and a configured smoke check. Unsupported protocols remain
unavailable with an explicit follow-up.

Jules is an independent `requires_model=false` action tool. Its core surface is
`list_sources`, `create`, `status`, `message` and `approve`. Creation takes a
task prompt, an explicitly selected repository/source and branch context, plus
explicit plan-approval and PR-mode choices. Default to requiring plan approval;
automatic PR creation requires caller opt-in. Status reports session state and
paginated activities with continuation tokens when more data exists. Message and
approve operate on one validated session identifier and report resulting state
without polling indefinitely.

Treat session IDs as opaque single path components: reject empty, traversal,
slash/backslash, query and fragment injection before I/O. Missing key, auth failure,
rate limit, timeout, invalid state and unknown action are distinct errors.
Mutating actions have accurate MCP annotations. Do not blindly retry creation;
unknown remote outcomes need reconciliation unless idempotency has been verified.
Archive/unarchive are conditional on verified official methods and state semantics;
unavailable methods remain documented follow-up work.

### Prompt-efficiency acceptance contract (Tasks 39–41)

Capture requests through the real dispatcher using synthetic conversations covering
chat, files, workflow steps, consensus, model changes and at least 12 continuation
turns for layout/compaction experiments. First assert complete, nonduplicated content
for each consumer's input contract, fresh attached file contents, instruction
retention and truthful omission markers. For consensus, assert the original proposal,
stance and explicit attachments are present while prior verdicts, intervening
summaries and generic thread history are absent; verify the complete history
separately in storage. These intentional exclusions are not completeness failures.
Investigate web-search guidance, newline handling, customization hooks and workflow
history through captured requests before declaring additional defects.

Report per-invocation input/output and optional cached-input/cache-write usage,
grouped by provider/model with field coverage. Never double-count a workflow or
consensus aggregate as another provider call. Unknown usage is not a zero cache-hit
rate. Stable-prefix measurements use serialized requests and multiple process hash
seeds; retain baseline/candidate values and completeness assertions.

Move volatile counters away from leading history and deterministically order
set-derived files/symbols/images. Stance relocation needs behavioral regression
checks. Append-oriented history, changed-file presentation and compaction hysteresis
are experiments gated on measured churn and complete context. Compare unchanged,
added, touched and edited files under constrained budgets. Record a no-change
decision when the proposed layout fails the gate. Provider cache controls require
separate verified API support; prefix similarity alone cannot establish cost or
answer-quality improvement.

## Assumptions and decision gates

- This remains a single-user stdio deployment. Remote multi-user operation would
  invalidate the shared state-directory and resource trust model.
- Python 3.10 is an acceptable corrected minimum, consistent with current CI and
  MCP requirements. Confirm in Task 1 before changing published metadata.
- Existing tool constructors can be invoked per request; verify this for every
  registered tool in Task 10.
- Old threads need safe in-process compatibility, not resurrection after restart.
- PAL_WORKSPACE_ROOT is operator-controlled and its owner is trusted; document
  startup cwd before enabling artifact writes.
- Optional providers are useful but not preconfigured. Their live smoke tests need
  operator-provided credentials and explicit test targets.
- Transcript opt-in and unique artifact names are intentional defaults proposed
  by this draft; review them as user-visible changes before implementation.
- Cache improvements require preserved prompt content and measured benefit; no
  benefit threshold is assumed before the local baseline is measured.

## Not Doing

- Toolchain, release automation and publishing migration: retain existing workflows
  so feature changes can be verified independently.
- MCP 2.x migration: target the retained major's interfaces.
- Wholesale catalog replacement or removal based only on models.dev absence:
  preserve intentional configuration and require provider evidence.
- Replacing clink/read-path protections, remote version-check defaults or project
  identity: these are compatibility boundaries.
- Implementing file annotations: this roadmap preserves their integration contract.
- Persistent continuation recovery, remote multi-user authorization or an OS
  sandbox: the operating model remains single-user stdio with ephemeral memory.
- Automatic service activation, external PR creation or publishing: integrations
  are configured and invoked explicitly.

## Deferred decisions and reversibility

Tasks 36 and 38 retain explicit gates for unsupported OpenCode protocols and
live Jules lifecycle validation. Jules archive/unarchive now have verified offline
contracts. Task 41 records the gate for explicit/strict-prefix
caching. These are deferred work, not silently discarded features.
Keep each feature independently reviewable and record its verification evidence. Optional transcripts/services
can be disabled by configuration; state-format additions are backward-readable.
Async-interface and schema/response changes require release notes and coordinated
rollback of their callers. A cache-rendering rollback may lose cache hits but must
not lose conversation data. Service actions still require explicit invocation by their caller.

# Design review of per-file descriptions

**Readiness at `ddf97c6` — the shared header renderer can start.** The previous seven findings have
corresponding changes in the plans. The direct read-through below identifies additional cases to
settle before implementing continuation eligibility and workflow replay.

## Direct read-through

The design owns the public contract, the implementation plan names the code paths, and the checklist
orders six pending tasks. Their task numbering and source links agree. The feature maintainer owns
the following clarifications. The probes exercised the existing code with a local fake provider.

### Pending versus settled workflow labels

A local `thinkdeep` reference step was followed by `chat` sending the file, then a resumed
`thinkdeep` expert sending it again. If the first step stages “reference”, skipping new updates on
resume still leaves “reference” in the restored map. Specify that a pending label becomes settled
only if that workflow actually used it on the first committed embedding. If another tool embedded
the file first, render it plain on resume. Test this interleaving alongside same-workflow re-embedding.
[State restoration](../../../../tools/workflow/workflow_mixin.py#L119) · [Current merge instructions](./implementation.md#task-3--deliver-descriptions-to-generic-workflow-experts)

### Retention across requests

ADR 0001 describes the feature as introducing no cross-continuation annotation persistence. The WIP
retains sanitized text across separate MCP calls in the same workflow. Successful turns serialize
the full accumulated `work_history`. An earlier label can remain in a private transcript and be
copied into a later event after another step replaces it. Reconcile the ADR wording with the intended
same-workflow exception and specify whether superseded values belong in those events. Inspect both
steps' stored and emitted records. [ADR](../../../adr/0001-retain-conversation-history-layout.md) ·
[Workflow save](../../../../tools/workflow/workflow_mixin.py#L1170) · [Transcript emission](../../../../utils/conversation_memory.py#L449)

One scale consideration belongs with the retention decision. The 200-character cap bounds each value.
A single directory annotation covering 2,000 files can still contribute 400,000 characters of label
text to one step before counting paths. Subsequent snapshots can repeat it. State whether this cost
is accepted, or define how to limit the retained representation and what the caller observes at the
limit. Rendering and file token budgets apply later than a reference-only step's state write.

### Legacy inclusion evidence

A clink assistant turn can store a model identity and paths even though PAL handed the CLI references.
A legacy workflow reply can also store a directory from `request.relevant_files`. Expanding it at
replay time classifies newly created children as historical. Limit the fallback to assistant turns
from PAL file-embedding tools with evidence of a model attempt. Use concrete paths recorded at the
time of the exchange and specify what to do if that child list is unavailable. Test a clink-to-chat
handoff and a new child of a legacy directory. [Clink turn](../../../../clink/conversation.py#L310) ·
[Workflow turn](../../../../tools/workflow/workflow_mixin.py#L1188) · [User-turn attachment capture](../../../../utils/conversation_context.py#L249)

### Which paths may receive a label

A successful chat with only an explicit `prompt.txt` contained its text twice in the provider prompt
and saved the path as an attachment. The helper returns `None` for an empty remainder and the caller
keeps the original list. With a sibling attachment the prompt file was removed. With a directory alone
its `prompt.txt` was discovered as a regular attachment. Specify label eligibility by the role the
path played. Preserve existing no-map bytes and file tracking. Removing the existing duplicate would
be a separate behavior change. The expansion helper also retains missing paths for diagnostics. Test
that an unavailable key cannot enter replay state merely because it appears in the expanded list.
[Extraction](../../../../tools/shared/base_tool.py#L890) · [Caller](../../../../utils/conversation_context.py#L239) · [Expansion](../../../../utils/file_utils.py#L542)

## Previous recheck

Issue #12 asks for optional path-keyed labels, compatible file lists and deduplication, and token
accounting. The runtime source at `af874c5` on `main` has the same Git tree as the original review
revision, `a951d95`. The recheck traced the call sites and exercised the disputed assumptions with
local probes. All 89 existing tests covering file context, rendering, tool schemas, workflow state
and transcripts passed on that baseline. The dispositions below identify the document corrections
and the checks planned for the feature. [Issue #12](https://github.com/serpro69/pal-mcp-server/issues/12) ·
[Design](./design.md) · [Implementation plan](./implementation.md) · [Tasks](./tasks.md)

## Dispositions

| # | What the recheck established | Correction |
| --- | --- | --- |
| 1 | Continued chat renders even newly attached files through the history reader. Stored attachment references alone cannot prove successful embedding. | Wire both chat routes. Define path-only inclusion evidence, handling for legacy replies, and a mixed old/new provider-prompt test. [Task 2](./implementation.md#task-2--deliver-chat-annotations-on-the-first-request-and-on-continuations) |
| 2 | An in-memory probe retained the raw map in initial context. The thread-creation event omitted it. The successful workflow-turn event carried the supplied step metadata. Failed workflow state follows a separate memory write. | Exclude the raw map from initial context. Retain only effective bounded values for same-workflow replay and specify exactly what the existing opt-in transcript can contain. [Retention policy](./design.md#workflow-replay-and-retention) |
| 3 | Normalizing after three raw-key updates selected step two. A directory key applied to a union of current and historical siblings matched both. | Bind only eligible file keys per step, resolve precedence there, then merge across steps by canonical path. [Matching rules](./design.md#matching-and-rendering) |
| 4 | Draft 7 validation of the proposed property accepted string values and rejected a non-object, a numeric value and a null value. The dispatcher validates before request-model construction. | Use a single strict contract at both entry points and test the malformed cases. [Field contract](./design.md#field-contract-and-tool-scope) |
| 5 | The shared expert helper delegates to base-tool admission. The multi-file reader measures rendered blocks independently of the single-file estimate. Consensus performs normal consultations in its own loop and reports errors per participant. | Route annotations through those actual delegates. Check an annotation-only overage, zero outgoing calls for the rejected input, and the appropriate caller-visible error. [Task 1](./implementation.md#task-1--bind-descriptions-and-admit-the-rendered-files) · [Task 5](./implementation.md#task-5--deliver-request-local-descriptions-to-consensus-participants) |
| 6 | Docgen and tracer disable expert analysis. Their final-step file summary exposes counts. Chat builds its own schema even when the generic simple builder is unavailable. | Advertise the field on the ten tools with real prompt consumers. Opt in each tool with its working route and omit the unused simple-schema registration. [Tool scope](./design.md#field-contract-and-tool-scope) |
| 7 | The surface test compares complete semantic schemas, then checks aggregate size. A valid new optional property requires an explicit expected delta even when the byte ceilings hold. | Update the exact supported tool expectations in each public schema change and retain the aggregate ceilings. [Verification](./implementation.md#task-6--verify-the-public-surface-and-document-usage) |

## Original assessment

**MAJOR_GAPS** before revision. Three findings were high priority and four were medium. The entries below record the original evidence and recommendations. Links to the earlier plan text use its Git revision.

## P0 — Critical

None.

## P1 — High

### 1. A file first attached on a chat continuation loses its description · MISSING

Task 2 wires the map through `build_standard_prompt`. On turn two, `SimpleTool.execute` goes directly to `invocation_history`, which renders the current attachment together with historical files. A user who first supplies `b.py` and its description on turn two would get the file content without its annotation. The existing provider-capture test exercises this exact attachment path. **Confidence 10/10** from the branch in the entry point and the captured prompt assertion. [Task 2.5](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/tasks.md#task-2-chat-simple-tool-request-surface) · [Chat execution](../../../../tools/simple/base.py#L353) · [History reader](../../../../utils/conversation_memory.py#L878) · [Continuation regression](../../../../tests/test_workflow_file_context_counts.py#L74)

Pass the current description map to the history renderer with eligibility restricted to newly embedded files. Extend the existing offline test with an old file supplied again and a newly attached file. Assert each appears once in the provider request, only the new file gets its new annotation, and the description counts toward the same file budget.

### 2. The proposed persistence exceeds the stated retention boundary · INCONSISTENT

The design promises that descriptions stay out of conversation memory, yet Task 4 puts the original map in every workflow step's `work_history`. PAL saves this history as assistant-turn metadata. With transcripts enabled, successful turns also serialize that metadata to disk. Separately, new conversations copy request fields into `initial_context`, including fields with serializable values. A long or unrelated description can therefore survive even when its header is shortened or never rendered. **Confidence 10/10** from both storage paths. [Design exclusions](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/design.md#not-doing) · [New-thread input](../../../../utils/conversation_context.py#L269) · [Initial-context filter](../../../../utils/conversation_memory.py#L265) · [Workflow state](../../../../tools/workflow/workflow_mixin.py#L1170) · [Transcript event](../../../../utils/conversation_memory.py#L449)

Specify which bounded annotations the same workflow may retain for replay, what enters transcript events, and what a later tool can reuse. Apply the agreed bounds before saving. Verify initial context, saved step metadata, and an opt-in transcript using an overlong value and an unmatched key. Also verify replay from older state without the field.

### 3. Late key matching can annotate the wrong file or select an older description · TECH_RISK

Task 4 passes one consolidated map over the union of current workflow files and historical paths. A directory description intended for a current file can then label a historical sibling, contradicting Task 4.5. Raw-key merging also breaks “latest wins” for aliases. For successive descriptions keyed by `/work/a.py`, `/work/./a.py`, and `/work/a.py`, updating the raw dict and then normalizing picks the **second** value. **Confidence 10/10** from the source of the file union and a local check of the specified merge order. [Matching and precedence](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/design.md#directory-propagation--precedence) · [Task 4](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/tasks.md#task-4-cross-step-persistence--generic-expert-analysis-embedding) · [File union](../../../../tools/workflow/workflow_mixin.py#L398)

Resolve descriptions against eligible files while their originating step is known. Preserve direct-file precedence within the step and merge by canonical path in step order. Test a history-only sibling and the three-step alias example in the final expert prompt.

## P2 — Medium

### 4. The MCP schema rejects values the design promises to coerce · INCONSISTENT

The proposed map schema accepts string values. PAL validates the advertised JSON schema before constructing a Pydantic request, so a numeric value, a null value, or a string in place of the map produces an MCP input error before the new validator runs. **Confidence 10/10** from the dispatcher order and a local check using the proposed schema. [Field contract](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/design.md#field-placement--mirror-the-path-field-per-family) · [MCP dispatcher](../../../../server.py#L1349)

Choose one public contract. The simplest is a string-valued map with ordinary MCP validation errors. If direct Python callers deliberately receive more permissive handling, label and test that behavior separately. Exercise a valid and malformed map through MCP.

### 5. The expert-file task targets a removed call and verifies the wrong budget measure · TECH_RISK

Task 4.4 instructs the implementer to pass the map to a `read_files` call in `_force_embed_files_for_expert_analysis`. That function now delegates to `_prepare_file_content_for_prompt`, which owns model-specific admission, the file report and the oversize error. Task 1.7 checks the single-file estimate, but `read_files` discards that estimate and charges the rendered block using the selected model's estimator. **Confidence 10/10** from the current call sites. [Task 4.4](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/implementation.md#task-4-expert-analysis) · [Expert delegate](../../../../tools/workflow/workflow_mixin.py#L460) · [Shared admission](../../../../tools/shared/base_tool.py#L977) · [Block measurement](../../../../utils/file_utils.py#L602)

Thread the map through the existing delegate. Test a request that fits without an annotation and exceeds the allowance with it. Verify the existing `code_too_large` result and absence of a provider call. Preserve consensus's participant-specific budget and its exclusion of conversation history. Consensus has its own step loop, so Task 4's “single call site covers every tool” claim also needs a request-local consensus exception. [Consensus step loop](../../../../tools/consensus.py#L552) · [Participant file budget](../../../../tools/consensus.py#L651)

### 6. Two advertised workflows have no model-visible destination for annotations · TECH_RISK

The scope includes `docgen` and `tracer` on the premise that their files reach a generic expert. Both disable expert analysis. The shared final-step response reports file counts and status without returning the rendered headers. A caller can therefore supply a valid map without any reader receiving the promised intent. Task 2.1 also adds an entry that the design itself identifies as unused by current simple tools. **Confidence 9/10** from the workflow overrides and response builder. [Tool scope](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/design.md#tool-scope) · [Docgen](../../../../tools/docgen.py#L284) · [Tracer](../../../../tools/tracer.py#L304) · [Response metadata](../../../../tools/workflow/workflow_mixin.py#L839) · [Decision D5](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/design.md#resolved-design-decisions)

List the tools with an actual annotation consumer. Exclude the two self-contained workflows unless a caller-visible output is explicitly designed for them. Use the first chat slice to exercise a caller-visible path, and add each workflow schema alongside the provider prompt that consumes it.

### 7. The task list omits the public-schema compatibility check · MISSING

The plan requires a green quality suite after each atomic task. Tasks 2 and 3 change advertised properties. The tool-surface test checks the full semantic schema of every tool against explicit expectations and separately enforces aggregate size limits. The planned per-tool assertions can pass while that required check fails. **Confidence 10/10** from the equality assertion and documented baseline policy. [Implementation order](https://github.com/serpro69/pal-mcp-server/blob/af874c51ac9bfdca2c9a39f79d9dd807d0ab01a7/docs/feat/wip/per-file-descriptions/implementation.md#implementation-order--rationale) · [Schema comparison](../../../../tests/test_tool_surface.py#L49) · [Baseline policy](../../../tool-surface.md#tool-schema-compatibility-and-measurement)

Add the expected optional property for precisely the supported tools in each schema-changing task. Keep the exclusions and size checks, and run the tool-surface test with those tasks.

## P3 — Low

None.

## Clean areas and verification

The path-keyed map gives callers a way to express intent without changing file-list identity. The existing successful-file header is an appropriate render point. The plan covers delimiter sanitization, empty descriptions, error headers, and byte compatibility when annotations are absent. The task list includes dependencies, sizes and parallel-work markers.

Thirty existing file-context, embedding, and tool-surface tests passed on the reviewed revision. They establish the baseline behavior. The continuation, retention, and alias examples above identify checks the implementation will need.

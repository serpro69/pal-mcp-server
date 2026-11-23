# Implementation plan for per-file descriptions

> [Design](./design.md) · [Tasks](./tasks.md) · [Review](./review-design.md)
> Created 2026-06-06 · Revised 2026-11-23

Use the existing renderer, conversation lifecycle and schema builders. The first task establishes the
shared render and admission contract. Each subsequent tool advertises the new property in the same
commit that connects it to its provider prompt. Run the repository quality checks at each commit. Symbol
names below refer to `af874c5`.

## Task 1 — Bind descriptions and admit the rendered files

In `utils/file_utils.py`, add one helper that binds the caller's map to an eligible set of expanded,
canonical file paths. Share the current path-validation and expansion rules. The helper resolves
supplied keys and compares path components, gives direct matches priority over the deepest directory
match, and returns sanitized exact-file entries. Ignore unmatched keys and suppress annotations that
clean to empty. A successful block appends the bracketed annotation immediately before the final
delimiter on the BEGIN line. The named 200-character limit includes the ellipsis. Preserve unannotated
bytes and unsuccessful-read diagnostics. [Matching and rendering](./design.md#matching-and-rendering)

Extend `read_file_content`, `read_files`, and `BaseTool._prepare_file_content_for_prompt` with optional
keyword-only inputs. At the reader, use an exact lookup after normal expansion. Keep `included_files`,
line numbering, sorting, reserves, the model estimator and the shared report. The renderer can
defensively reapply the idempotent sanitizer. Empty input should add no resolution work. Update the
corresponding mixin signature for the shared preparation call, preserving its existing arguments and
callers.

Verify with two files in sibling directories, a nested directory override, an explicit file override, a
path alias, unsafe delimiter text and whitespace-only input. In `tests/test_file_size_gate.py`, arrange
for the unannotated file to fit and its annotated block to exceed the same model allowance. Check the
reported limit, overage and canonical path. Test absence, error, missing-file and oversized-file headers
against the existing output.

## Task 2 — Deliver chat annotations on the first request and on continuations

Define the optional string-map property once for reuse. Add it to `ChatRequest`,
`ChatTool.get_input_schema` and `get_tool_fields`. Keep it out of the generic simple-tool schema. Use an
empty default factory and strict JSON string values. In the first-call
`SimpleTool.build_standard_prompt` path, pass the effective annotations through shared preparation for
the actual request files. In `conversation_request`, exclude the raw field from the copy passed to
`create_thread`. Keep the live request available to the renderers. The wrapper already removes a
consumed `prompt.txt` and expands attachments. Bind against that eligible set, preserving the original
directory keys long enough to resolve inheritance.

On a continuation, `SimpleTool.execute` calls `invocation_history`. Bind the current request's
descriptions before the history reader combines current and historical attachments. Supply the
exact-file map via an optional keyword on `build_conversation_history` to its normal `read_files` call.
Keep the override reader's existing calling convention and preserve text-only and expert history modes.
Pass the same report and effective allowance through the continuation's omitted-current-file check. A
currently attached file can be counted in `files_in_history` and still be new to the model.
[Chat continuations](./design.md#chat-continuations)

For the prior-inclusion check, use the earlier turns in the flattened parent chain. Record a path-only
list in existing successful model-turn metadata for the final prompt. Use `report.included` as exposed
by the actual renderer (`_actually_processed_files` for fresh/expert/participant blocks and
`invocation.history_files` for chat history). Observe the prompt used for the successful call. Discard
preflight or failed-call observations. Integrate with the existing simple and workflow turn writers so a
later chat can also recognize a file delivered by another tool. Delimit evidence from legacy data with
an explicit empty list for new model replies that contained no files. Reference-only turns contribute
nothing. For a legacy model-backed reply, use its stored file references conservatively. Keep this
annotation-eligibility check separate from the file-selection and admission algorithms. Do not add a new
turn, a second storage write, or store any chat label.

Extend the fake-provider capture in `tests/test_workflow_file_context_counts.py`. On turn two, resupply
an old file under another spelling and add a fresh file with a description. Assert both contents occur
once, only the new file receives the new annotation, order/counts are preserved, and the old label is
never replayed from chat storage. Also cover an earlier reference or unavailable file that was never
delivered, a legacy reply, and an annotation that makes the continued request exceed its file budget
before provider dispatch. Exercise valid, omitted and malformed JSON through the MCP entry point and
direct request validation. Add the precise chat property expectation to `tests/test_tool_surface.py` at
this step.

## Task 3 — Deliver descriptions to generic workflow experts

Cover `analyze`, `codereview`, `precommit`, `refactor`, `secaudit`, `testgen` and `thinkdeep`. Use a
shared file-aware workflow request derived from `WorkflowRequest` for the participating tools. Add the
reusable property to their existing tool-specific schema inputs when enabling them. Keep the global
workflow field map free of automatically advertised annotations. Keep the behavior in the shared
workflow mixin, with each tool contributing its schema registration.

In `BaseWorkflowMixin.execute_workflow`, bind annotations to **this step's** expanded
`request.relevant_files` before appending its `step_data`. Store only a nonempty effective map with
eligible canonical file keys and sanitized, length-capped values. Skip changes for files already
delivered to the model, retaining any settled same-workflow label. Merge by canonical path in
`_update_consolidated_findings`. The existing `_reprocess_consolidated_findings` replays steps in order.
An absent field contributes nothing. Intermediate steps can collect intent without reading file contents
merely to annotate them. Limit persistence to same-workflow replay as specified in the
[retention table](./design.md#workflow-replay-and-retention).

Pass the consolidated exact map to `_embed_workflow_files` for the final-step file set and via
`_prepare_files_for_expert_analysis` → `_force_embed_files_for_expert_analysis` →
`_prepare_file_content_for_prompt` for the actual expert set. Generic experts can receive the union of
workflow and history paths. Resolve directory inheritance before forming that union. The delegate keeps
its `continuation_id=None`, model context and shared budget. Expert history suppresses its separate file
body, and the preflight report is reset before the final expert render.

Verify a real two-step continuation through the dispatcher with a captured expert request. A step-one
directory description should reach the requested child. A historical sibling should appear with a plain
header. Update one file across three steps using the original spelling, an alias, then the original
again. Check the final pre-embed value in the provider prompt. Re-embed after a successful expert call
and verify a proposed replacement leaves its settled label intact. Inspect old-state replay,
failed-state persistence, the successful turn metadata and a real opt-in transcript. Check bounded
effective text, absence of raw/unmatched values, and isolation on cross-tool handoff. Update the exact
schema expectations for the seven tools in this task.

## Task 4 — Deliver descriptions in debug's custom expert block

Opt in `DebugInvestigationRequest` and the debug schema. In
`DebugIssueTool.prepare_expert_analysis_context`, pass the consolidated map to the existing
`_prepare_file_content_for_prompt` call for “Essential debugging files”. Use the shared step/replay
behavior from Task 3 and the final expert report for inclusion evidence. Capture the provider prompt and
verify one annotated file in that custom block after a continuation. Preserve the path order, line
numbers, counts and no-description output. Update the debug schema expectation with this change.

## Task 5 — Deliver request-local descriptions to consensus participants

Opt in `ConsensusRequest` and the consensus schema. The usual consultation branch of
`ConsensusTool.execute_workflow` owns its step loop. In `_consult_model`, bind only
`request.file_descriptions` against that request's explicit `relevant_files`. Preserve
`continuation_id=None`, `blinded=True`, the original proposal, the current participant's model context
and remaining-input allowance. Keep annotations out of consensus step state, including the fallback to
the shared workflow executor. Participant-specific results continue to use the existing success/error
envelope. A successful participant can supply path-only inclusion evidence for later chat. This evidence
never filters another participant's attachments.

Extend the existing blinded two-participant test. Reuse a file with distinct descriptions for each
participant and verify each captured prompt contains only its own description. With the second
description omitted, its header must be plain. Check the over-budget participant sends no provider
request, retains its error status, and cannot be charged for generic history. Update the consensus
schema expectation with this change.

## Task 6 — Verify the public surface and document usage

Run the ten supported schema expectations together. Check absence on every other tool, including
`docgen`, `tracer`, `planner` and `clink`. Preserve the existing semantic baseline, aggregate byte
ceilings and prompt hashes. Express intentional changes in the test's per-tool expected schema. Run the
repository quality suite and the targeted provider-capture, state, transcript and file-budget checks.
Assertions about a provider seeing an annotation belong on the captured outgoing request.
[Tool-surface policy](../../../tool-surface.md)

Document the field with one multi-file example, its supported tools, directory/direct precedence,
first-embed behavior, same-workflow retention and consensus's per-call scope. Complete code and spec
review before moving the feature to `done`. The [task record](./tasks.md) contains the final verification results.

# Design: Per-file descriptions / annotations for tool file inputs

> Status: implemented
> Created: 2026-06-06 · Revised: 2026-11-23
> [Source issue #12](https://github.com/serpro69/pal-mcp-server/issues/12) · [Implementation](./implementation.md) · [Tasks](./tasks.md) · [Review and dispositions](./review-design.md)

## Summary

Add an optional, path-keyed map `file_descriptions` to tools that send file contents to a model. Each
description goes into the corresponding successful file header. A caller sending twenty files can
identify the module under review and label the others as references. Today that intent must be spelled
out in the separate `prompt` or `step` field. Workflow `relevant_context` describes symbols.

## Goals

- A description appears next to the file it describes in the provider request, including when a chat continuation introduces the file or an earlier workflow step identified it.
- Keep path lists, directory expansion, ordering, deduplication, and the meaning of file-context counts. With no effective description, the rendered bytes remain identical.
- Keep each annotation on one delimiter-safe line. Count the complete rendered header in the existing model-specific file and input allowances.

## Field contract and tool scope

`file_descriptions` is an optional JSON object with string values. Omission and `{}` mean no
annotations. For example,
`{"/work/service.py": "module under review", "/work/tests": "reference tests"}` accompanies the normal
attachment list. A key alone cannot request a read. File keys are absolute paths, with the same home
expansion and safe path resolution used for matching directory keys.

Advertise `type: object` and `additionalProperties: {type: string}`. Keep the field out of `required`.
Use a string dictionary with an empty default factory on the participating request models. A null map, a
non-object, and null or numeric values are invalid for both MCP and direct JSON-shaped requests. PAL
already checks the advertised schema before constructing the request model. Omit the previously proposed
coercion validator. [MCP validation](../../../../server.py#L1349)

| Consumer | File source | Where the description is used |
| --- | --- | --- |
| `chat`, first request | `absolute_file_paths` after prompt-file extraction | `build_standard_prompt` → shared file preparation |
| `chat`, continued request | Current attachments rendered with older files, newest first | `invocation_history` → `build_conversation_history` → `read_files` |
| `analyze`, `codereview`, `precommit`, `refactor`, `secaudit`, `testgen`, `thinkdeep` | Step `relevant_files`, then consolidated files plus history | Final-step preparation and the actual generic expert prompt |
| `debug` | Consolidated `relevant_files` | `prepare_expert_analysis_context` builds the essential-files block |
| `consensus` | The current participant request's explicit `relevant_files` | `_consult_model` builds a blinded prompt with that participant's budget |

Only these ten tools advertise the field. `docgen` and `tracer` can produce file-context counts, but
neither sends the rendered files to an expert or returns their headers to the caller. `planner` has no
attachment input. `clink` passes path references to a CLI. An expert-disabled step of a supported
workflow may collect descriptions for a later expert call in that workflow.
[Docgen](../../../../tools/docgen.py#L284) · [Tracer](../../../../tools/tracer.py#L304) ·
[Workflow response](../../../../tools/workflow/workflow_mixin.py#L839)

## Matching and rendering

1. Bind descriptions to the current request's eligible expanded files before adding historical files or
merging steps. Reuse the existing path policy, expansion and alias deduplication. Resolve keys with home
expansion and `resolve_and_validate_path`. Ignore invalid, inaccessible or unmatched keys. An empty map
takes the existing fast path.
2. Match canonical paths. A direct key wins over a directory key, followed by the deepest keyed
ancestor. Compare path components. Thus `/work/pkg` never covers `/work/pkg2`. Apply directory values
only to eligible children in this request. For two spellings of the same key in one JSON object, prefer
the canonical spelling, then the lexicographically first spelling. This keeps the result independent of
member order. Return exact canonical **file** keys. A later reader of the resulting map performs exact
lookups only.
3. Clean the selected value before either storage or rendering. Collapse whitespace and control
characters to single spaces, trim, replace closing brackets and delimiter runs (`---`), and cap at 200
characters including a final ellipsis when truncated. Empty effective text produces no annotation. Use
the same idempotent sanitizer at the successful-header boundary for direct calls.
4. Insert ` [description]` before the closing ` ---` on the BEGIN line. Preserve the END line,
direct-code block, error, missing-file and too-large headers. Omitted files receive no rendered
annotation. Annotation input never changes which paths the caller requested.

```text
--- BEGIN FILE: /work/service.py (Last modified: 2026-11-23 08:00:00 UTC) [module under review] ---
```

`read_files` measures the completed block with its supplied model estimator.
`BaseTool._prepare_file_content_for_prompt` passes the same `FileReadReport` into
`check_total_file_size`. Keep that path for expert calls too. An annotation that pushes a required
attachment over the limit is subject to the same overage, omission, and coverage rules as additional
file text. [File measurement](../../../../utils/file_utils.py#L597) ·
[Shared admission](../../../../tools/shared/base_tool.py#L977)

## Chat continuations

The conversation wrapper saves the current user turn, including its attachments, before the continuation
prompt is rendered. Consequently a file first attached on turn two is already in the history reader's
file list. Pass that request's **effective exact-file map** to the existing history read. Keep
newest-first order and a single copy of each file. Historical-only paths receive no annotation. A caller
cannot relabel an already embedded file by resubmitting it under another spelling.
[Chat dispatch](../../../../tools/simple/base.py#L353) ·
[History assembly](../../../../utils/conversation_context.py#L141) ·
[History reader](../../../../utils/conversation_memory.py#L870)

Distinguish a prior **committed model prompt** containing the file from a prior reference, omitted file,
or unavailable file. PAL currently stores requested paths on turns and exposes the actual included list
during each invocation. To make that distinction available on later chat calls, retain only the
canonical paths actually included in the successful model exchange in its existing turn metadata. Use
the final prompt's included paths, including history-rendered files. A preflight read, a reference-only
workflow step and a failed invocation cannot establish prior delivery. These path-only observations
serve annotation eligibility. File selection, `ConversationTurn.files`, budgets, and coverage counters
keep their existing roles. [Stored chat paths](../../../../tools/simple/base.py#L734) ·
[Invocation-local file evidence](../../../../tools/shared/base_tool.py#L726)

For a continued chat, subtract prior committed inclusions across the thread and its parents from the
current attachment candidates, excluding the newly saved user turn from that comparison. A path
previously unavailable can receive a description when successfully supplied again. Older model-backed
turns predate actual-inclusion metadata. For those turns alone, conservatively treat their recorded
attachments as already introduced. Legacy history-only paths therefore cannot acquire new descriptions.
A previously unavailable path in a legacy model reply may also remain unannotated. New-turn metadata
distinguishes a known empty list from a legacy unknown.

## Workflow replay and retention

A workflow can collect relevant files in a reference-only step and send them to an expert on a later
invocation. After validating each supported step, retain only its effective, sanitized exact-file map
for that step's own expanded `relevant_files` (at most one 200-character value per eligible file). Store
it only when nonempty in `step_data` before appending to `work_history`. Restore via
`_update_consolidated_findings` and the existing chronological replay. An absent field on an older step
means an empty contribution. Until a file's first committed expert embedding, the latest effective value
for its canonical path wins. Omission or blank text adds no override. A forced re-embed in the same
workflow uses its settled label. Use the prior included-path evidence for this check. A reference-only
step does not freeze a label. A history-only sibling gets no inherited directory annotation. Apply the
consolidated map to both final-step preparation and the actual expert rendering so admission measures
the same annotations on shared files. Debug uses the same map in its custom block.
[Step processing](../../../../tools/workflow/workflow_mixin.py#L726) ·
[Expert file union and delegate](../../../../tools/workflow/workflow_mixin.py#L395)

| Destination | Data permitted |
| --- | --- |
| Initial thread context | Exclude the request's `file_descriptions` when creating the thread. Other initial request fields keep their existing semantics. |
| General conversation history or another tool | No reusable annotation text. Chat's record of prior successful inclusion contains paths only. |
| Same-tool workflow replay | Only the matched, sanitized file values in existing workflow state. Restoration is scoped to the saved tool and workflow. Starting step one resets it. |
| Optional private transcripts | A successful workflow turn already serializes its model metadata. Only its effective workflow map may appear there. Chat and consensus supply no structured annotation text to transcripts. A thread creation event contains no initial request payload. Ordinary user input or model replies may of course contain the same words. |
| Consensus participant | Only that request's matched descriptions. Use its separate consultation loop and preserve `blinded=True`. A later participant needs its own files and descriptions. |

The raw map, unmatched keys, and truncated suffixes have no place in stored request or workflow
metadata. Failed workflow-state saves on the user turn use the same sanitized representation. Preserve
existing persistence errors and transcript-event semantics.
[Initial context](../../../../utils/conversation_memory.py#L265) ·
[Workflow persistence](../../../../tools/workflow/workflow_mixin.py#L1170) ·
[Success event](../../../../utils/conversation_memory.py#L449) ·
[Participant prompt](../../../../tools/consensus.py#L643)

## Assumptions

- File intent is presentation metadata. Adding the property to a tool is useful only when its successful provider prompt can carry the annotation.
- Existing admission and history ordering remain authoritative. A description may legitimately change whether a rendered file fits.
- The single-line header remains compatible with existing delimiter consumers. `prompt.txt` is consumed as prompt input before annotation matching.

## Not Doing

- Structured path objects would change existing callers and file identity. Files and directories remain strings.
- Images, CLI path references, reference-only `files_checked`, and diagnostic headers have no successful text-file block to label.
- General cross-tool label reuse, file snapshots, a version log, and a new transcript format are outside the feature. Workflow replay uses the existing state lifecycle. A directory-as-unit display would separate intent from each embedded file.

## Rejected alternatives

Resolving directory keys after combining file histories can label a historical sibling. Binding the map
while the originating request is known preserves file ownership. A separate read to estimate annotation
cost would duplicate file I/O and could measure different bytes if a file changed between reads. The
existing report already measures the rendered blocks.

## Revision notes and verification

The June draft established the path-keyed map, sanitized file headers, directory inheritance, and
coverage of expert and participant prompts. The October decision retained the existing file layout and
first-embed convention. The November recheck corrected the continuation route, storage boundaries, and
actionable tool list. The [review record](./review-design.md) traces all seven findings to the revised
tasks. [History layout decision](../../../adr/0001-retain-conversation-history-layout.md)

Use offline captured provider requests to verify first and continued chat, a multi-step expert workflow,
debug and two blinded consensus participants. Pair the annotated cases with byte-identical
no-description rendering, an allowance-boundary case, inspection of stored state and an opt-in
transcript, and the repository's exact tool-surface regression.
[Implementation checks](./implementation.md)

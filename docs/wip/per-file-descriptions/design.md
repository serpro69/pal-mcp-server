# Design: Per-file descriptions / annotations for tool file inputs

> Status: draft (revised after design review — see §Revision notes)
> Created: 2026-06-06
> Source issue: https://github.com/serpro69/pal-mcp-server/issues/12
> Implementation: [./implementation.md](./implementation.md)
> Tasks: [./tasks.md](./tasks.md)

## Summary

Add an optional, path-keyed map `file_descriptions: dict[str, str]` to the tools that
embed file content. Each path's description is **sanitized to a single header-safe line**
and rendered into that file's existing `--- BEGIN FILE: ... ---` delimiter header, so the
model receives per-file *intent* ("module under review", "reference-only") right next to
the content.

The map is **additive and presentation-only**: the path-list parameters stay `list[str]`,
so deduplication, conversation-memory tracking, and directory expansion — all of which key
on plain path strings — are untouched.

## Motivation

When many files are sent at once, the model gets path names + raw contents and nothing
about *why* each file is there. Today the only channel for that is the free-text
`prompt` / `step` field, physically separated from the path list, so the path-to-intent
mapping is implicit and degrades as file count grows. The workflow field
`relevant_context` does not fill this gap — it targets **symbols** (`Class.method`), not
files.

## Goals

- An optional `file_descriptions` map can be supplied to `chat` and to every workflow tool
  that embeds files, keyed by absolute path.
- When a path has a description, it appears in that file's `--- BEGIN FILE ---` header —
  in **all** embedding prompts the model actually sees, including workflow **expert
  analysis** and consensus model consultations, not only the final-step embed.
- Descriptions are sanitized so they can never corrupt the delimiter structure.
- When a path has no (effective) description, output is **byte-for-byte unchanged**.
- Deduplication, directory expansion, and continuation file-tracking are unchanged.
- Description text is accounted for in token-aware truncation.

## Current architecture (verified against `main`)

File parameters differ by tool family but are all plain `list[str]` of absolute paths.
There is **one render point** but **several feed paths** — the original design missed the
latter, which the review corrected.

### Entry families

- **Simple tools** — `absolute_file_paths`, declared on the per-tool request model
  (`ChatRequest`, `tools/chat.py:46`). Read via `SimpleTool.get_request_files`
  (`tools/simple/base.py:232`).
- **Workflow tools** — `relevant_files` / `files_checked` on `WorkflowRequest`
  (`tools/shared/base_models.py:114-115`). Read via `get_request_relevant_files`
  (`tools/workflow/workflow_mixin.py:969`).

### The single render point

Every embedded file is formatted by `read_file_content` (`utils/file_utils.py:421`); the
header is built at `file_utils.py:506-510`, and per-file token estimation
(`file_utils.py:511`) happens *after* the header — so header content is automatically
counted in truncation.

### The feed paths (all reach `read_file_content`)

| # | Path | File source | Code |
|---|------|-------------|------|
| 1 | Simple-tool prompt | `request.absolute_file_paths` | `build_standard_prompt` `simple/base.py:780` → `_prepare_file_content_for_prompt` `base_tool.py:999` → `read_files` `file_utils.py:523` |
| 2 | Workflow final-step embed | `request.relevant_files` | `_handle_workflow_file_context` `workflow_mixin.py:444` → `_embed_workflow_files:511` → `_prepare_file_content_for_prompt:544` |
| 3 | Workflow **expert analysis** (generic) | `consolidated_findings.relevant_files` **+ conversation history** | `_prepare_files_for_expert_analysis:312` → `_force_embed_files_for_expert_analysis:375` → `read_files:409` |
| 4 | **debug** custom expert context | `consolidated_findings.relevant_files` | `debug.py:_prepare_expert_analysis_context:316` → `_prepare_file_content_for_prompt:317` |
| 5 | **consensus** per-model consult | `request.relevant_files` | `consensus.py:_consult_model:574` → `_prepare_file_content_for_prompt:594` |

Paths 3 and 4 read from `consolidated_findings`, accumulated across steps by
`_update_consolidated_findings` (`workflow_mixin.py:1369-1372`,
`relevant_files.update(...)`). A description supplied in *any* step must therefore be
**consolidated the same way** to reach the (final) expert-analysis call.

### Schema surfacing

- Simple path field: `SchemaBuilder.SIMPLE_FIELD_SCHEMAS["absolute_file_paths"]`
  (`tools/shared/schema_builders.py:47`), injected by `SchemaBuilder.build_schema:81`.
  **All four simple tools** (`chat`, `clink`, `apilookup`, `challenge`) hand-build
  `get_input_schema` and do **not** call `build_schema`; `clink` cherry-picks the
  `absolute_file_paths` key by name. So `SIMPLE_FIELD_SCHEMAS` is currently *inert* — see
  §Resolved decision D5.
- Workflow path fields: `WorkflowSchemaBuilder.WORKFLOW_FIELD_SCHEMAS`
  (`schema_builders.py:23`), auto-merged into **every** workflow tool by `build_schema:111`
  unless the tool lists the field in `excluded_workflow_fields`. `planner` excludes all
  file fields (`planner.py:205-219`).

## Proposed design

### Field placement — mirror the path field, per family

`file_descriptions` rides alongside the path field in each family; it is **not** on the
universal `ToolRequest`.

- **Pydantic models:** add to `ChatRequest` and `WorkflowRequest` as
  `Optional[dict[str, str]] = Field(default_factory=dict, ...)`. The explicit
  `default_factory=dict` is **required** — in Pydantic 2 an `Optional[...]` field with no
  default is a *required* field (mirrors `absolute_file_paths`, `chat.py:46`).
- **Validator (graceful degradation):** add a **separate** `@field_validator(...,
  mode="before")` for `file_descriptions` that (a) coerces a non-dict value to `{}`, and
  (b) within a dict, `str()`-coerces non-string values and drops `None` values — so
  `{"p": 123}` → `{"p": "123"}` and `{"p": None}` is dropped, rather than raising.
  **Do NOT add `file_descriptions` to the existing `convert_string_to_list` validator**
  (`base_models.py:126`) — it returns `[]`, the wrong type for a dict field.
- **Schema dicts:** add a `file_descriptions` entry to `SIMPLE_FIELD_SCHEMAS` and
  `WORKFLOW_FIELD_SCHEMAS` (type `object`, `additionalProperties: {type: string}`), plus a
  shared `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]` blurb.
- **Chat's hand-built schema:** add the entry to both `get_input_schema` (`chat.py:117`)
  and `get_tool_fields` (`chat.py:164`). This is what actually surfaces the field on
  `chat` (see D5).
- **planner exclusion:** add `"file_descriptions"` to `planner.py`'s
  `excluded_workflow_fields` (`planner.py:205`), mirroring its existing exclusion of
  `relevant_files`/`files_checked`. Every other workflow tool keeps the field.

### Accessors — mirror `get_request_files`

Add `get_request_file_descriptions(request) -> dict[str, str]` to `SimpleTool`
(beside `get_request_files`, `simple/base.py:232`) and to the workflow mixin (beside
`get_request_relevant_files`, `workflow_mixin.py:969`); each returns
`getattr(request, "file_descriptions", None) or {}`.

### Cross-step description persistence (workflow)

Workflow steps are **separate tool invocations**: on continuation, `consolidated_findings`
is rebuilt by `_reprocess_consolidated_findings` (`workflow_mixin.py:1391-1395`) replaying
the `step_data` dicts stored in `work_history`. So a description must live **in
`step_data`** to survive across steps — merging it from the per-invocation request would be
lost on the next step's rebuild.

`step_data` is produced by `prepare_step_data` (`workflow_mixin.py:759`), but **all 11
workflow tools override it and build the dict from scratch (no `super()` call)** — editing
each override would be invasive and easy to miss. Instead, inject once at the single call
site in `execute_workflow` (`workflow_mixin.py:705`, immediately after
`step_data = self.prepare_step_data(request)` and before `work_history.append`):
`step_data["file_descriptions"] = self.get_request_file_descriptions(request)`. This
covers every tool regardless of its override and is persisted in `work_history`.

Then: add `file_descriptions: dict[str, str] = Field(default_factory=dict)` to
`ConsolidatedFindings` (`base_models.py:136`), and in `_update_consolidated_findings`
(`workflow_mixin.py:1369`) merge it:
`self.consolidated_findings.file_descriptions.update(step_data.get("file_descriptions", {}))`
(dict update → a later step's description for a path overrides an earlier one; `.get(...,
{})` keeps pre-feature threads safe). This makes the map available to feed paths 3-4.

### Threading matrix

| Feed path | File source | Description source |
|-----------|-------------|--------------------|
| 1 chat `build_standard_prompt` | request paths | `get_request_file_descriptions(request)` |
| 2 `_embed_workflow_files` | request `relevant_files` | `get_request_file_descriptions(request)` |
| 3 `_prepare_files_for_expert_analysis` / `_force_embed_files_for_expert_analysis` | `consolidated_findings.relevant_files` | `consolidated_findings.file_descriptions` |
| 4 debug `_prepare_expert_analysis_context` | `consolidated_findings.relevant_files` | `consolidated_findings.file_descriptions` |
| 5 consensus `_consult_model` | request `relevant_files` | `get_request_file_descriptions(request)` |

All five pass an optional `descriptions=` kwarg through
`_prepare_file_content_for_prompt` / `read_files` down to `read_file_content`.

### Header rendering + sanitization

`read_file_content` gains a keyword-only `description: Optional[str] = None`. Before
rendering it **sanitizes** the value (see below); if the sanitized result is empty, no
annotation is added and the header is byte-for-byte identical to today. Otherwise the
successful header (`file_utils.py:506-510`) becomes:

```
--- BEGIN FILE: {file_path} (Last modified: {modified_at}) [{sanitized_description}] ---
```

`--- END FILE ---` and the error / not-found / too-large headers are never annotated.
`read_file_content` is the **single point** where sanitization is applied, so every feed
path is covered.

**Sanitization rules** (a small helper, e.g. `_sanitize_file_description`):
1. `strip()`; collapse all internal whitespace **and control characters (incl. newlines,
   tabs, CR)** to single spaces — guarantees a single-line header.
2. Neutralize delimiter tokens: remove/replace `]`, and any `---`, `--- BEGIN FILE:`,
   `--- END FILE:` substrings, so a description cannot fabricate a file boundary or close
   the bracket early.
3. Cap length at a named constant (e.g. `MAX_FILE_DESCRIPTION_LEN = 200`), truncating with
   an ellipsis.
4. If empty after step 1 → return empty (→ no annotation; covers whitespace-only input).

This closes the injection/corruption vector: the marker structure is parsed line-by-line
elsewhere (e.g. the `prompt.txt` extractor, `base_tool.py:919-930`, splits on `\n` and
matches `--- BEGIN/END FILE:` prefixes), so a multi-line or marker-bearing description
must never reach the header.

### Key matching — normalize then match (in `read_files`)

Before the read loop, `read_files` normalizes every description key through the **same**
resolution used for paths (`os.path.expanduser` + `resolve_and_validate_path`,
`file_utils.py:351`), producing a `resolved_key -> text` map; unresolvable keys are
dropped. Each loop iteration looks up its already-resolved `file_path`.

### Directory propagation & precedence

For each expanded file: (1) direct hit on its resolved path wins; else (2) the **deepest
ancestor directory** that is a key (separator-boundary prefix test, not bare
`startswith`). A file's own key always beats an inherited directory key.

*Trade-off (acknowledged):* propagating to children re-renders the same annotation on
every file in a directory of N files — a small token cost and some repetition. Per-child
is still preferred because it keeps the annotation adjacent to each file's content and
needs no new per-directory rendering hook. A "describe a directory as a unit" mode is a
possible follow-up (see Not Doing).

### Token accounting

No new logic — the (sanitized, capped) header is built before `estimate_tokens`
(`file_utils.py:511`). A test asserts a long description raises the per-file estimate.

### Dedup / continuation behaviour

- `filter_new_files` (`base_tool.py:1077`) skips files already embedded earlier in a
  continuation; such a file will not re-render its description on later turns.
- **Descriptions are fixed at first embed.** Supplying a new/changed description for an
  already-embedded path on a later continuation turn is a **silent no-op** — accepted,
  presentation-only.
- **Within a single workflow invocation**, descriptions *are* consolidated across steps
  (latest wins) so they reach expert analysis. They are **not** persisted into
  conversation memory, so files pulled from conversation history by
  `_prepare_files_for_expert_analysis` (`workflow_mixin.py:345`) in a *continued* workflow
  carry no description (see Not Doing).

## Tool scope

- **In scope:** `chat`; and **all workflow tools that embed `relevant_files`** —
  `analyze`, `codereview`, `refactor`, `debug`, `precommit`, `thinkdeep`, `secaudit`,
  `testgen`, `tracer`, `docgen`, `consensus`. (`debug` and `consensus` use custom feed
  paths 4/5; the rest use generic paths 2/3.)
- **Excluded workflow tool:** `planner` — excludes all file fields (`planner.py:205-219`),
  so `file_descriptions` is added to its exclusion list (no orphan field).
- **Out of scope:** `clink` (lists path *references* via `_format_file_references`,
  `clink.py:344`, and hands paths to an external CLI — never reaches `read_file_content`);
  file-less tools (`version`, `listmodels`, `challenge`).

## Backward compatibility

Path lists stay `list[str]`. `file_descriptions` defaults to `{}`. With an empty/absent
map every threaded function behaves exactly as before and rendered output is byte-for-byte
identical. No conversation-memory schema change.

## Assumptions

- **A1 (corrected):** in-scope embedding reaches the model through **five** feed paths
  (table above), not one. All converge on `read_file_content`, where sanitization +
  rendering happen. (Verified by tracing `read_files` / `_prepare_file_content_for_prompt`
  callers.)
- **A2:** resolving keys identically to paths makes them match the post-expansion resolved
  paths the read loop iterates; divergence silently drops the annotation (guarded by a
  normalization test).
- **A3:** a file's own key overrides an inherited directory key; for the consolidated
  workflow map, a later step's key overrides an earlier one.
- **A4:** appending a sanitized, single-line ` [desc]` to the header breaks no existing
  BEGIN-FILE consumer (they match the prefix; the `prompt.txt` extractor reads files
  without a description).

## Not Doing

- **Structured file objects** (`list[str | {path, description}]`) — breaking change that
  disrupts dedup/memory/expansion.
- **Descriptions for `images`.**
- **`clink` support** — references, not embedded content; a parallel render in
  `_format_file_references` is a deferred follow-up.
- **Persisting descriptions in conversation memory across continuation turns** — so
  history-sourced files in a continued workflow stay unannotated, and descriptions can't
  be updated after first embed. Larger change to `utils/conversation_memory.py`; deferred.
- **"Describe a directory as a unit"** (single annotation at the directory boundary) — the
  issue's open-question #1 alternative; deferred in favour of per-child propagation.
- **Annotating error / not-found / too-large file headers.**

## Resolved design decisions

- **D1 Schema surface:** mirror the path field per family (not on `ToolRequest`).
- **D2 Directory keys:** propagate to expanded children; child key beats inherited dir key.
- **D3 Key matching:** normalize keys identically to file paths, then match resolved.
- **D4 Scope (C3):** every workflow tool that embeds `relevant_files` is in scope;
  `planner` is the sole exclusion and is handled via its `excluded_workflow_fields`.
- **D5 `SIMPLE_FIELD_SCHEMAS` is forward-looking:** currently inert (no simple tool uses
  `build_schema`); added for consistency/future simple tools. `chat`'s field is surfaced
  by its hand-built schema. Could be omitted with no behavioural change.
- **D6 Sanitization (C6/C7):** descriptions are normalized to a single, delimiter-safe,
  length-capped line at the render point; empty-after-strip → no annotation.
- **D7 Value coercion (C12):** non-string dict values are `str()`-coerced and `None`
  dropped in the before-validator, consistent with graceful degradation.

## Revision notes (post-review)

This revision incorporates four design reviews. Material corrections from the original
draft: (a) **C5** — the original threaded only `_embed_workflow_files`, missing the
expert-analysis (paths 3-4) and consensus (path 5) feed paths and the cross-step
persistence they require; (b) **C2/C3** — scope widened to all embedding workflow tools
with an explicit `planner` exclusion; (c) **C6/C7** — added description sanitization;
(d) **C1/C12** — explicit field default and value coercion. Lower-severity doc fixes
(C4/C8/C9/C10/C11) folded into the relevant sections above.

## Testing strategy

- **Unit (primary):** `read_file_content` (render with/without description; byte-for-byte
  unchanged when absent; **sanitization** of newline, `--- END FILE:`/`]`/`---`,
  whitespace-only, and overlong inputs); `read_files` (map application, key normalization
  incl. `~`/`..`, directory propagation + precedence, token-count increase);
  `_prepare_file_content_for_prompt` pass-through. Per-family request/schema tests:
  `chat`, a representative generic workflow tool, `debug` (consolidated path), `consensus`
  (request path), and `planner` (asserts `file_descriptions` is **absent** from its
  schema). Validator tests: non-dict → `{}`, non-string values coerced, `None` dropped.
- **Cross-step:** a multi-step workflow test asserting a description from step 1 appears in
  the step-N expert-analysis embedding (consolidated map).
- **End-to-end (optional, deferred):** a simulator test asserting an annotated header
  reaches the model. Deferred (needs live API keys; excluded from `code_quality_checks.sh`).

# Implementation Plan: Per-file descriptions / annotations

> Design: [./design.md](./design.md)
> Tasks: [./tasks.md](./tasks.md)
> Created: 2026-06-06 · Revised after design review

Assumes a skilled Python developer with **no prior context** for this codebase. Read
[design.md](./design.md) first — especially §The feed paths and §Threading matrix, which
this plan implements. Line numbers reflect `main` at writing time and may drift; treat
them as anchors.

## Implementation order & rationale

The shared render core (incl. sanitization) is the riskiest, most-reused piece and lands
first, unit-tested with no tool. The two request surfaces wire in parallel; the
workflow expert-analysis paths build on the workflow surface:

```
Task 1  shared pipeline: render + sanitize + thread descriptions
Task 2  chat (simple-tool) request surface            ─┐ parallel
Task 3  workflow request surface + final-step embed   ─┘ + planner exclusion
Task 4  cross-step persistence + generic expert-analysis embedding   (needs 3)
Task 5  custom expert paths: debug + consensus                       (needs 3,4)
Task 6  final verification
```

Follow TDD; each task is one atomic commit; run `./code_quality_checks.sh` before
finishing any task.

---

## Task 1 — Shared pipeline: render, sanitize, thread
<a id="task-1-shared-pipeline"></a>

**Files:** `utils/file_utils.py`, `tools/shared/base_tool.py`,
`tools/shared/base_models.py`, `tests/`.

1. **Sanitizer.** Add a helper (e.g. `_sanitize_file_description(text) -> str`) in
   `utils/file_utils.py`: `strip`; collapse internal whitespace + control chars (incl.
   `\n\r\t`) to single spaces; remove/replace `]` and the substrings `---`,
   `--- BEGIN FILE:`, `--- END FILE:`; cap at a module constant
   `MAX_FILE_DESCRIPTION_LEN` (e.g. 200) with an ellipsis; return `""` if empty after
   strip.
   → verify: unit tests — newline/tab/CR collapse to one line; `]` and `--- END FILE:`
   are neutralized; overlong is truncated; `"   "` → `""`.

2. **`read_file_content` renders a sanitized description.** Add keyword-only
   `description: Optional[str] = None`. Sanitize it; if non-empty, append
   ` [{sanitized}]` to the **successful** BEGIN-FILE header (`file_utils.py:506-510`)
   before the closing ` ---`. Leave END and all error/not-found/too-large headers
   unchanged. No lookup here — pure render.
   → verify: with a description the BEGIN line contains a single-line `[...]`; with `None`
   (or whitespace-only) the bytes equal the pre-change output; a description containing
   `--- END FILE:` cannot introduce a second boundary token.

3. **`read_files` accepts and matches a descriptions map.** Add keyword-only
   `descriptions: Optional[dict[str, str]] = None`. Before the loop (`file_utils.py:586`)
   build `resolved_key -> text` via `os.path.expanduser` + `resolve_and_validate_path`
   (drop unresolvable). In the loop (`file_utils.py:588`) resolve each file's description:
   direct hit, else deepest ancestor-directory key (separator-boundary test); pass as
   `description=` to `read_file_content` (`file_utils.py:594`).
   → verify: per-file map annotates only the match; directory key annotates every child;
   child key overrides inherited dir key; `~`- and `..`-keys match resolved targets.

4. **Token accounting — confirm.** Header built before `estimate_tokens`
   (`file_utils.py:511`).
   → verify: token count for a file with a long description > same file with `None`.

5. **`_prepare_file_content_for_prompt` threads the map.** Add keyword-only
   `descriptions: Optional[dict[str, str]] = None` (`base_tool.py:999-1009`); forward into
   the `read_files(...)` call (`base_tool.py:1108`). No other behaviour change.
   → verify: pass-through test (annotated when supplied; unchanged when omitted).

6. **Shared blurb.** Add `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]`
   (`base_models.py:22`): optional absolute-path→intent map; rendered in each embedded
   file's header; applies to embedded files only.
   → verify: import succeeds; referenced by schema entries in Tasks 2-3.

---

## Task 2 — Chat (simple-tool) request surface
<a id="task-2-chat-surface"></a>

**Files:** `tools/shared/schema_builders.py`, `tools/chat.py`, `tools/simple/base.py`,
`tests/`. **Depends on Task 1.** Parallel with Task 3.

1. **Simple schema entry (forward-looking).** Add `file_descriptions` to
   `SchemaBuilder.SIMPLE_FIELD_SCHEMAS` (`schema_builders.py:47`): `object`,
   `additionalProperties: {"type": "string"}`, description from the shared blurb. Note: no
   current simple tool calls `build_schema`, so this is inert today (design D5) — `chat`'s
   field is surfaced by step 3 below. Kept for consistency/future tools.
   → verify: `SchemaBuilder.build_schema()` output contains `file_descriptions`.

2. **`ChatRequest` field + validator.** In `tools/chat.py` add
   `file_descriptions: Optional[dict[str, str]] = Field(default_factory=dict,
   description=COMMON_FIELD_DESCRIPTIONS["file_descriptions"])` (the explicit default is
   required — Pydantic 2). Add a **separate** `@field_validator("file_descriptions",
   mode="before")` that coerces non-dict → `{}` and, within a dict, `str()`-coerces values
   and drops `None`. Do **not** touch any list validator.
   → verify: dict kept; string → `{}`; `{"p": 1}` → `{"p": "1"}`; `{"p": None}` dropped.

3. **Chat hand-built schema.** Add the `file_descriptions` property to **both**
   `get_input_schema` (`chat.py:117`, beside `absolute_file_paths` at `chat.py:124`) and
   `get_tool_fields` (`chat.py:164`).
   → verify: `ChatTool().get_input_schema()["properties"]` contains `file_descriptions`.

4. **Accessor.** Add `get_request_file_descriptions(request) -> dict[str, str]` to
   `SimpleTool` (beside `get_request_files`, `simple/base.py:232`):
   `getattr(request, "file_descriptions", None) or {}`.
   → verify: returns the map for a populated request, `{}` otherwise.

5. **Thread into the prompt build.** In `build_standard_prompt` (`simple/base.py:780`)
   pass `descriptions=self.get_request_file_descriptions(request)` into
   `_prepare_file_content_for_prompt` (`base.py:808`).
   → verify: a `chat` request with `file_descriptions` yields an annotated BEGIN-FILE
   header; without it, output unchanged.

---

## Task 3 — Workflow request surface + final-step embed
<a id="task-3-workflow-surface"></a>

**Files:** `tools/shared/base_models.py`, `tools/workflow/schema_builders.py`,
`tools/workflow/workflow_mixin.py`, `tools/planner.py`, `tests/`.
**Depends on Task 1.** Parallel with Task 2.

1. **`WorkflowRequest` field + validator.** Add
   `file_descriptions: Optional[dict[str, str]] = Field(default_factory=dict, ...)`
   (`base_models.py:96`). Add a **separate** `@field_validator("file_descriptions",
   mode="before")` (same coercion as Task 2.2). **Do not** append the field to
   `convert_string_to_list` (`base_models.py:126`) — it returns `[]`.
   → verify: dict kept; non-dict → `{}`; value coercion as in Task 2.2.

2. **Workflow schema entry.** Add `file_descriptions` to
   `WorkflowSchemaBuilder.WORKFLOW_FIELD_SCHEMAS` (`schema_builders.py:23`), same shape as
   Task 2.1; auto-merged by `build_schema:111`.
   → verify: a normal workflow tool's `get_input_schema()` contains `file_descriptions`.

3. **planner exclusion.** Add `"file_descriptions"` to `planner.py`'s
   `excluded_workflow_fields` list (`planner.py:205`), alongside `relevant_files` /
   `files_checked`.
   → verify: `PlannerTool().get_input_schema()["properties"]` does **not** contain
   `file_descriptions`.

4. **Accessor.** Add `get_request_file_descriptions(request) -> dict[str, str]` to the
   workflow mixin (beside `get_request_relevant_files`, `workflow_mixin.py:969`).
   → verify: returns the map / `{}`.

5. **Thread into final-step embed.** In `_embed_workflow_files` (`workflow_mixin.py:511`)
   pass `descriptions=self.get_request_file_descriptions(request)` into
   `_prepare_file_content_for_prompt` (`workflow_mixin.py:544`).
   → verify: a workflow tool's final step (`next_step_required=False`) with
   `file_descriptions` produces annotated headers in the embedded content; intermediate
   steps and absent map unchanged.

---

## Task 4 — Cross-step persistence + generic expert-analysis embedding
<a id="task-4-expert-analysis"></a>

**Files:** `tools/shared/base_models.py`, `tools/workflow/workflow_mixin.py`, `tests/`.
**Depends on Tasks 1, 3.**

This is the path that produces the prompt sent to the **assistant/expert model** for most
workflow tools — the original draft missed it.

1. **Consolidate descriptions.** Add
   `file_descriptions: dict[str, str] = Field(default_factory=dict)` to
   `ConsolidatedFindings` (`base_models.py:136`). In `_update_consolidated_findings`
   (`workflow_mixin.py:1369`) add
   `self.consolidated_findings.file_descriptions.update(step_data.get("file_descriptions", {}))`.
   **Carry `file_descriptions` in `step_data` via a single call-site injection**, NOT via
   `prepare_step_data`: in `execute_workflow` (`workflow_mixin.py:705`), immediately after
   `step_data = self.prepare_step_data(request)` and before `self.work_history.append(...)`,
   add `step_data["file_descriptions"] = self.get_request_file_descriptions(request)`.
   Rationale: all 11 workflow tools override `prepare_step_data` from scratch (no `super()`),
   and `consolidated_findings` is rebuilt from `work_history` `step_data` on continuation
   (`_reprocess_consolidated_findings`, `workflow_mixin.py:1391`) — so the value must be in
   `step_data` (persisted in history) and injecting at the one call site covers every tool.
   → verify: after a 2-step continuation run where step 1 supplies a description, the value
   is present in `consolidated_findings.file_descriptions` on step 2 (i.e. it survived the
   `work_history` rebuild), and a later step's value overrides an earlier one.

2. **Thread into force-embed.** Add keyword-only
   `descriptions: Optional[dict[str, str]] = None` to
   `_force_embed_files_for_expert_analysis` (`workflow_mixin.py:375`) and pass it into the
   `read_files(...)` call (`workflow_mixin.py:409`). In `_prepare_files_for_expert_analysis`
   (`workflow_mixin.py:312`) pass `descriptions=self.consolidated_findings.file_descriptions`.
   → verify: a multi-step workflow whose step-1 `relevant_files` carry descriptions
   produces annotated headers in the expert-analysis file content.

3. **Note on history-sourced files.** Files added from conversation history
   (`workflow_mixin.py:345`) will not have descriptions (not persisted in conversation
   memory — see design §Not Doing). No code change; covered by a comment at the call site.
   → verify: documented; no annotation expected for history-only files (assert in test).

---

## Task 5 — Custom expert paths: debug + consensus
<a id="task-5-custom-paths"></a>

**Files:** `tools/debug.py`, `tools/consensus.py`, `tests/`.
**Depends on Tasks 1, 3, 4.**

1. **debug.** In `_prepare_expert_analysis_context` (`debug.py:316`) pass
   `descriptions=consolidated_findings.file_descriptions` into the
   `_prepare_file_content_for_prompt` call (`debug.py:317`).
   → verify: a debug workflow with described `relevant_files` annotates the
   `=== ESSENTIAL FILES FOR DEBUGGING ===` block.

2. **consensus.** In `_consult_model` (`consensus.py:574`) pass
   `descriptions=self.get_request_file_descriptions(request)` into the
   `_prepare_file_content_for_prompt` call (`consensus.py:594`). Consensus uses
   `request.relevant_files` (request-level, not consolidated), so use the request map.
   → verify: a consensus request with described `relevant_files` annotates the context
   files sent to each model.

---

## Task 6 — Final verification
<a id="task-6-verification"></a>

**Depends on Tasks 1-5.** Quality gates + design-conformance reviews. Optional simulator
test (design §Testing strategy) remains a deferred follow-up, not a gate.

---

## Assumptions (carried from design)

A1 (corrected): five feed paths, all converging on `read_file_content`; A2: key resolution
matches post-expansion paths; A3: own key > inherited dir key, later step > earlier step;
A4: sanitized single-line header breaks no consumer. Full text in
[design.md §Assumptions](./design.md#assumptions).

## Not Doing (carried from design)

Structured file objects; image descriptions; clink support; cross-continuation /
conversation-memory description persistence; describe-as-unit; error-header annotation.
Rationale: [design.md §Not Doing](./design.md#not-doing).

# Tasks: Per-file descriptions / annotations

> Design: [./design.md](./design.md)
> Implementation: [./implementation.md](./implementation.md)
> Status: pending
> Created: 2026-06-06 · Revised after design review
> Not Doing: structured file objects, image descriptions, clink support, cross-continuation/conversation-memory description persistence, describe-as-unit, error-header annotation

## Task 1: Shared pipeline — render, sanitize, thread
- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Slicing:** Risk-First (shared, most-reused core)
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-1-shared-pipeline](./implementation.md#task-1-shared-pipeline)

### Subtasks
- [ ] 1.1 `utils/file_utils.py`: add `_sanitize_file_description(text)` + `MAX_FILE_DESCRIPTION_LEN` — strip, collapse whitespace/control chars to single spaces, neutralize `]`/`---`/`--- BEGIN FILE:`/`--- END FILE:`, cap length, return `""` if empty after strip.
- [ ] 1.2 Tests: newline/tab/CR → one line; `]` and `--- END FILE:` neutralized; overlong truncated; whitespace-only → `""`.
- [ ] 1.3 `utils/file_utils.py` `read_file_content` (~L421): add keyword-only `description: Optional[str] = None`; sanitize; if non-empty append ` [{sanitized}]` to the successful BEGIN-FILE header (L506-510); leave END and error/not-found/too-large headers unchanged. Pure renderer.
- [ ] 1.4 Tests: with description → single-line `[...]`; with `None`/whitespace-only → bytes equal pre-change output; embedded `--- END FILE:` cannot create a second boundary.
- [ ] 1.5 `utils/file_utils.py` `read_files` (~L523): add keyword-only `descriptions: Optional[dict[str, str]] = None`; before the loop (L586) build `resolved_key -> text` via `os.path.expanduser` + `resolve_and_validate_path` (drop unresolvable); in the loop resolve each file's description (direct hit, else deepest ancestor-dir key with separator-boundary test) and pass `description=` into `read_file_content` (L594).
- [ ] 1.6 Tests: per-file map annotates only the match; directory key annotates every child; child key overrides inherited dir key; `~`/`..` keys match resolved targets.
- [ ] 1.7 Test: token count for a file with a long description > same file with `None`.
- [ ] 1.8 `tools/shared/base_tool.py` `_prepare_file_content_for_prompt` (L999): add keyword-only `descriptions: Optional[dict[str, str]] = None`; forward into `read_files(...)` (L1108). Pass-through test.
- [ ] 1.9 `tools/shared/base_models.py`: add `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]` blurb (absolute-path→intent; embedded files only).

## Task 2: Chat (simple-tool) request surface
- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** Task 3
- **Docs:** [implementation.md#task-2-chat-surface](./implementation.md#task-2-chat-surface)

### Subtasks
- [ ] 2.1 `tools/shared/schema_builders.py` `SIMPLE_FIELD_SCHEMAS` (L47): add `file_descriptions` (`object`, `additionalProperties:{type:string}`, shared blurb). Inert today (no simple tool uses `build_schema`); forward-looking — see design D5.
- [ ] 2.2 `tools/chat.py` `ChatRequest` (L42): add `file_descriptions: Optional[dict[str, str]] = Field(default_factory=dict, ...)` (explicit default required — Pydantic 2). Add a **separate** `@field_validator("file_descriptions", mode="before")`: non-dict → `{}`; within a dict, `str()`-coerce values and drop `None`. Do **not** touch any list validator. Test all paths.
- [ ] 2.3 `tools/chat.py`: add `file_descriptions` to **both** `get_input_schema` (L117, beside `absolute_file_paths`) and `get_tool_fields` (L164). Assert it appears in `get_input_schema()["properties"]`.
- [ ] 2.4 `tools/simple/base.py`: add `get_request_file_descriptions(request) -> dict[str, str]` beside `get_request_files` (L232).
- [ ] 2.5 `tools/simple/base.py` `build_standard_prompt` (L780): pass `descriptions=self.get_request_file_descriptions(request)` into `_prepare_file_content_for_prompt` (L808).
- [ ] 2.6 Test: `chat` with `file_descriptions` → annotated BEGIN-FILE header; without → unchanged.

## Task 3: Workflow request surface + final-step embed
- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** Task 2
- **Docs:** [implementation.md#task-3-workflow-surface](./implementation.md#task-3-workflow-surface)

### Subtasks
- [ ] 3.1 `tools/shared/base_models.py` `WorkflowRequest` (L96): add `file_descriptions: Optional[dict[str, str]] = Field(default_factory=dict, ...)` + a **separate** `@field_validator("file_descriptions", mode="before")` (coercion as 2.2). Do **not** append the field to `convert_string_to_list` (L126). Test paths.
- [ ] 3.2 `tools/workflow/schema_builders.py` `WORKFLOW_FIELD_SCHEMAS` (L23): add `file_descriptions` (same shape as 2.1); auto-merged by `build_schema` (L111). Confirm it appears on a normal workflow tool's schema.
- [ ] 3.3 `tools/planner.py` (L205): add `"file_descriptions"` to `excluded_workflow_fields`. Test: `PlannerTool().get_input_schema()["properties"]` does **not** contain `file_descriptions`.
- [ ] 3.4 `tools/workflow/workflow_mixin.py`: add `get_request_file_descriptions(request) -> dict[str, str]` beside `get_request_relevant_files` (L969).
- [ ] 3.5 `tools/workflow/workflow_mixin.py` `_embed_workflow_files` (L511): pass `descriptions=self.get_request_file_descriptions(request)` into `_prepare_file_content_for_prompt` (L544).
- [ ] 3.6 Test: final step (`next_step_required=False`) with `file_descriptions` → annotated embedded headers; intermediate steps / absent map unchanged. (Descriptions apply to embedded `relevant_files` only — `files_checked` is reference-only.)

## Task 4: Cross-step persistence + generic expert-analysis embedding
- **Status:** pending
- **Depends on:** Task 1, Task 3
- **Size:** M
- **Slicing:** Risk-First (the prompt the assistant/expert model actually receives)
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-4-expert-analysis](./implementation.md#task-4-expert-analysis)

### Subtasks
- [ ] 4.1 `tools/shared/base_models.py` `ConsolidatedFindings` (L136): add `file_descriptions: dict[str, str] = Field(default_factory=dict)`.
- [ ] 4.2 `tools/workflow/workflow_mixin.py` `execute_workflow` (L705): immediately after `step_data = self.prepare_step_data(request)` (before `work_history.append`), inject `step_data["file_descriptions"] = self.get_request_file_descriptions(request)`. Then in `_update_consolidated_findings` (L1369) add `self.consolidated_findings.file_descriptions.update(step_data.get("file_descriptions", {}))`. **Do NOT edit `prepare_step_data` or its 11 per-tool overrides** — the call-site injection covers all and persists in `work_history` (rebuilt on continuation by `_reprocess_consolidated_findings`, L1391).
- [ ] 4.3 Test: after a 2-step **continuation** run where step 1 supplies a description, it is present in `consolidated_findings.file_descriptions` on step 2 (survived the `work_history` rebuild); a later step overrides an earlier value for the same path.
- [ ] 4.4 `tools/workflow/workflow_mixin.py` `_force_embed_files_for_expert_analysis` (L375): add keyword-only `descriptions=None`; pass into `read_files(...)` (L409). `_prepare_files_for_expert_analysis` (L312): pass `descriptions=self.consolidated_findings.file_descriptions`.
- [ ] 4.5 Test: a multi-step workflow whose relevant_files carry descriptions → annotated headers in the expert-analysis file content. Assert history-only files (added at L345) are not annotated (documented limitation).

## Task 5: Custom expert paths — debug + consensus
- **Status:** pending
- **Depends on:** Task 1, Task 3, Task 4
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-5-custom-paths](./implementation.md#task-5-custom-paths)

### Subtasks
- [ ] 5.1 `tools/debug.py` `_prepare_expert_analysis_context` (L316): pass `descriptions=consolidated_findings.file_descriptions` into `_prepare_file_content_for_prompt` (L317).
- [ ] 5.2 Test: debug workflow with described `relevant_files` annotates the `=== ESSENTIAL FILES FOR DEBUGGING ===` block.
- [ ] 5.3 `tools/consensus.py` `_consult_model` (L574): pass `descriptions=self.get_request_file_descriptions(request)` into `_prepare_file_content_for_prompt` (L594). (Request-level, not consolidated.)
- [ ] 5.4 Test: consensus request with described `relevant_files` annotates the context files sent to each model.

## Task 6: Final verification
- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5
- **Size:** S
- **Can run in parallel with:** —

### Subtasks
- [ ] 6.1 Run `/kk:test` — full unit suite (`pytest -m "not integration"`) + `./code_quality_checks.sh`; verify the byte-for-byte-unchanged guarantee for the no-description path and the sanitization tests pass.
- [ ] 6.2 Run `/kk:document` — surface the new field in `docs/adding_tools.md` / tool docs if needed; move this feature dir from `docs/wip` to `docs/done` when complete.
- [ ] 6.3 Run `/kk:review-code` with `python` as the language input.
- [ ] 6.4 Run `/kk:review-spec` to confirm implementation matches design + implementation docs.

## Dependency Graph

```
Task 1 ─┬─→ Task 2 ─────────────────────────────→ Task 6
        └─→ Task 3 ─┬─→ Task 4 ─→ Task 5 ───────→ Task 6
                    └─────────────→ Task 5
Task 2 ∥ Task 3
```

# Tasks: Per-file descriptions / annotations

> Design: [./design.md](./design.md)
> Implementation: [./implementation.md](./implementation.md)
> Status: pending
> Created: 2026-06-06
> Not Doing: structured file objects, image descriptions, clink support, workflow file_context/reference-step rendering, error-header annotation

## Task 1: Shared file-content pipeline renders descriptions
- **Status:** pending
- **Depends on:** —
- **Size:** M
- **Slicing:** Risk-First (shared, most-reused core)
- **Can run in parallel with:** —
- **Docs:** [implementation.md#task-1-shared-pipeline](./implementation.md#task-1-shared-pipeline)

### Subtasks
- [ ] 1.1 `utils/file_utils.py` `read_file_content` (~L421): add keyword-only `description: Optional[str] = None`; append ` [{description}]` to the successful BEGIN-FILE header (L506-510) only when non-empty; leave END line and error/not-found/too-large headers unchanged. Pure renderer — no lookup.
- [ ] 1.2 Unit test: with a description the BEGIN line contains `[...]`; with `None` the bytes equal the pre-change output for the same file.
- [ ] 1.3 `utils/file_utils.py` `read_files` (~L523): add keyword-only `descriptions: Optional[dict[str, str]] = None`; before the loop (L586) build a `resolved_key -> text` map via `os.path.expanduser` + `resolve_and_validate_path` (drop unresolvable keys); in the loop resolve each file's description (direct hit, else deepest ancestor-directory key using a separator-boundary check) and pass `description=` into `read_file_content` (L594).
- [ ] 1.4 Unit tests: per-file map annotates only the matching file; directory key annotates every expanded child; child key overrides inherited dir key; `~`- and `..`-containing keys match their resolved targets.
- [ ] 1.5 Unit test: token count for a file with a long description is strictly greater than for the same file with `description=None` (confirms truncation accounts for it).
- [ ] 1.6 `tools/shared/base_tool.py` `_prepare_file_content_for_prompt` (L999): add keyword-only `descriptions: Optional[dict[str, str]] = None`; forward into the `read_files(...)` call (L1108). Test pass-through (annotated when supplied; unchanged when omitted).
- [ ] 1.7 `tools/shared/base_models.py`: add `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]` blurb (absolute-path→intent map; renders in each file header).

## Task 2: Chat (simple-tool) surface
- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** Task 3
- **Docs:** [implementation.md#task-2-chat-surface](./implementation.md#task-2-chat-surface)

### Subtasks
- [ ] 2.1 `tools/shared/schema_builders.py` `SIMPLE_FIELD_SCHEMAS` (L47): add `file_descriptions` entry — type `object`, `additionalProperties: {type: string}`, description from `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]`.
- [ ] 2.2 `tools/chat.py` `ChatRequest` (L42): add `file_descriptions: Optional[dict[str, str]]` + a `mode="before"` validator coercing non-dict input to `{}` (mirror `convert_string_to_list`). Test both paths.
- [ ] 2.3 `tools/chat.py`: add the `file_descriptions` property to **both** `get_input_schema` (L117, beside `absolute_file_paths`) and `get_tool_fields` (L164). Assert it appears in `get_input_schema()["properties"]`.
- [ ] 2.4 `tools/simple/base.py`: add `get_request_file_descriptions(request) -> dict[str, str]` beside `get_request_files` (L232), returning the map or `{}`.
- [ ] 2.5 `tools/simple/base.py` `build_standard_prompt` (L780): fetch the map via the accessor and pass `descriptions=...` into `_prepare_file_content_for_prompt` (L808).
- [ ] 2.6 Test: a `chat` request with `file_descriptions` produces an annotated BEGIN-FILE header; without it, output is unchanged.

## Task 3: Workflow-tools surface
- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** Task 2
- **Docs:** [implementation.md#task-3-workflow-surface](./implementation.md#task-3-workflow-surface)

### Subtasks
- [ ] 3.1 `tools/shared/base_models.py` `WorkflowRequest` (L96): add `file_descriptions: Optional[dict[str, str]]`; extend graceful coercion so a non-dict value becomes `{}` (the existing `convert_string_to_list` at L126 covers only list fields). Test both paths.
- [ ] 3.2 `tools/workflow/schema_builders.py` `WORKFLOW_FIELD_SCHEMAS` (L23): add `file_descriptions` entry (same shape as 2.1); confirm it auto-merges into a workflow tool's `get_input_schema()`.
- [ ] 3.3 `tools/workflow/workflow_mixin.py`: add `get_request_file_descriptions(request) -> dict[str, str]` beside `get_request_relevant_files` (L969).
- [ ] 3.4 `tools/workflow/workflow_mixin.py` `_embed_workflow_files` (L511): fetch the map and pass `descriptions=...` into `_prepare_file_content_for_prompt` (L544).
- [ ] 3.5 Test: a workflow tool's final step (`next_step_required=False`) with `file_descriptions` embeds annotated headers; intermediate steps and absent map are unchanged.

## Task 4: Final verification
- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3
- **Size:** S
- **Can run in parallel with:** —

### Subtasks
- [ ] 4.1 Run `/kk:test` — full unit suite (`pytest -m "not integration"`) + `./code_quality_checks.sh`; verify byte-for-byte-unchanged guarantee holds for the no-description path.
- [ ] 4.2 Run `/kk:document` — update `docs/adding_tools.md` / tool docs if the new field needs surfacing; move this feature dir out of `docs/wip` when complete.
- [ ] 4.3 Run `/kk:review-code` with `python` as the language input.
- [ ] 4.4 Run `/kk:review-spec` to confirm implementation matches design + implementation docs.

## Dependency Graph

```
Task 1 ─→ Task 2 ─→ Task 4
   └────→ Task 3 ─→ Task 4
Task 2 ∥ Task 3
```

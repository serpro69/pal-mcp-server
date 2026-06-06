# Implementation Plan: Per-file descriptions / annotations

> Design: [./design.md](./design.md)
> Tasks: [./tasks.md](./tasks.md)
> Created: 2026-06-06

This plan assumes a skilled Python developer with **no prior context** for this codebase.
Read [design.md](./design.md) first — it documents the verified file pipeline and the
three resolved design decisions. All line numbers reflect `main` at writing time and may
drift; treat them as anchors, not absolutes.

## Implementation order & rationale

The shared rendering core is the riskiest and most-reused piece, so it lands first
(risk-first) and is unit-tested directly with no tool involved. The two tool families
then wire into it independently and in parallel:

```
Task 1  shared pipeline renders descriptions  (file_utils + base_tool + the shared blurb)
Task 2  chat (simple-tool) surface            ─┐ parallel
Task 3  workflow-tools surface                ─┘ parallel
Task 4  final verification
```

Follow TDD throughout: write the failing unit test, then the change. Each task maps to
one atomic commit. Run `./code_quality_checks.sh` (ruff/black/isort + unit tests) before
finishing any task.

---

## Task 1 — Shared file-content pipeline renders descriptions
<a id="task-1-shared-pipeline"></a>

**Files:** `utils/file_utils.py`, `tools/shared/base_tool.py`,
`tools/shared/base_models.py`, `tests/`.

This task makes the *content* pipeline description-aware end to end, independent of any
tool. After it, calling `read_files(..., descriptions=...)` annotates headers correctly.

### Steps

1. **`read_file_content` — render an optional description.** Add a keyword-only
   `description: Optional[str] = None` parameter. When it is a non-empty string, append
   ` [{description}]` to the **successful** BEGIN-FILE header (`file_utils.py:506-510`),
   immediately before the closing ` ---`. Leave the END-FILE line and all error / not-
   found / too-large headers unchanged. `read_file_content` performs **no lookup** — it
   renders exactly the string it is given.
   → verify: unit test — with a description the BEGIN line contains `[...]`; with `None`
   the returned bytes equal the pre-change output for the same file.

2. **`read_files` — accept and apply a descriptions map.** Add keyword-only
   `descriptions: Optional[dict[str, str]] = None`. Before the read loop
   (`file_utils.py:586`), build a normalized lookup: for each `(key, text)` resolve the
   key with `os.path.expanduser` then `resolve_and_validate_path` (the same calls
   `expand_paths` uses, `file_utils.py:351`); drop keys that fail resolution; store
   `str(resolved) -> text`. Inside the loop (`file_utils.py:588`), compute each file's
   description via: (a) direct hit on the file's resolved path, else (b) the **deepest
   ancestor directory** key (ancestor test must check a path-separator boundary, e.g.
   compare against `key + os.sep`, not a bare `startswith`). Pass the result as
   `description=` to `read_file_content` (`file_utils.py:594`).
   → verify: unit tests — (i) per-file map annotates the matching file only; (ii) a
   directory key annotates every expanded child; (iii) a child's own key overrides the
   inherited directory key; (iv) a `~`-prefixed and a `..`-containing key both match
   their resolved targets.

3. **Token accounting — confirm, don't add logic.** The header is built before
   `estimate_tokens(formatted)` (`file_utils.py:511`), so description text is already
   counted.
   → verify: unit test — `read_file_content` token count for a file with a long
   description is strictly greater than for the same file with `description=None`.

4. **`_prepare_file_content_for_prompt` — thread the map through.** Add keyword-only
   `descriptions: Optional[dict[str, str]] = None` to the signature
   (`base_tool.py:999-1009`) and forward it into the `read_files(...)` call
   (`base_tool.py:1108`). No other behaviour changes; filtering/expansion/dedup stay as
   is.
   → verify: unit test calling `_prepare_file_content_for_prompt` with a descriptions map
   produces annotated content; calling it without the kwarg is unchanged.

5. **Shared field-description constant.** Add `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]`
   (`base_models.py:22`) — a concise blurb explaining the optional absolute-path→intent
   map and that descriptions render in each file's header. Both family schemas (Tasks 2
   and 3) reference this single constant.
   → verify: import succeeds; constant is referenced by the schema entries added later.

---

## Task 2 — Chat (simple-tool) surface
<a id="task-2-chat-surface"></a>

**Files:** `tools/shared/schema_builders.py`, `tools/chat.py`, `tools/simple/base.py`,
`tests/`. **Depends on Task 1.** Parallel with Task 3.

### Steps

1. **Simple schema entry.** Add a `file_descriptions` entry to
   `SchemaBuilder.SIMPLE_FIELD_SCHEMAS` (`schema_builders.py:47`): JSON type `object`,
   `additionalProperties: {"type": "string"}`, description from
   `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]`. This serves current/future simple
   tools built via `SchemaBuilder.build_schema`.
   → verify: `SchemaBuilder.build_schema()` output contains `file_descriptions` next to
   `absolute_file_paths`.

2. **`ChatRequest` field + validator.** Add `file_descriptions: Optional[dict[str, str]]`
   to `ChatRequest` (`chat.py:42`). Add a `mode="before"` field validator that coerces a
   non-dict value to `{}` (mirror `convert_string_to_list`, `base_models.py:126`).
   → verify: constructing `ChatRequest` with a dict keeps it; with a string yields `{}`.

3. **Chat hand-built schema.** Add the same `file_descriptions` property to **both**
   `get_input_schema` (`chat.py:117-154`, beside `absolute_file_paths` at `chat.py:124`)
   and `get_tool_fields` (`chat.py:164-183`).
   → verify: `ChatTool().get_input_schema()["properties"]` contains `file_descriptions`.

4. **Accessor.** Add `get_request_file_descriptions(request) -> dict[str, str]` to
   `SimpleTool` (`tools/simple/base.py`, beside `get_request_files` at `base.py:232`),
   returning `getattr(request, "file_descriptions", None) or {}`.
   → verify: unit test returns the dict for a populated request and `{}` for a request
   without the attribute.

5. **Thread into the prompt build.** In `build_standard_prompt`
   (`tools/simple/base.py:780`), fetch the map and pass it to
   `_prepare_file_content_for_prompt` (the call at `base.py:808`) as `descriptions=...`.
   → verify: unit/integration test — a `chat` request with `file_descriptions` produces
   an embedded BEGIN-FILE header containing the annotation; without it, output unchanged.

---

## Task 3 — Workflow-tools surface
<a id="task-3-workflow-surface"></a>

**Files:** `tools/shared/base_models.py`, `tools/workflow/schema_builders.py`,
`tools/workflow/workflow_mixin.py`, `tests/`. **Depends on Task 1.** Parallel with Task 2.

### Steps

1. **`WorkflowRequest` field + validator.** Add
   `file_descriptions: Optional[dict[str, str]]` to `WorkflowRequest`
   (`base_models.py:96`). Extend the graceful-coercion validator so a non-dict
   `file_descriptions` becomes `{}` (the existing `convert_string_to_list` at
   `base_models.py:126` only covers the list fields; add dict handling — either a second
   `mode="before"` validator for this field or a combined one).
   → verify: constructing `WorkflowRequest` (minimal required fields) with a dict keeps
   it; with a non-dict yields `{}`.

2. **Workflow schema entry.** Add a `file_descriptions` entry to
   `WorkflowSchemaBuilder.WORKFLOW_FIELD_SCHEMAS` (`schema_builders.py:23`), same shape as
   in Task 2, description from `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]`. It is
   auto-merged into every workflow tool's schema by `build_schema` (`schema_builders.py:111`).
   → verify: a workflow tool's `get_input_schema()` contains `file_descriptions`.

3. **Accessor.** Add `get_request_file_descriptions(request) -> dict[str, str]` to the
   workflow mixin (`tools/workflow/workflow_mixin.py`, beside `get_request_relevant_files`
   at `workflow_mixin.py:969`).
   → verify: unit test returns the map for a populated request, `{}` otherwise.

4. **Thread into embedding.** In `_embed_workflow_files` (`workflow_mixin.py:511`), fetch
   the map and pass `descriptions=...` to `_prepare_file_content_for_prompt` (the call at
   `workflow_mixin.py:544`). Keys are matched against `relevant_files`/`files_checked`
   paths by the shared normalization from Task 1 — no per-list logic needed.
   → verify: test — a workflow tool's **final step** (`next_step_required=False`, so
   `_should_embed_files_in_workflow_step` returns True, `workflow_mixin.py:481`) with
   `file_descriptions` embeds annotated headers; intermediate steps and absent map are
   unchanged.

---

## Task 4 — Final verification
<a id="task-4-verification"></a>

**Depends on Tasks 1-3.** Runs the project quality gates and the design-conformance
reviews. The optional simulator test (see design.md §Testing strategy) is a deferred
follow-up, not a gate here.

---

## Assumptions (carried from design)

- **A1:** in-scope tools embed via `read_file_content` (verified: chat + workflow; clink
  excluded).
- **A2:** resolving keys identically to paths makes them match post-expansion; otherwise
  annotations drop silently (guarded by a normalization test).
- **A3:** a file's own key overrides an inherited directory key.
- **A4:** appending ` [desc]` to the header breaks no existing BEGIN-FILE consumer.

## Not Doing (carried from design)

Structured file objects; image descriptions; clink support; workflow `file_context`
response-block / reference-only-step rendering; annotating error/not-found/too-large
headers. Rationale per [design.md §Not Doing](./design.md#not-doing).

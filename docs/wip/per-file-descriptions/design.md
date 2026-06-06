# Design: Per-file descriptions / annotations for tool file inputs

> Status: draft
> Created: 2026-06-06
> Source issue: https://github.com/serpro69/pal-mcp-server/issues/12
> Implementation: [./implementation.md](./implementation.md)
> Tasks: [./tasks.md](./tasks.md)

## Summary

Add an optional, path-keyed map `file_descriptions: dict[str, str]` to the tools that
embed file content. Each path's description is rendered into that file's existing
`--- BEGIN FILE: ... ---` delimiter header, so the model receives per-file *intent*
("module under review", "reference-only — don't review") right next to the content.

The map is **additive and presentation-only**: the path-list parameters stay
`list[str]`, so deduplication, conversation-memory tracking, and directory expansion —
all of which key on plain path strings — are untouched.

## Motivation

When many files are sent at once, the model gets path names + raw contents and nothing
about *why* each file is there or how files relate. Today the only channel for that is
the free-text `prompt` / `step` field, which is physically separated from the path list,
so the path-to-intent mapping is implicit and degrades as file count grows. The
workflow-tool field `relevant_context` does not fill this gap — it targets **symbols**
(`Class.method`), not files.

## Goals

- An optional `file_descriptions` map can be supplied to the chat tool and the workflow
  tools, keyed by absolute path.
- When a path has a description, it appears in that file's `--- BEGIN FILE ---` header.
- When a path has no description, output is **byte-for-byte unchanged** from today.
- Deduplication, directory expansion, and continuation file-tracking behaviour are
  unchanged.
- Description text is accounted for in token-aware truncation.

## Non-goals (Not Doing)

See [§Not Doing](#not-doing).

## Current architecture (verified against `main`)

File parameters differ by tool family, but both are plain `list[str]` of absolute paths,
and both converge on a single rendering function.

### Two entry families

- **Simple tools** — `absolute_file_paths`, declared on the per-tool request model
  (e.g. `ChatRequest`, `tools/chat.py:46`). Read via `SimpleTool.get_request_files`
  (`tools/simple/base.py:232`, which reads `request.absolute_file_paths`).
- **Workflow tools** (`debug`, `precommit`, `codereview`, `refactor`, `thinkdeep`,
  `analyze`) — `relevant_files` / `files_checked` on `WorkflowRequest`
  (`tools/shared/base_models.py:114-115`). Read via `get_request_relevant_files`
  (`tools/workflow/workflow_mixin.py:969`).

### The convergence point

Both families funnel through one method and one renderer:

```
Simple:   build_standard_prompt (tools/simple/base.py:780)
              → _prepare_file_content_for_prompt (tools/shared/base_tool.py:999)
Workflow: _handle_workflow_file_context (tools/workflow/workflow_mixin.py:444)
              → _embed_workflow_files (workflow_mixin.py:511)
              → _prepare_file_content_for_prompt (workflow_mixin.py:544)

_prepare_file_content_for_prompt
   → read_files (utils/file_utils.py:523)        # internally calls expand_paths
       → read_file_content (utils/file_utils.py:421)
           → header at file_utils.py:506-510      ← the single injection point
```

`read_files` (`file_utils.py:578`) calls `expand_paths` itself, so the loop at
`file_utils.py:588` iterates **already-resolved, individual** file paths
(`expand_paths` resolves each path via `resolve_and_validate_path`, `file_utils.py:351`).
Token estimation happens per file inside `read_file_content` (`file_utils.py:511`),
*after* the header is built — so anything added to the header is automatically counted.

### Schema surfacing (how the path fields are advertised)

This determines where `file_descriptions` must appear (decision: *mirror the path field*).

- Simple-tool path field lives in `SchemaBuilder.SIMPLE_FIELD_SCHEMAS["absolute_file_paths"]`
  (`tools/shared/schema_builders.py:47`) for tools built via `SchemaBuilder.build_schema`.
  **Chat hand-builds its schema** (`tools/chat.py:110` `get_input_schema`,
  `tools/chat.py:161` `get_tool_fields`) and lists each property explicitly.
- Workflow path fields live in
  `WorkflowSchemaBuilder.WORKFLOW_FIELD_SCHEMAS["relevant_files"|"files_checked"]`
  (`tools/workflow/schema_builders.py:46-55`), auto-merged into every workflow tool's
  schema.
- Field descriptions are centralised in `COMMON_FIELD_DESCRIPTIONS`
  (`base_models.py:22`) and `WORKFLOW_FIELD_DESCRIPTIONS` (`base_models.py:36`).

## Proposed design

### Field placement — mirror the path field, per family

`file_descriptions` rides alongside the path field in each family; it is **not** placed
on the universal `ToolRequest` (file-less tools — `version`, `listmodels`, `challenge` —
get nothing, consistent with how they get no path field today).

- **Pydantic models:** add `file_descriptions: Optional[dict[str, str]]` to `ChatRequest`
  and `WorkflowRequest`, with a `mode="before"` validator that coerces non-dict input to
  `{}` (mirrors the existing `convert_string_to_list` graceful-coercion pattern,
  `base_models.py:126-133`).
- **Schema dicts:** add a `file_descriptions` entry to `SIMPLE_FIELD_SCHEMAS` and
  `WORKFLOW_FIELD_SCHEMAS` (type `object`, `additionalProperties: {type: string}`),
  plus a `COMMON_FIELD_DESCRIPTIONS["file_descriptions"]` blurb shared by both.
- **Chat's hand-built schema:** add the same entry to both `get_input_schema` and
  `get_tool_fields`, beside `absolute_file_paths`.

### Accessors — mirror `get_request_files`

Add `get_request_file_descriptions(request) -> dict[str, str]` to `SimpleTool`
(`tools/simple/base.py`, beside `get_request_files`) and to the workflow mixin
(`tools/workflow/workflow_mixin.py`, beside `get_request_relevant_files`). Each returns
`request.file_descriptions or {}`, defensive against missing attribute.

### Threading

`build_standard_prompt` and `_embed_workflow_files` fetch the map via the accessor and
pass it as a new optional `descriptions` argument:

```
get_request_file_descriptions(request)
  → _prepare_file_content_for_prompt(..., descriptions=...)
  → read_files(..., descriptions=...)
  → (per file) read_file_content(..., description="<resolved string>")
```

`read_file_content` stays a **pure renderer**: it renders whatever string it is handed,
performing no lookup. All matching lives in `read_files` (the only layer that both holds
the raw map and knows the post-expansion resolved paths).

### Header rendering

In `read_file_content`, the successful header (`file_utils.py:506-510`) becomes:

```
--- BEGIN FILE: {file_path} (Last modified: {modified_at}) [{description}] ---
```

The ` [{description}]` segment is appended **only when** a non-empty description is
supplied; otherwise the header is emitted exactly as today (preserving the byte-for-byte
guarantee). The `--- END FILE ---` line is unchanged. Error / not-found / too-large
headers are **not** annotated (out of scope; keeps the guarantee trivial to assert).

### Key matching — normalize then match (in `read_files`)

User keys are raw (`~/proj/x.py`, relative segments, symlinks); the loop sees resolved
absolute paths. Before the read loop, `read_files` normalizes every description key
through the **same** resolution used for file paths (`os.path.expanduser` +
`resolve_and_validate_path`), producing a `resolved_key -> text` dict. Keys that fail
resolution are dropped (they cannot match any embedded file). Each iteration then looks
up its already-resolved `file_path`.

### Directory propagation & precedence

A key may resolve to a directory that `expand_paths` fanned out into many files. For each
expanded file the lookup is:

1. **Direct match** — the file's resolved path is itself a key → use it.
2. **Inherited match** — otherwise, find the **deepest ancestor directory** that is a key
   (ancestor test via path-prefix with a separator boundary, not a bare `startswith`) →
   use it.

A file's own key always wins over an inherited directory key (decision A3).

### Token accounting

No new logic. Because the header (now possibly carrying the description) is built before
`estimate_tokens(formatted)` (`file_utils.py:511`), description text is already inside the
per-file token estimate that drives truncation in `read_files`. A test asserts that a long
description raises the estimated token count.

### Dedup / continuation behaviour

`filter_new_files` (`base_tool.py:1077`) skips files already embedded earlier in a
continuation; such a file will not re-render its description on later turns. This is
accepted: descriptions are presentation-only and "travel with the first embed".

## Tool scope

- **In scope (render descriptions):** `chat`; the workflow tools that embed via
  `_embed_workflow_files` — `analyze`, `codereview`, `refactor`, `debug`, `precommit`,
  `thinkdeep`. (Workflow tools embed only on the final step / expert-analysis phase, per
  `_should_embed_files_in_workflow_step`, `workflow_mixin.py:481`.)
- **Out of scope:** `clink` declares `absolute_file_paths` but does **not** embed file
  content through `read_file_content` — it lists path *references* via
  `_format_file_references` (`tools/clink.py:344`) and hands paths to an external CLI.
  The header-injection mechanism therefore does not apply to it (see Not Doing).
- **Untouched:** file-less tools (`version`, `listmodels`, `challenge`, etc.).

## Backward compatibility

Path lists remain `list[str]`. `file_descriptions` is optional and defaults to empty.
With an empty/absent map, every threaded function behaves exactly as before and the
rendered output is byte-for-byte identical. No conversation-memory schema changes.

## Assumptions

- **A1 (must be true):** every in-scope tool embeds file content through
  `read_file_content`. Verified for `chat` and the workflow embed path; `clink` does not
  and is therefore excluded.
- **A2 (must be true):** resolving description keys with the *same* `expanduser` +
  `resolve_and_validate_path` used by `expand_paths` makes them match the post-expansion
  resolved paths the read loop iterates. If resolution diverges, annotations silently
  drop — covered by a key-normalization unit test.
- **A3 (decision):** a file's own description key overrides any inherited
  directory-level key.
- **A4 (should be true):** appending ` [desc]` to the header does not break any existing
  parser/consumer of the `--- BEGIN FILE ---` markers (they match the prefix, not the
  whole line). Existing file_utils tests guard this.

## Not Doing

- **Structured file objects** (`list[str | {path, description}]`) — rejected: a breaking
  schema change that disrupts dedup, conversation memory, and path expansion, all of
  which key on plain path strings.
- **Descriptions for `images`** — out of scope; images use a separate channel.
- **`clink` support** — clink passes path references to an external CLI rather than
  embedding content, so the BEGIN-FILE header injection does not apply. A parallel render
  inside `_format_file_references` is a possible later follow-up, deferred.
- **Surfacing descriptions in the workflow `file_context` response block** (the
  `fully_embedded` / `reference_only` summary) or in reference-only intermediate steps —
  this feature is embed-time only.
- **Annotating error / not-found / too-large file headers.**

## Resolved design decisions (from refinement)

- **Schema surface:** mirror the existing path field per family (not on `ToolRequest`).
- **Directory keys:** propagate to expanded children; child key beats inherited dir key.
- **Key matching:** normalize keys identically to file paths, then match on resolved path.

## Testing strategy

- **Unit (primary):** `read_file_content` (with/without description; byte-for-byte
  unchanged when absent), `read_files` (map application, key normalization including `~`
  and `..`, directory propagation + precedence, token-count increase), and
  `_prepare_file_content_for_prompt` pass-through. Per-family request/schema tests for
  `chat` and a representative workflow tool.
- **End-to-end (optional, deferred):** a simulator test analogous to
  `simulator_tests/test_*_validation.py` asserting an annotated header reaches the model.
  Deferred because simulator tests require live API keys and are excluded from
  `code_quality_checks.sh`; tracked as a follow-up, not an MVP gate.

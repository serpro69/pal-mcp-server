# Follow-ups reproduced by Task 39

The 2026-10-12 [synthetic capture](cache-capture-baseline.json) establishes the
following findings through the real dispatcher and serializer. These are bounded
follow-ups, deferred because Task 39 measures current behavior and the design
explicitly excludes unrelated prompt fixes from that slice. No runtime prompt
changes were made to conceal the measurements. They are separate from Tasks 40–41
and must be resolved or explicitly release-triaged during Task 42.

## Task C1: Restore chat web-search guidance once

**Status:** pending
**Evidence:** `investigations.chat_web_search_guidance_present=false` and
`chat_tool_specific_search_guidance_present=false`; the actual `chat_files`
request contains neither the shared web-search instruction nor ChatTool's guidance.
**Files:** `tools/chat.py:prepare_prompt`, `tools/simple/base.py` shared guidance,
`tests/cache_capture.py`.
**Next step:** Trace ChatTool's current prompt preparation and integrate the shared
guidance exactly once without duplicating system instructions or history. Add
dispatcher captures for first/continued chat and verify unchanged file budgets.

## Task C2: Resolve the unused thinkdeep customization hook

**Status:** pending
**Evidence:** `investigations.thinkdeep_customization_hook_called=false` after a
two-step expert workflow. The full base system instructions and work/history
sentinels do reach the provider; `customize_expert_analysis_prompt` is never invoked.
**Files:** `tools/thinkdeep.py:customize_expert_analysis_prompt`,
`tools/workflow/workflow_mixin.py:perform_expert_analysis`, capture tests.
**Next step:** Decide whether to wire the intended validation context into expert
prompt assembly or deliberately retire the unsupported hook. Verify problem
context, focus areas and findings each reach the real request without duplicates.
Do not delete it as incidental dead-code cleanup in another task.

## Task C3: Render codereview separators as actual newlines

**Status:** pending
**Evidence:** `investigations.codereview_contains_literal_newlines=true`; the
decoded provider request includes a literal backslash+n after the code-review
request heading, separate from JSON's normal wire escaping.
**Files:** `tools/codereview.py:prepare_expert_analysis_context`,
`_build_code_review_summary` and guidance builders; capture tests.
**Next step:** Replace accidental literal escapes at those construction sites and
assert decoded request headings contain real line breaks. Preserve caller findings,
history, referenced files and safety wording; recapture prefix metrics after the
behavioral regression tests pass.

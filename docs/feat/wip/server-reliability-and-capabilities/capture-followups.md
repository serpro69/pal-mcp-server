# Follow-ups reproduced by Task 39

The 2026-10-12 [synthetic capture](cache-capture-baseline.json) establishes the
following findings through the real dispatcher and serializer. These are bounded
follow-ups, deferred because Task 39 measures current behavior and the design
explicitly excludes unrelated prompt fixes from that slice. No runtime prompt
changes were made to conceal the measurements. They are separate from Tasks 40–41.
All three are now fixed in the user-authorized actionable follow-up batch;
[verification and remaining limits](actionable-fixes.md) are recorded separately
from Task 42, which remains pending.

## Task C1: Restore chat web-search guidance once

**Status:** done
**Evidence:** `investigations.chat_web_search_guidance_present=false` and
`chat_tool_specific_search_guidance_present=false`; the actual `chat_files`
request contains neither the shared web-search instruction nor ChatTool's guidance.
**Files:** `tools/chat.py:prepare_prompt`, `tools/simple/base.py` shared guidance,
`tests/cache_capture.py`.
**Resolution:** Include shared and chat-specific guidance in the system prompt,
which reaches both first and continued calls. Dispatcher/SDK captures assert each
instruction appears once across 14 chat requests; existing file/history and total
request-budget guards pass. Guidance tokens count toward the existing budgets.

## Task C2: Resolve the unused thinkdeep customization hook

**Status:** done
**Evidence:** `investigations.thinkdeep_customization_hook_called=false` after a
two-step expert workflow. The full base system instructions and work/history
sentinels do reach the provider; `customize_expert_analysis_prompt` is never invoked.
**Files:** `tools/thinkdeep.py:customize_expert_analysis_prompt`,
`tools/workflow/workflow_mixin.py:perform_expert_analysis`, capture tests.
**Resolution:** Call the customization hook before history deduplication and file
rendering. Preserve problem/focus scope in workflow state before expert execution;
fresh instances restore it, explicit values update/clear it and step 1 resets it.
The hook adds validation instructions and scope, leaving findings/context/files
to their existing renderers. First-call, continuation and reused-object tests plus
real SDK captures cover delivery without duplicating the current findings.

## Task C3: Render codereview separators as actual newlines

**Status:** done
**Evidence:** `investigations.codereview_contains_literal_newlines=true`; the
decoded provider request includes a literal backslash+n after the code-review
request heading, separate from JSON's normal wire escaping.
**Files:** `tools/codereview.py:prepare_expert_analysis_context`,
`_build_code_review_summary` and guidance builders; capture tests.
**Resolution:** Replace accidental literal escapes throughout codereview context,
summary and caller-guidance construction. Captures assert real heading separators,
preserved instructions, findings and file content. Legitimate JSON escapes in
stored local replies remain intact. Updated prefix measurements are retained in
[prompt-fixes-capture.json](prompt-fixes-capture.json); historical baselines are unchanged.

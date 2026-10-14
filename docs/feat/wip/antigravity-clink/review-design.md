# Antigravity clink design review — 2026-10-14

> Scope: design.md, implementation.md, tasks.md; capability evidence and current code as context
> Checkout: `c1c2521` before Task 2 production changes
> Method: `kk:review-design:isolated`
> Final assessment: SOUND after corrections

This is a fresh review of the approved inherited-permissions contract and the
enterprise-evidence Task 1 boundary. It does not reconstruct the missing historical
2026-10-12 report or certify its findings.

The independent design-reviewer initially found one P1 and two P2 issues. The
implementing session corrected them, and the reviewer approved a narrow recheck
with no remaining P0–P3 findings or Task 2 design blocker.

| Finding | Evidence and correction |
| --- | --- |
| P1 INCONSISTENT: obsolete conversation lifecycle | The plan named `conversation_request`/`invocation_history` and preflight user-turn persistence. Current clink uses coordinated public execution, `CLIConversation`, bounded history, and atomic success-only recording. Component boundaries, diagram, A5, and Task 4/5 test anchors now match that implementation. No conversation refactor is required. |
| P2 INCOMPLETE: argument contract lacked exact spellings | The plan named broad rejection categories without aliases or `--` handling. The design now records the 1.3.5 rejection table, split/equals forms, defensive single-dash spellings, all configured argument sources including inactive buckets, canonical internal arguments, output-file rejection, and permitted model/timeout options. |
| P2 AMBIGUOUS: runtime versus cached default selection | The plan's Gemini-default wording could imply implicit selection on new multi-client calls. It now preserves explicit selection for those calls, inherited successful continuation selection, sole-client selection, and the separate cached Gemini metadata default. |

The external PAL expert attempt (`gemini-3.8-flash`) failed because its Gemini API
key was invalid. It provides no corroboration; the independent design-reviewer is
the available isolated checkpoint. No systemic finding required knowledge indexing.

The reviewer checked architecture and configuration boundaries, task dependencies,
assumptions, failure handling, permission semantics, and release gates against the
current repository. It did not run tests or live probes, independently reproduce
authentication/timings/settings hashes, or retrieve the CLI alias inventory. The
implementing session inspected native 1.3.5 help and current official protocol docs.
Task 6's authentication and runtime acceptance requirements remain unverified.

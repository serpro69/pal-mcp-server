# Tasks: Per-file descriptions / annotations

> Design: [design.md](./design.md)
> Implementation: [implementation.md](./implementation.md)
> Status: in progress
> Created: 2026-06-06 · Revised: 2026-11-23
> Not Doing: structured path objects, images, CLI references, provider-free workflow output, general annotation history, file snapshots, error-header annotations, directory-as-unit display

The November review revised the plan before implementation. Every checkbox below tracks implementation work. [Review dispositions](./review-design.md)

## Task 1 — Bind and render eligible attachments

- **Status:** done
- **Depends on:** —
- **Size:** M
- **Slicing:** Contract-first. Establish one renderer and the real model admission boundary for all tool paths.
- **Can run in parallel with:** —
- **Docs:** [Binding, rendering and admission](./implementation.md#task-1--bind-descriptions-and-admit-the-rendered-files)

- [x] 1.1 In `utils/file_utils.py`, bind direct and deepest-directory keys to the current attachment set, returning sanitized exact canonical file keys. Cover aliases, component boundaries, conflicts, empty/unmatched keys and directory inheritance.
- [x] 1.2 Add the optional successful-header annotation to `read_file_content` and exact lookups in `read_files`. Verify delimiter safety, the 200-character cap, byte-identical absent/empty output and plain diagnostic headers.
- [x] 1.3 Pass the optional map through `BaseTool._prepare_file_content_for_prompt` and align its workflow mixin signature. Exercise `FileReadReport` with the chosen model estimator at the fit/overage boundary in `tests/test_file_size_gate.py`.

**Execution context**

- 2026-11-23, observed at `cf715ec`. The shared reader measures complete blocks and the preparation method already passes that report to admission.

- 2026-11-23, verified with the repository quality suite and 12 focused file-gate tests. Independent review identified an inaccessible-child failure and repeated cleaning of inherited labels. Both fixes passed the focused suite and re-review.

## Task 2 — Ship chat on initial and continued requests

- **Status:** done
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Chat delivery and first-embed eligibility](./implementation.md#task-2--deliver-chat-annotations-on-the-first-request-and-on-continuations)

- [x] 2.1 Add the optional string-map request field and both chat schema definitions. Verify valid and malformed inputs through MCP and Pydantic. Update the chat expectation in `tests/test_tool_surface.py` in this commit.
- [x] 2.2 Exclude the incoming map from initial thread context in `conversation_request`. Wire first-call `build_standard_prompt` and continuation `invocation_history` → `build_conversation_history` → `read_files`. Use the current attachment candidates after prompt-file extraction.
- [x] 2.3 Record the final model prompt's included paths on the existing successful turn writes for simple tools and workflows. Exclude preflight, reference-only and failed operations. Derive eligibility from prior committed inclusions across the chain. Distinguish explicit empty evidence and legacy replies. Preserve existing attachment tracking.
- [x] 2.4 Capture a turn-two request combining a repeated alias and a new file. Check annotations, order, counts, no duplicated contents and no replay of stored chat labels. Cover prior unavailable/reference-only files, legacy fallback and the no-provider-call overage boundary.

**Execution context**

- 2026-11-23, observed at `fe0715a`. Continuations render the newly saved input alongside historical attachments. Only a committed model reply establishes prior inclusion.

- 2026-11-23, verified. The quality suite passed (3,753 unit tests), as did 36 focused checks after the structured-error fix and all three stub simulations. The live integration suite passed four and skipped 22. The quick simulator passed four of six with no live model configured. Independent re-review approved the corrected inclusion evidence.

## Task 3 — Ship the generic expert workflows

- **Status:** pending
- **Depends on:** Task 2
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Generic expert delivery and replay](./implementation.md#task-3--deliver-descriptions-to-generic-workflow-experts)

- [ ] 3.1 Opt the seven generic expert requests and schemas into the shared property. Add the corresponding seven expected properties in `tests/test_tool_surface.py`.
- [ ] 3.2 Bind each step's map to its expanded `relevant_files` in `execute_workflow` before saving step data. Merge effective canonical entries in `_update_consolidated_findings`, freeze delivered labels and replay older steps with an empty default.
- [ ] 3.3 Pass the consolidated map to final-step preparation and to the expert delegate through `_prepare_files_for_expert_analysis` and `_force_embed_files_for_expert_analysis`. Preserve the shared gate, ordering and diagnostics.
- [ ] 3.4 Capture a multi-step expert prompt with inherited, overridden and history-only sibling files. Check canonical ordering of three alias updates, same-tool replay, cross-tool isolation, boundedness and absence of raw/unmatched data in initial context, saved state and opt-in transcripts. Exercise the existing failed-state branch.

## Task 4 — Ship debug's custom expert path

- **Status:** pending
- **Depends on:** Task 3
- **Size:** S
- **Can run in parallel with:** Task 5
- **Docs:** [Debug expert block](./implementation.md#task-4--deliver-descriptions-in-debugs-custom-expert-block)

- [ ] 4.1 Opt in `DebugInvestigationRequest` and the debug schema. Use the consolidated exact map in `DebugIssueTool.prepare_expert_analysis_context`. Update the debug tool-surface expectation.
- [ ] 4.2 Capture a continued debug call and assert one annotated essential-files block in the expert prompt, plus the existing no-description output and coverage counts.

## Task 5 — Ship blinded consensus participant annotations

- **Status:** pending
- **Depends on:** Task 3
- **Size:** S
- **Can run in parallel with:** Task 4
- **Docs:** [Consensus consultation](./implementation.md#task-5--deliver-request-local-descriptions-to-consensus-participants)

- [ ] 5.1 Opt in `ConsensusRequest` and its schema, updating the consensus tool-surface expectation. Pass that participant request's effective map to `_consult_model`'s existing blinded preparation call. Exclude consensus maps from generic replay.
- [ ] 5.2 Capture two participants with the same attachment, distinct inputs and separate budgets. Verify participant-local labels, omission on the second request, exclusion of hidden history and the existing per-participant failure when the annotated input exceeds its allowance.

## Task 6 — Verify and document

- **Status:** pending
- **Depends on:** Tasks 1–5
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Final verification](./implementation.md#task-6--verify-the-public-surface-and-document-usage)

- [ ] 6.1 Run `$kk:test` and the repository quality suite. Check the exact set of ten advertised tools, exclusions, byte ceilings, existing file-count contracts and captured provider prompts. Keep the historical schema baseline intact.
- [ ] 6.2 Use `$kk:document` for a multi-file usage example, scope, precedence, continuation and retention behavior.
- [ ] 6.3 Run `$kk:review-code` for Python and `$kk:review-spec` against these documents. Move the completed feature to `docs/feat/done` after closing the implementation tasks and update incoming links.

## Dependency Graph

```text
Task 1 → Task 2 → Task 3 ┬→ Task 4 ─┬→ Task 6
                         └→ Task 5 ─┘
```

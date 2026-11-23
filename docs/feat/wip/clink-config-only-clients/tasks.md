# Tasks for config-only clink clients

> [Design](./design.md) · [Implementation plan](./implementation.md) · [Issue #33](https://github.com/serpro69/pal-mcp-server/issues/33)
>
> **Status:** in-progress. **Created:** 2026-11-23.
>
> **Not Doing:** executable parser plugins, interactive or event-stream protocols, vendor presets, per-path enforcement.

## Task 1 — Validate the manifest contract

- **Status:** done
- **Depends on:** —
- **Size:** M
- **Can run in parallel with:** —
- **Strategy:** Contract-first
- **Docs:** [Manifest resolution](./implementation.md#resolve-a-custom-manifest)

- [x] In `clink/models.py`, validate custom input before coercion. Cover unknown fields, empty versus missing `safe_args`, process-safe argv and environment strings, whole-name prompt flag conflicts, output, workspace, hint and the optional stderr switch.
- [x] In `clink/registry.py`, validate the selected source and every discovered file, then publish the complete registry. Preserve valid shipped overrides. Add the bundled `generic_default.txt` and its single baseline digest. Resolve generic fallback roles directly against that resource.
- [x] Exercise a missing explicit source, absent optional personal directory, malformed JSON, duplicate permission keys, empty and field-invalid later overrides. Check startup exit before handshake, safe diagnostics in both sinks, valid replacement, and successful registration with a missing executable.

**Execution context**

- 2026-11-23, observed at `2b00f454`. The registry skipped empty or unreadable manifests and updated its live dictionary while loading. Task 1 covers registration, startup and discovery. Generic execution starts in Task 2.
- 2026-11-23, baseline. The repository quality checks passed using the existing Python 3.11 feature environment with MCP 2.3.0. The virtualenvwrapper `pal` environment failed during collection because its MCP package lacks `ServerRequestContext`.
- 2026-11-23, verified on the final candidate. All 220 focused checks passed, including real stdio discovery and startup failures on both diagnostic sinks. The repository quality script passed with 3,669 tests passed, 17 skipped and 26 integration tests deselected. The credential-free integration simulator (`./run_integration_tests.sh --stub`) passed.
- 2026-11-23, review. The independent Codex reviewer approved the corrections for Unicode name replacement, non-finite JSON coordinates and per-file access errors. Regression cases are in `tests/test_clink_manifests.py`.
- 2026-11-23, review follow-up. Claude identified that an invalid environment key could contain a credential assignment. Startup tests now check both sinks for generic and shipped environment errors and nested role errors. The registry groups missing fields and records the source of each valid override. Hidden `*.json` entries follow the same validation rules as other discovered manifests. Codex and Claude approved the final Task 1 source.
- 2026-11-23, subsequent tasks. Task 2 can enable stdin/text with workspace `any`. The pre-launch guard still applies to flag or JSON clients until Task 3, project workspaces until Task 4, and opt-in failure logging until Task 5.
- 2026-11-23, verification limit. Automatic approval review blocked the live integration and quick simulator commands because they may transmit test data to external model providers. The Task 6 owner can run both in an authorized provider environment and record passes or unavailable prerequisites there.

## Task 2 — Run the stdin and text example

- **Status:** pending
- **Depends on:** Task 1
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Text execution](./implementation.md#run-a-text-client-safely)

- [ ] In `clink/availability.py` and `clink/agents/base.py`, make discovery and launch use the same absolute executable resolved against PAL's startup directory. Cover a relative command and a bare command on a relative PATH with different server and project directories.
- [ ] Route custom stdin/text through the shared process lifecycle and a text parser. In `tools/clink.py`, select neutral guidance even for custom roles. Inspect prepared prompts for both the generic default and an operator role.
- [ ] In the custom validation and execution paths, gate edit arguments, reject unsupported scoped paths and enforce known default-mode argument checks for every role. Keep executable, exit and parse errors actionable.
- [ ] In the generic runner, bound both streams during concurrent I/O. Cover exact-limit success, excess on either pipe, invalid UTF-8, timeout and cancellation. Verify reaping and scratch cleanup.
- [ ] In `tools/clink.py` and agent logging, retain structured stage, reason and hint once the client is selected. Keep raw streams out of MCP and default logs. Preserve the success-only commit and post-success persistence warning.
- [ ] Exercise a fixture subprocess through clink for exact stdin, a supplied or missing role file, safe/edit argv, filtered credentials, continuation and a nonzero exit with plausible-looking stdout.

## Task 3 — Run flag transport and parse JSON

- **Status:** pending
- **Depends on:** Task 2
- **Size:** M
- **Can run in parallel with:** Task 4
- **Docs:** [Flag prompts and JSON](./implementation.md#deliver-flag-prompts-and-extract-json)

- [ ] Implement flag delivery in the generic runner and literal-key traversal in the generic parser. Use a fixture variant with `workspace: "any"` so this task can run alongside Task 4. The exact published example runs in Task 6.
- [ ] Verify prompt boundaries, EOF, error reporting for U+0000 and OS argument-length errors, and history preservation on a failed follow-up. Cover Windows batch rejection and check representative literal flags (`-q`, `-question`, `--question`) and distinct names such as `-quiet`.
- [ ] Verify a nested JSON result, missing field and unencodable result, plus flag/text and stdin/JSON. Exercise a prompt echo on failure under the default diagnostic policy from Task 2.

## Task 4 — Apply directory requirements to a call

- **Status:** pending
- **Depends on:** Task 2
- **Size:** M
- **Can run in parallel with:** Task 3
- **Docs:** [Workspace enforcement](./implementation.md#enforce-the-selected-workspace)

- [ ] In `CLinkTool._execute_coordinated`, apply `any` or `project` immediately after directory selection. Distinguish new scratch from inherited scratch in the caller's recovery message. Native CLIs handle repository access failures through their normal exit path.
- [ ] Verify rejection before launch, explicit promotion of saved scratch on the same thread, acceptance of an ordinary directory, and recovery from a native failure in a bound project. A failed call must preserve the saved binding and history.

## Task 5 — Deliver opt-in private stderr diagnostics

- **Status:** pending
- **Depends on:** Tasks 3 and 4
- **Size:** M
- **Can run in parallel with:** —
- **Docs:** [Private failure diagnostics](./implementation.md#private-failure-diagnostics)

- [ ] Pass complete, size-limited stderr from a failed generic call to the opted-in local filter. Use the request's actual prompt and effective child environment. Apply redaction before tail selection and cap the rendered record.
- [ ] Route excerpt records only to the main rotating log. Suppress the handler's stderr fallback for those records on write or rotation failure. Preserve the original CLI error and normal logging for other records.
- [ ] Check the main file, activity file, host stderr and MCP response on opt-in, default-off, success, overflow and file-sink failure. Include overlapping sensitive values and an echo crossing the truncation boundary.

## Task 6 — Document and verify the feature

- **Status:** pending
- **Depends on:** Task 5
- **Size:** S
- **Can run in parallel with:** —
- **Docs:** [Documentation and release checks](./implementation.md#operator-documentation-and-release-checks)

- [ ] Use `$kk:document` to publish both manifests and their troubleshooting rules. Cover startup stderr, hidden `*.json` entries and UTF-8 byte-order marks, optional local failure details, executable and script path bases, argv-preserving launchers, platform size limits, smaller history budgets, restart effects and directory recovery.
- [ ] Use `$kk:test` to run the **exact published manifests** through the local stdio MCP harness. Supply named executable fixtures on an isolated PATH and a Git worktree for `team-review`. Verify registration in the schema and provider-free discovery, both replies and a dependent follow-up. Check the installed distribution can resolve the bundled neutral prompt.
- [ ] Run the focused subprocess, directory, environment, parser and history checks plus the repository quality, integration and quick simulator commands. Record live prerequisites or skips. The installed-vendor simulator needs a new case only if a shipped adapter changes in a way the existing checks cannot verify.
- [ ] Verify the four shipped clients and stock schema against the existing baseline. Exercise a native adapter directly if its invocation behavior changed or its regression tests leave an unresolved risk.
- [ ] Run `$kk:review-code` with Python and `$kk:review-spec` against this design. Verify the accepted empty-list behavior and the separation between caller-visible diagnostics and the opt-in operator log.

## Dependency Graph

```text
1 ── 2 ──┬── 3 ──┐
         └── 4 ──┴── 5 ── 6
```

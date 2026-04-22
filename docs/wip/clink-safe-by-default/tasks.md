# Tasks: clink Safe-by-Default CLI Execution

> Design: [./design.md](./design.md)
> Implementation: [./implementation.md](./implementation.md)
> Status: pending
> Created: 2026-04-22

## Task 1: Extend config model and registry with safe_args / edit_args
- **Status:** done
- **Depends on:** —
- **Docs:** [implementation.md#1-extend-the-cli-client-config-model](./implementation.md#1-extend-the-cli-client-config-model), [#2](./implementation.md#2-propagate-new-fields-through-the-registry)

### Subtasks
- [x] 1.1 Add optional `safe_args: list[str]` and `edit_args: list[str]` fields (default `[]`) to `CLIClientConfig` in `clink/models.py`, with a `field_validator(mode="before")` that accepts a list or single string (mirror the existing `additional_args` validator).
- [x] 1.2 Add the same two fields (default `[]`) to `ResolvedCLIClient` in `clink/models.py`.
- [x] 1.3 In `clink/registry.py` `_resolve_config`, read `raw.safe_args` / `raw.edit_args` and pass them into the `ResolvedCLIClient(...)` constructor.
- [x] 1.4 Verify: unit test loading a config with `safe_args`/`edit_args` populated — assert they round-trip into `ResolvedCLIClient`.

## Task 2: Base agent plumbing for allow_edits and path restrictions
- **Status:** done
- **Depends on:** Task 1
- **Docs:** [implementation.md#3-update-base-agent-command-construction](./implementation.md#3-update-base-agent-command-construction)

### Subtasks
- [x] 2.1 In `clink/agents/base.py`, add class attribute `supports_path_restrictions: bool = False` to `BaseCLIAgent`.
- [x] 2.2 Add method `_build_path_restriction_args(self, editable_paths: Sequence[str], *, allow_edits: bool) -> list[str]` returning `[]`.
- [x] 2.3 Add method `_extra_command_args(self, *, system_prompt: str | None) -> list[str]` returning `[]` (hook for Claude's `--append-system-prompt`).
- [x] 2.4 Extend `run(...)` with kwargs `allow_edits: bool = False`, `editable_paths: Sequence[str] = ()`; pass them to `_build_command`.
- [x] 2.5 Rewrite `_build_command` to compose: `executable + internal_args + config_args + (edit_args if allow_edits else safe_args) + _extra_command_args(system_prompt=...) + _build_path_restriction_args(...) + role.role_args`.
- [x] 2.6 Verify: unit test with a stub `ResolvedCLIClient` asserting command list for both `allow_edits=False` and `True`.

## Task 3: Claude agent — adopt base, add path restrictions, keep system-prompt injection
- **Status:** done
- **Depends on:** Task 2
- **Docs:** [implementation.md#4-simplify-claudeagent](./implementation.md#4-simplify-claudeagent)

### Subtasks
- [x] 3.1 Remove `ClaudeAgent._build_command` override in `clink/agents/claude.py`.
- [x] 3.2 Set `supports_path_restrictions = True` on `ClaudeAgent`.
- [x] 3.3 Override `_extra_command_args` to inject `["--append-system-prompt", system_prompt]` when `system_prompt` is non-empty and `--append-system-prompt` is not already in `config_args` / `safe_args` / `edit_args`.
- [x] 3.4 Override `_build_path_restriction_args`: when `allow_edits=True` and paths non-empty, emit `["--allowedTools", f"Edit({path})", "--allowedTools", f"Write({path})"]` for each path.
- [x] 3.5 Verify: unit test builds a Claude command with `allow_edits=True` + two `editable_paths` and checks the expected flags + order.

## Task 4: Migrate CLI config JSON files
- **Status:** done
- **Depends on:** Task 1
- **Docs:** [implementation.md#5-migrate-cli-config-json-files](./implementation.md#5-migrate-cli-config-json-files)

### Subtasks
- [x] 4.1 `conf/cli_clients/claude.json`: remove `--permission-mode acceptEdits` from `additional_args`; add `safe_args: ["--permission-mode", "default"]` and `edit_args: ["--permission-mode", "acceptEdits"]`. Keep `--model sonnet` in `additional_args`.
- [x] 4.2 `conf/cli_clients/gemini.json`: remove `--yolo` from `additional_args`; add `edit_args: ["--yolo"]`.
- [x] 4.3 `conf/cli_clients/codex.json`: remove `--dangerously-bypass-approvals-and-sandbox` from `additional_args`; add `edit_args: ["--dangerously-bypass-approvals-and-sandbox"]`. Keep `--json` and `--enable web_search_request` in `additional_args`.
- [x] 4.4 Verify: registry loads all three configs without error and each shows populated `safe_args`/`edit_args` as expected.

## Task 5: CLinkRequest schema — allow_edits and editable_paths
- **Status:** pending
- **Depends on:** —
- **Docs:** [implementation.md#6-update-toolsclinkpy-request-and-schema](./implementation.md#6-update-toolsclinkpy-request-and-schema)

### Subtasks
- [ ] 5.1 In `tools/clink.py`, add `allow_edits: bool = Field(default=False, description=...)` and `editable_paths: list[str] = Field(default_factory=list, description=...)` to `CLinkRequest`.
- [ ] 5.2 Extend `get_input_schema()` `properties` with matching `allow_edits` (boolean) and `editable_paths` (array of strings) entries.
- [ ] 5.3 Verify: `CLinkTool().get_input_schema()["properties"]` contains both keys.

## Task 6: Request validation and agent support check
- **Status:** pending
- **Depends on:** Task 3, Task 5
- **Docs:** [implementation.md#7-validate-the-request](./implementation.md#7-validate-the-request)

### Subtasks
- [ ] 6.1 In `execute()`, after parsing the request: if `request.editable_paths` is non-empty and `request.allow_edits` is False → `_raise_tool_error("editable_paths can only be used when allow_edits=true.")`.
- [ ] 6.2 Add helper `_validate_editable_paths(request)` in `CLinkTool` that returns an error string for any relative path.
- [ ] 6.3 After resolving `client_config`, look up the agent class for the selected CLI (reuse whatever `create_agent` does or expose it); if `editable_paths` is non-empty and agent `supports_path_restrictions` is False → `_raise_tool_error` naming the CLI.
- [ ] 6.4 Verify: unit tests drive each failure path and confirm a valid Claude + absolute path + `allow_edits=True` passes.

## Task 7: Wire allow_edits / editable_paths into agent.run and harden prompt
- **Status:** pending
- **Depends on:** Task 2, Task 5
- **Docs:** [implementation.md#8-prompt-hardening](./implementation.md#8-prompt-hardening), [#9](./implementation.md#9-wire-the-new-fields-through-to-the-agent)

### Subtasks
- [ ] 7.1 In `CLinkTool.execute()`, pass `allow_edits=request.allow_edits, editable_paths=request.editable_paths` into `agent.run(...)`.
- [ ] 7.2 In `_prepare_prompt_for_role`, change the user section header from `=== USER REQUEST ===` to `=== UNTRUSTED USER REQUEST ===`.
- [ ] 7.3 In the same function, when `request.allow_edits` is False, append an `=== EXECUTION POLICY ===` section with text instructing the CLI not to modify the filesystem.
- [ ] 7.4 Verify: unit test asserting the new section headers and that `EXECUTION POLICY` only appears when `allow_edits=False`.

## Task 8: Unit tests
- **Status:** pending
- **Depends on:** Task 3, Task 4, Task 6, Task 7
- **Docs:** [implementation.md#10-tests](./implementation.md#10-tests)

### Subtasks
- [ ] 8.1 Add/extend `tests/test_clink_tool.py` — default request omits `edit_args` for all three CLIs; `allow_edits=True` includes `edit_args`.
- [ ] 8.2 Test: Claude with two absolute `editable_paths` emits the correct `--allowedTools Edit(...)` / `Write(...)` entries.
- [ ] 8.3 Test: Gemini / Codex with `editable_paths` errors cleanly.
- [ ] 8.4 Test: relative path in `editable_paths` errors.
- [ ] 8.5 Test: `editable_paths` without `allow_edits` errors.
- [ ] 8.6 Test: prompt always includes `UNTRUSTED USER REQUEST`; `EXECUTION POLICY` conditional on `allow_edits=False`.
- [ ] 8.7 Audit existing `tests/` for assertions on the old `USER REQUEST` header and update.

## Task 9: Final verification
- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8

### Subtasks
- [ ] 9.1 Run `test` skill to verify all tasks — full unit test suite via `./code_quality_checks.sh`.
- [ ] 9.2 Run `document` skill to update any relevant docs (README, SECURITY.md if it references clink, etc.).
- [ ] 9.3 Run `review-code` skill with Python as the project language input to review the implementation.
- [ ] 9.4 Run `review-spec` skill to verify implementation matches design and implementation docs.

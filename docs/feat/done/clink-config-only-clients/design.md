# Config-only clink clients

A PAL operator can register a noninteractive CLI by installing its executable, placing one complete JSON manifest in `~/.pal/cli_clients/` or `CLI_CLIENTS_CONFIG_PATH`, and restarting PAL. Success means that clink advertises the new name, executes it, and returns its answer through the usual MCP call. Valid overrides for the four shipped clients keep their native adapters and precedence. The stricter startup checks and executable lookup below also apply to those clients. [Issue #33](https://github.com/serpro69/pal-mcp-server/issues/33) · [Upstream use cases](https://github.com/BeehiveInnovations/pal-mcp-server/issues/357)

## Two complete manifests

Both commands are illustrative test executables. The first reads a UTF-8 prompt through EOF, prints an answer, and is read-only by default.

```json
{
  "name": "team-ask",
  "command": "team-ask",
  "safe_args": [],
  "edit_args": [],
  "env_passthrough": [],
  "custom": {
    "prompt": {"mode": "stdin"},
    "output": {"format": "text"},
    "workspace": "any",
    "failure_hint": "Check that team-ask is installed and configured."
  }
}
```

The second takes the complete prompt as the value of `--question` and requires a project directory. The example CLI checks its own Git requirements and returns an object such as `{"reply":{"text":"Ready"}}` on success. The caller receives `Ready`.

```json
{
  "name": "team-review",
  "command": "team-review",
  "safe_args": ["--read-only"],
  "edit_args": ["--apply"],
  "env_passthrough": ["TEAM_REVIEW_TOKEN"],
  "custom": {
    "prompt": {"mode": "flag", "flag": "--question"},
    "output": {"format": "json", "path": ["reply", "text"]},
    "workspace": "project",
    "failure_hint": "Check the team-review login and that it can access a Git worktree in the selected directory."
  }
}
```

## Manifest contract

| Decision | Behavior |
| --- | --- |
| Registration | An unknown name requires the six top-level fields and four `custom` fields shown above. Reject unknown keys at the top level, in the protocol and in role definitions for generic clients. Shipped names retain their existing field rules and native adapters. A complete later definition replaces an earlier one by case-insensitive name. |
| Executable | Discovery and invocation resolve the first command token against PAL's startup directory and the client's effective lookup path. Launch the absolute match in the selected workspace. Use an absolute script operand when invoking an operator-installed script through an interpreter. |
| Edit permissions | Omitted or false `allow_edits` selects `safe_args`. Writing `"safe_args": []` asserts the CLI's native defaults are safe. Omitting `safe_args` or setting it to `null` is invalid. True selects `edit_args`. An empty edit list declares a read-only integration and an edit request fails before launch. The operator owns the meaning of the flags and saved permissions. Generic clients reject scoped `editable_paths`. |
| Prompt and output | Send the prepared role, history and user prompt exactly once. Flag mode passes the declared option and one whole prompt value, then closes stdin. It requires an argv-preserving launcher and can expose the prompt in process listings. Reject U+0000 anywhere in the prepared flag value before launch. Text mode returns nonblank stdout. JSON mode follows literal object keys to a nonblank string. |
| Directory | `any` accepts clink's selected directory or scratch. `project` requires a persistent directory. A caller can promote a saved scratch conversation by supplying `working_directory_absolute_path` on that follow-up. Changing an already bound project requires a new conversation. A Git-dependent CLI enforces its own repository rules. Its operator declares that prerequisite in `failure_hint`. |
| Environment and roles | Inherit the shared runtime allowlist and the names in `env_passthrough`, then apply explicit `env` values. An empty list adds no vendor credentials. Generic clients inherit a neutral default role. PAL's appended guidance also stays neutral for operator-supplied roles. A text-only client must be able to answer from the supplied material. |
| Failure | Once PAL selects a generic client, its validation and execution errors include the client name, stage, reason, available exit code and hint. A nonzero exit always fails. On exit zero, output must be valid UTF-8. A missing JSON key, wrong type, blank reply or malformed document is a parse error. Failure before commit preserves history and the directory binding. An existing PAL persistence warning after a successful reply keeps its normal meaning. |

## Startup and troubleshooting

PAL loads shipped manifests, then the file or directory selected by `CLI_CLIENTS_CONFIG_PATH`, then personal overrides in `~/.pal/cli_clients/`. An absent optional personal directory is normal. An explicitly selected source must exist and be readable. Every discovered JSON manifest must be readable, use each member name once per object and pass validation. A missing or ambiguous replacement could otherwise activate unintended permissions. PAL therefore stops **the entire server before the MCP handshake** on a source or manifest error. The MCP host's stderr and PAL's main log identify the path and JSON line/column, field location or read error. Correct the source and restart PAL. A valid manifest whose executable is missing still loads, and discovery reports the executable as missing.

Flag-mode launch limits apply to the **assembled** prompt, including history. A shorter request may fit on the same thread. If retained history alone is too large, start a new conversation with a short summary. Operators can lower `history_token_budget` for future sessions and restart PAL. Restarting discards existing continuation IDs. See the [platform limits and recovery rules](./implementation.md#deliver-flag-prompts-and-extract-json).

A child can echo role instructions, earlier turns, file content or credentials it obtained itself. Generic MCP diagnostics therefore contain PAL's failure stage and hint. The default server log records the stage, client, and exit code when available. For hands-on diagnosis an operator may explicitly set `custom.log_stderr_on_failure` to `true` for one trusted CLI and restart PAL. Only PAL's local main log receives a bounded, filtered stderr excerpt from a failed call. A CLI can print sensitive data in forms PAL cannot recognize. Use a benign request and restrict access to the log when enabling this option. Find `mcp_server.log` with `python -m utils.log_dir`.

Generic clients may produce up to 10 MiB on each output stream per call. Crossing either limit fails the call and reaps the child. This bounds the data PAL parses or filters. The existing, smaller MCP reply cap still applies to successful answers.

## Assumptions

The operator controls the executable and manifest and can identify its safe mode. The supported CLIs finish after one prompt and emit their answer on stdout. Two local fixture executables can exercise these contracts without credentials. Compatibility with a named third-party product requires a separate check of that product's actual CLI.

## Not Doing

Executable parser plugins, interactive sessions and event-stream protocols need a different extension contract. Vendor presets need verified vendor behavior. Per-path write enforcement needs native CLI support. These stay outside issue #33.

## Rejected alternatives

Loading Python parsers from manifests, as proposed upstream, would run operator extensions inside the PAL server. Reusing only the shipped parser names would make an internal CLI imitate a vendor's wire format. A declarative text or JSON result covers both examples and keeps the interface small.

A PAL-side Git probe would add a second repository policy. Git can refuse an existing repository because of its ownership, and the target CLI may apply its own trust rules. Requiring a project before launch and reporting the CLI's own failure preserves that distinction. [Git ownership checks](https://git-scm.com/docs/git-config#Documentation/git-config.txt-safedirectory)

[Implementation plan](./implementation.md) · [Tasks](./tasks.md) · [Review findings and resolutions](./review-resolution.md)

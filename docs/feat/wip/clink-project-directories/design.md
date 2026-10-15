# Design: per-request project directories for clink

> Status: proposed; ready for independent design review, not implemented
> Created: 2026-10-15
> Source baseline: `6e6ea4a`
> Implementation: [implementation.md](implementation.md)
> Tasks: [tasks.md](tasks.md)
> Evidence and limitations: [verification.md](verification.md)

## Problem and outcome

Developers using PAL from several projects need a delegated CLI to start in the
project selected by the calling agent. Today, clink uses a directory in its CLI
manifest or creates a temporary directory. A user-level manifest pins every PAL
instance using that home directory to the same project; per-instance manifests
require separate project configuration. Absolute file references do not change
the child's working directory.

Add an optional project directory to the shared clink request. One PAL process
must accept overlapping conversations for projects A and B, with each child
observing its own requested directory and neither changing the server directory
or another request's configuration. Calls that do not select a project retain
manifest and temporary-directory fallback on their first clink invocation.

The user explicitly chose: **reuse the project on continuation; require a new
conversation to change projects**. This applies across CLI and role changes.
Success is demonstrated by real child-process cwd and file-marker observations,
successful continuation in the original project, and rejection of a project
change before any child starts. A generated answer alone is insufficient evidence.

## Existing mechanisms

The following are source observations, independently checked in
[isolated verification](verification.md#isolated-verification), not claims that
the new feature already exists.

| Mechanism | Current source and behavior |
| --- | --- |
| Request boundary | [CLinkRequest and CLinkTool](../../../../tools/clink.py) define a custom MCP schema without a directory field; direct and MCP calls enter the same execution path. |
| Configuration | [registry](../../../../clink/registry.py) loads built-ins, `CLI_CLIENTS_CONFIG_PATH`, then user manifests. Later client definitions replace earlier definitions. Cached client objects are mutable and shared. |
| Execution | [BaseCLIAgent](../../../../clink/agents/base.py) passes configured cwd or a fresh temporary directory to the subprocess. It preserves configured directories and cleans up scratch directories. |
| Adapter coupling | [CodexAgent](../../../../clink/agents/codex.py) checks `client.working_dir` when deciding whether to add `--skip-git-repo-check`. The other adapters use the shared directory lifecycle. |
| Conversation state | [CLIConversation](../../../../clink/conversation.py) loads a snapshot and atomically appends both turns on success. It replaces `initial_context.clink_defaults` with successful CLI/role selection. |
| Concurrency | The existing [coordinator](../../../../utils/conversation_coordination.py) admits one invocation per continuation ID. Different conversations can overlap, including in the same directory. |

This feature extends shared clink behavior. It does not reopen the independent
[Antigravity adapter work](../antigravity-clink/design.md) or the completed
[CLI-only conversation feature](../../done/cli-only-clink/design.md).

## Request and directory selection

Add optional `working_dir` to both `CLinkRequest` and `get_input_schema()`. Use the
same name as the existing manifest field. Its value is a strict string containing
an absolute directory path on the **PAL server's filesystem**. Omission and JSON
null both mean no explicit selection; neither clears a conversation binding.
The schema must describe inheritance, immutable binding, and the new-conversation
requirement. Keep existing CLI/role schema requirements and all other fields.

For a conversation without a saved binding:

1. Use a valid explicit request directory when present.
2. Otherwise use the selected CLI's manifest `working_dir` when configured.
3. Otherwise select scratch mode, creating a fresh temporary directory per call.

Only the winning directory is checked. A valid request can override a missing
manifest directory. Existing manifest resolution remains in the registry;
request paths are never resolved relative to the manifest, server cwd, an
attachment, or text in the prompt.

For a bound conversation, use the saved choice. A supplied directory is a check
against that choice, not a replacement. The selected CLI's current manifest
cannot move a bound conversation to a different project.

| Saved state | Request `working_dir` | Result |
| --- | --- | --- |
| No binding | Absolute valid directory A | Run in A; bind A after successful recording. |
| No binding | Omitted/null | Resolve manifest or scratch; bind that choice after successful recording. |
| Directory A | Omitted/null or canonical equivalent of A | Run in A, including after a CLI or role change. |
| Directory A | Different directory B | Error before spawn; instruct caller to start a new conversation. |
| Scratch | Omitted/null | Create a new scratch directory; retain scratch mode. |
| Scratch | Any explicit project directory | Error before spawn; start a new conversation to select that project. |
| Any | Explicit invalid value | Error; never fall back silently. |

## Path validation and identity

Require an existing directory accessible to the PAL process. Reject empty or
whitespace-only strings, non-string values, NUL characters, relative paths,
files, nonexistent paths, broken symlinks, and resolution/permission failures.
Do not trim a meaningful filename, expand `~` or environment variables, create a
directory, or infer a Git root. Spaces and ordinary path punctuation remain
valid; cwd is an OS subprocess argument, not shell text or an edit-rule pattern.

Resolve symlinks and `.`/`..` to an absolute canonical path before selection,
comparison, launch, and persistence. Compare canonical paths using the host's
path semantics. A symlink to the same canonical directory is acceptable. Keep
raw request provenance unchanged; store the canonical selection separately.

Revalidate a bound canonical directory on every call. If it disappears, becomes
a file, or resolves through a replacement symlink to a different path, fail
without selecting another directory. Handle a disappearance or access failure
between validation and spawn as a normal CLI execution error with no commit.
The binding identifies a canonical path, not an inode snapshot; it is not a
defense against concurrent filesystem replacement.

Any existing absolute directory can be selected under this feature's contract.
Do not add a containment restriction under the PAL installation, use attachment
denylist helpers as cwd policy, or reuse `PAL_WORKSPACE_ROOT` as a new clink
allowlist. Native and host filesystem permissions remain authoritative.

## Conversation binding and compatibility

Store a namespaced `initial_context.clink_working_directory` record with `mode`
equal to `directory` or `scratch`. Directory mode requires a canonical absolute
`path`; scratch mode has no `path`. Persist neither a temporary path nor the
per-call selection source. Keep this record separate from `clink_defaults`,
whose CLI/role updates must continue independently.

Resolve and hold the candidate binding in invocation-local state. Save it in
the **same compare-and-set** as the successful input/reply pair. Failed CLI
execution, cancellation, or failed persistence must not establish or replace a
binding. A successful answer whose commit fails retains the existing persistence
warning and no-continuation behavior. Original context, parent linkage, history
budgeting, and edit-authorization rules remain intact.

A missing binding in an older thread or API-origin thread means unbound. Its
first successful clink exchange establishes the binding. Do not infer it from
legacy raw request fields, historical file paths, or a parent's binding; parent
history does not establish the current thread's execution directory. A present
but malformed binding is a conversation-data error, not a missing-value fallback.
An intervening API/local-tool exchange on the same thread must preserve the
binding for a later clink continuation; those other tools do not adopt it as cwd.

Binding a manifest directory or scratch mode is a deliberate behavioral change
for continuations: changing CLI/configuration no longer changes that thread's
directory. Ordinary first calls without the new field retain their fallback.
To leave scratch mode, move to another project, or use another CLI's different
manifest directory, start a new conversation. Conversation storage remains
process-local and expires under the existing TTL; restarting PAL does not
restore bindings from transcripts. No persistent-storage migration is required.

## Execution architecture and observability

Introduce a small `clink/working_directory.py` component to validate paths, decode
bindings, and return an immutable selection containing mode, canonical path, and
per-call source. It owns no registry or storage writes. `CLinkTool` resolves it
after loading the coordinated conversation and selecting the CLI, before role
prompt/history preparation and child construction.

Create a deep request-local copy of `ResolvedCLIClient`, setting `working_dir`
to the selection's canonical path or `None` for scratch. Pass that copy through
the existing agent factory. The shared runner and Codex command hook therefore
see the same directory choice. Preserve all other fields. Never assign to a
cached client, call `os.chdir()`, change global environment variables, or add a
second directory lock. Direct low-level runner callers retain existing behavior.

Expose PAL-owned `metadata.working_directory` with `mode`, `path` (null for
scratch), and `source` (`request`, `conversation`, `configuration`, or `scratch`).
Omitted bound selections use source `conversation`, including bound scratch;
an accepted explicit repeat uses `request`. Add this after parsed CLI metadata
so native output cannot spoof it, and retain it through output reduction and
post-selection execution errors. Validation errors must not imply a child ran.
Do not expose or persist the disposable scratch pathname.

## Permissions and native CLI behavior

Selecting cwd may change the project's native settings, instructions, trust
checks, and permission decisions. The feature adds no permission bypass flag
and does not edit native settings. It preserves each adapter's current argument
construction and current-request `allow_edits`/`editable_paths` handling. In
particular, Agy false inherits saved policy and is not enforced read-only.

The guaranteed boundary is the **child's launch directory**. Native tools can
change directories, and operator-configured native flags can affect their
workspace selection. Do not promise uniform instruction discovery, command
approval, or filesystem confinement across vendors. Shared attachment helpers
are not a sandbox for native CLI reads; references outside cwd remain references.
Concurrent conversations in the same project are not serialized by this feature.

## Assumptions

- Callers can supply absolute paths meaningful to the PAL host. Verify through
  MCP tests; remote-client path translation is not provided.
- A request-local client copy reaches all adapter hooks without hidden global
  mutation. Verify through overlapping child processes for all four vendors.
- Canonical paths provide useful conversation identity without pinning directory
  contents. Verify normalization and symlink cases; filesystem race limits remain.
- Native CLIs use cwd meaningfully with the supported manifests. Verify project
  reads in opt-in live smoke tests; fake children establish transport only.

## Not Doing

- Automatic discovery from MCP roots, prompt text, attachments, or server cwd:
  explicit selection avoids ambiguous or installation-relative guesses.
- Global/per-project manifest generation or user settings edits: this feature
  removes the need for a static manifest solely to choose each conversation's cwd.
- Project switching or reset within a conversation: the user requires a new one.
- New filesystem sandbox, permission allowlist, or project-wide locking: these
  require separate contracts and are not implied by directory selection.
- Relative attachment resolution, Git-root inference, repository creation, or
  persistent conversation storage: existing attachment and storage contracts stay.
- Rewriting the greeting/default-role prompt or repairing baseline dependencies:
  these are separate concerns, not requirements for the directory feature.

## Rejected Alternatives

| Direction | Value and feasibility | Decision |
| --- | --- | --- |
| User-level static `working_dir` | Already implemented, but pins all instances sharing the override to one project. | Rejected for the user's multi-project workflow. |
| Per-instance manifests | Already implemented and can isolate projects, but requires launcher/configuration maintenance. | Retained as existing functionality; insufficient for per-request selection. |
| Infer the project automatically | Saves a request field, but sources may disagree or be unavailable. | Rejected for ambiguity and hidden directory changes. |
| Allow explicit project changes on continuation | Flexible and easy to implement, but combines project contexts under one ID. | User explicitly chose a new conversation instead. |
| Require the directory on every call | Avoids stored selection but makes follow-ups repetitive and easy to misroute. | User chose inheritance. |
| Mutate the registry or change process cwd | Shorter wiring, but shares mutable execution context across overlapping requests. | Rejected for cross-request interference. |

## Verification and rollout

Use the cases and concrete commands in [implementation.md](implementation.md).
Release requires observed new-request/continuation behavior, conflict rejection,
success-only persistence, same-client concurrency isolation, and preserved
native argument construction for all four adapters. Include real subprocess and
MCP stdio coverage with disposable projects; no API keys are needed for these.
Live vendor observations must state their version and prepared permission policy.

Restart PAL to expose the new schema. Callers must supply the field to opt into
explicit project selection; the server does not automatically learn their active
project. No new dependency or configuration migration is planned. Independent
design review remains the next gate; isolated fact-checking is not that review.

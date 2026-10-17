# Design: per-request project directories for clink

> Status: Task 1 helper implemented; public API/persistence pending, including Task 2.0 validation-contract correction
> Created: 2026-10-15
> Source baseline: `6e6ea4a`
> Review correction baseline: `6da3e2b`
> Implementation: [implementation.md](implementation.md)
> Tasks: [tasks.md](tasks.md)
> Evidence and limitations: [verification.md](verification.md)
> Review corrections: [review-resolution.md](review-resolution.md)

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
The user also explicitly approved selecting the first project from a conversation
that began in scratch mode. That transition retains history and becomes permanent
only when the project call is successfully recorded.
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

Add optional `working_directory_absolute_path` to both `CLinkRequest` and
`get_input_schema()`, matching the existing [chat request](../../../../tools/chat.py).
Its value is a strict string naming a directory on the **PAL server's filesystem**.
Like chat, expand `~` with `os.path.expanduser()` on that host, then require an
absolute path. Omission and JSON null both mean no explicit selection; neither
clears an established project binding. The operator manifest retains `working_dir`;
there is no second public clink alias for it.
The schema must describe inheritance, immutable binding, and the new-conversation
requirement. Keep existing CLI/role schema requirements and all other fields.

The shared field spelling does not make chat's artifact-output directory a
clink execution context. Chat's code-generation boundary additionally enforces
`PAL_WORKSPACE_ROOT` and no-follow artifact creation. Clink retains native CLI
permission controls and does not reuse chat's artifact validator. No chat code
change is proposed. Neither a chat-origin thread's
`working_directory_absolute_path` nor an old top-level `working_dir` establishes
a clink binding; only a current clink request or clink's saved binding can do so.

For a conversation without a saved binding:

1. Use a valid explicit request directory when present.
2. Otherwise use the selected CLI's manifest `working_dir` when configured.
3. Otherwise select scratch mode, creating a fresh temporary directory per call.

Only the winning directory is checked. A valid request can override a missing
manifest directory. Existing manifest resolution remains in the registry;
request paths are never resolved relative to the manifest, server cwd, an
attachment, or text in the prompt.

For a project-bound conversation, use the saved choice. A supplied directory is
a check against that choice, not a replacement. The selected CLI's current
manifest cannot move it to a different project. A saved scratch choice is not
an established project: omission retains scratch even if the selected CLI has a
manifest directory; an explicit valid directory can select the first project.

| Saved state | Request `working_directory_absolute_path` | Result |
| --- | --- | --- |
| No binding | Absolute valid directory A | Run in A; bind A after successful recording. |
| No binding | Omitted/null | Resolve manifest or scratch; bind that choice after successful recording. |
| Directory A | Omitted/null or filesystem-equivalent path to A | Run using A's saved launch spelling, including after a CLI or role change. |
| Directory A | Different directory B | Error before spawn; instruct caller to start a new conversation. |
| Scratch | Omitted/null | Create a new scratch directory; retain scratch mode. |
| Scratch | Explicit valid directory A | Run in A with retained history; replace scratch mode with A only on successful recording. |
| Any | Explicit invalid value | Error; never fall back silently. |

## Path validation and identity

Require an existing directory accessible to the PAL process. Reject empty or
whitespace-only strings, non-string values, NUL characters, paths still relative
after home expansion, files, nonexistent paths, broken symlinks, and
resolution/permission failures.
Do not trim a meaningful filename, expand environment variables, create a
directory, or infer a Git root. Spaces and ordinary path punctuation remain
valid; cwd is an OS subprocess argument, not shell text or an edit-rule pattern.

Keep two paths with different responsibilities:

- `launch_path`: for request input, `os.path.normpath(os.path.expanduser(value))`,
  preserving symlink spelling. For manifest input, preserve the registry's
  resolved `Path` spelling without another normalization/realpath pass. This
  preserves existing absolute-manifest launch behavior, including any `..`
  interpretation already supplied by the operator. Pass this path as subprocess
  cwd and save it for omitted-directory continuations.
- `canonical_path`: resolve the **launch path** with `Path.resolve(strict=True)`
  for identity and persistence. Do not substitute it into cwd or rewrite
  `editable_paths`. Computing identity after choosing launch spelling ensures
  normalization cannot make validation refer to a different launch target.

Use `os.path.samefile()` to compare existing directory targets; do not use string
equality or `normcase()` as the acceptance check. This accepts differently cased
names on case-insensitive filesystems and symlink aliases, while distinguishing
different directories on case-sensitive filesystems. An explicit equivalent
path does not replace the saved `launch_path`. Stat/comparison failure is an
explicit error, never a string-equality fallback. No inode value is persisted.

Revalidate both saved paths on every call and require
`os.path.samefile(saved_launch_path, saved_canonical_path)`. If either disappears,
becomes a file, or the launch alias points at a different directory, fail without
using an alternative alias or canonical path as a fallback. Handle an access failure
between validation and spawn as a normal CLI execution error with no commit.
The binding identifies a canonical path, not an inode snapshot; it is not a
defense against concurrent filesystem replacement.

Preserving the cwd argument follows `_validate_editable_paths()`'s existing
non-realpath precedent and avoids PAL changing `/tmp/project` to
`/private/tmp/project`. It does not force an OS or native CLI to return that
spelling from `getcwd()` or preserve it in native trust keys. Tests must check
the literal spawn argument and Claude edit arguments separately from the actual
child directory, which is checked by filesystem equivalence and file markers.

Any existing absolute directory can be selected under this feature's contract.
Do not add a containment restriction under the PAL installation, use attachment
denylist helpers as cwd policy, or reuse `PAL_WORKSPACE_ROOT` as a new clink
allowlist. Native and host filesystem permissions remain authoritative.

## Conversation binding and compatibility

Store a namespaced `initial_context.clink_working_directory` record with `mode`
equal to `directory` or `scratch`. Directory mode requires absolute `launch_path`
and `canonical_path`; scratch mode has neither. Persist neither a temporary path
nor the per-call selection source. Keep this record separate from `clink_defaults`,
whose CLI/role updates must continue independently.

Resolve and hold the candidate binding in invocation-local state. Save it in
the **same compare-and-set** as the successful input/reply pair. Failed CLI
execution, cancellation, or failed persistence must not establish or replace a
binding. A successful answer whose commit fails retains the existing persistence
warning and no-continuation behavior. Original context, parent linkage, history
budgeting, and edit-authorization rules remain intact.

Scratch-to-directory promotion uses that same CAS and keeps all prior turns;
there is no thread reset or history copy. A failed project attempt leaves the
thread in scratch mode with unchanged TTL. Only this first-project transition
can replace scratch state. Resetting an established project to scratch or changing
it to a different project remains unsupported within the same conversation.

A missing binding in an older thread or API-origin thread means unbound. Its
first successful clink exchange establishes the binding. Do not infer it from
legacy raw request fields (including chat's `working_directory_absolute_path`),
historical file paths, or a parent's binding. Parent history does not establish
the current thread's execution directory. A present
but malformed binding is a conversation-data error, not a missing-value fallback.
An intervening API/local-tool exchange on the same thread must preserve the
binding for a later clink continuation; those other tools do not adopt it as cwd.

Saving the initial choice is a deliberate continuation behavior change: changing
CLI/configuration does not silently choose a different project or promote a
scratch conversation. Ordinary first calls without the new field retain their
fallback. An explicit first-project selection may leave scratch mode; moving
between established projects requires a new conversation. Conversation storage
remains process-local and expires under the existing TTL; restarting PAL does not
restore bindings from transcripts. No persistent-storage migration is required.

## Execution architecture and observability

Introduce a small `clink/working_directory.py` component to validate paths, decode
bindings, and return an immutable selection with mode, launch/canonical paths,
and per-call source. It owns no registry or storage writes. `CLinkTool` resolves
it after loading the coordinated conversation and selecting the CLI, before role
prompt/history preparation and child construction.

Create a deep request-local copy of `ResolvedCLIClient`, setting `working_dir`
to the selection's `launch_path` or `None` for scratch. Pass that copy through
the existing agent factory. The shared runner and Codex command hook therefore
see the same directory choice. Preserve all other fields. Never assign to a
cached client, call `os.chdir()`, change global environment variables, or add a
second directory lock. Direct low-level runner callers retain existing behavior.

Expose PAL-owned `metadata.working_directory` with `mode`, `launch_path`,
`canonical_path` (both null for scratch), and `source` (`request`, `conversation`,
`configuration`, or `scratch`). Chat currently has no equivalent response object
to rename. The manifest's short key, public cross-tool input name, namespaced
storage key, and response object have intentionally different roles; document
this mapping without inventing an additional public alias.
Bound selections use source `conversation`, including an explicit equivalent
repeat: the saved launch spelling remains authoritative. A first explicit
project selection uses `request`. Add this after parsed CLI metadata
so native output cannot spoof it, and retain it through output reduction and
post-selection execution errors. Validation errors must not imply a child ran.
Do not expose or persist the disposable scratch pathname.

### Rejection contract

Directory validation/rejection uses the existing `ToolOutput(status="error")`
and `ToolExecutionError` envelope, with no continuation offer. Metadata includes
`tool_name="clink"`, `stage="validation"`, `execution_started=false`,
`retryable=false`, and the stable `error_type` below. False means do not blindly
retry unchanged input; restoring a missing directory can make a later call valid.

| Condition | `error_type` | `new_conversation_required` |
| --- | --- | --- |
| Invalid current request path/type | `InvalidWorkingDirectoryError` | false |
| Valid requested directory differs from the bound project | `WorkingDirectoryConflictError` | true |
| Saved launch/canonical path missing, inaccessible or no longer equivalent | `BoundWorkingDirectoryUnavailableError` | false |
| Malformed saved binding | `InvalidWorkingDirectoryBindingError` | true |

For all these errors, `bound_working_directory` is the saved directory record or
null when absent, scratch, or undecodable. `requested_working_directory` is the
caller's original string or null for omission/null/invalid non-string input.
`normalized_requested_path` and `canonical_requested_path` are strings when
validation reached those stages, otherwise null. Do not echo an invalid object
or invent a resolved path. Conflict prose explicitly instructs starting a new
conversation. Errors must not write history/defaults or refresh TTL, and must
not include success-shaped `working_directory` selection metadata.

A directory race/access failure after selection while preparing or attempting spawn uses
`error_type="WorkingDirectoryLaunchError"`, `stage="spawn"`,
`execution_started=false`, and the selected `working_directory` object. Preserve
the OS cause. Use source-neutral wording such as "Selected working directory is
not an existing directory" instead of calling every path "Configured working_dir".
Do not misclassify an unrelated executable failure or cancellation as a cwd error.

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

Caller-selected cwd is also a **native-configuration activation surface**. A
prompt-injected caller could select a directory containing project instructions,
hooks, plugins, or MCP-server definitions that the operator has not reviewed.
These may influence behavior or launch processes under the native CLI's policy.
For a caller without shell access, this can add a capability beyond selecting
files for analysis. Do not assume an interactive trust prompt protects headless
execution. For example, Claude documents project hooks/settings and project MCP
configuration; its headless MCP behavior differs from interactive approval.
See [settings](https://code.claude.com/docs/en/settings) and
[project MCP configuration](https://code.claude.com/docs/en/mcp#project-scope).

This version retains the existing trusted-local-delegation model and makes that
activation surface explicit to operators. It does not treat arbitrary restricted
MCP clients as safely confined. An operator-controlled directory allowlist is a
documented possible follow-up with concrete scope in
[implementation.md](implementation.md#deferred-follow-up-operator-directory-policy).

Codex's default repository check can reject a selected directory outside a Git
repository. PAL automatically adds `--skip-git-repo-check` only for scratch;
operators may deliberately add it to the Codex manifest's `additional_args` and
restart PAL to allow non-repository projects. Preserve that explicit override;
do not claim the flag can never occur with a selected directory. Surface native
repository-rejection diagnostics with this actionable option and never silently
add the flag or automatically retry. This is not a new PAL Git-root requirement.

## Assumptions

- Callers can supply absolute paths meaningful to the PAL host. Verify through
  MCP tests; remote-client path translation is not provided.
- A request-local client copy reaches all adapter hooks without hidden global
  mutation. Verify through overlapping child processes for all four vendors.
- Separate launch/canonical paths and `samefile()` provide useful identity without
  pinning directory contents. Verify symlinks, case aliases and comparison errors;
  filesystem race limits remain.
- Native CLIs use cwd meaningfully with the supported manifests. Verify project
  reads in opt-in live smoke tests; fake children establish transport only.

## Not Doing

- Automatic discovery from MCP roots, prompt text, attachments, or server cwd:
  explicit selection avoids ambiguous or installation-relative guesses.
- Global/per-project manifest generation or user settings edits: this feature
  removes the need for a static manifest solely to choose each conversation's cwd.
- Switching established projects or resetting them to scratch: the user requires
  a new conversation. Selecting the first project from scratch is supported.
- New filesystem sandbox or project-wide locking: these require separate
  contracts and are not implied by directory selection. The possible operator
  directory-policy follow-up is recorded explicitly in the implementation plan.
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
| Allow changes between established projects | Flexible, but combines distinct project contexts under one ID. | User chose a new conversation instead; scratch-to-first-project selection is permitted. |
| Make scratch permanently immutable | Simple mode handling, but prevents a history-bearing conversation with no project from choosing one. | Rejected by the user's explicit review-resolution choice. |
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

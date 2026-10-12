# Logging and state paths

PAL writes logs outside its installation directory. Server startup, launchers,
container health checks and simulator readers use the same Python resolver.

## Location

State directory precedence is:

1. Absolute `PAL_STATE_DIR`.
2. `XDG_STATE_HOME/pal`, when XDG_STATE_HOME is an absolute path.
3. On Unix/macOS, `~/.local/state/pal`; on Windows, `%LOCALAPPDATA%\pal`,
   falling back to `~\AppData\Local\pal` when LOCALAPPDATA is unset.

`PAL_LOG_DIR` overrides only the log directory. Otherwise logs are under
`<state directory>/logs`. Empty settings are treated as unset. Home prefixes are
expanded; spaces are preserved. Relative paths, regular files, inaccessible
directories and failed creation cause visible errors with no package-directory
fallback. If the home cannot be resolved, set an absolute PAL_STATE_DIR.
The existing `.env` override policy applies to PAL and XDG settings.

Example (use absolute paths appropriate to your account):

```env
PAL_STATE_DIR=/absolute/path/to/pal-state
PAL_LOG_DIR=/absolute/path/to/pal-logs
LOG_LEVEL=INFO
```

`PAL_LOG_DIR` does not create or relocate the selected state directory; it is
still validated. These directories are operator-controlled, not a sandbox or
multi-user authorization boundary. Use separate state directories for mutually
untrusted projects. Use separate log directories for simultaneously running
server processes: standard Python rotating handlers do not coordinate rotation
across processes. This change does not enable transcripts or persistent recovery.

## Follow logs

After activating the existing virtual environment, resolve the path from the
repository root or an installed PAL package:

```bash
pal_log_path=$(python -m utils.log_dir)
tail -f "$pal_log_path/mcp_server.log"
tail -n 100 "$pal_log_path/mcp_activity.log"
rg 'ERROR|WARNING' "$pal_log_path/mcp_server.log"
```

`python -m utils.log_dir --create` creates the directory explicitly. Without
`--create`, path lookup does not create files/directories. `./run-server.sh -f`
retains the existing setup workflow, then follows that resolved path. Its setup
output no longer prefixes an absolute log path with the repository path.

On Windows, `run-server.ps1 -Follow` invokes the same resolver using the setup
environment's Python. To inspect the location directly:

```powershell
$palLogPath = & .\.pal_venv\Scripts\python.exe -m utils.log_dir
Get-Content -LiteralPath (Join-Path $palLogPath 'mcp_server.log') -Wait
```

## Rotation and diagnostics

| File | Rotation threshold | Backups | Active + backups | Approximate capacity |
| --- | --- | --- | --- | --- |
| `mcp_server.log` | 20 MiB | 5 | 6 files | 120 MiB |
| `mcp_activity.log` | 10 MiB | 2 | 3 files | 30 MiB |

These retain the existing runtime limits and correct the previous comments and
documentation. Thresholds are not strict disk quotas: a single oversized record
can exceed them. `LOG_MAX_SIZE` and `LOG_BACKUP_COUNT` in older setup/Compose
examples are not consumed; the constants in `utils/log_dir.py` are authoritative.
Follow-up for Task 42: remove those misleading legacy example settings or design
and verify actual configurable rotation; no new knob is claimed here.

`LOG_LEVEL` accepts DEBUG, INFO, WARNING, ERROR and CRITICAL. The current server
default is DEBUG; configure INFO for less detail. Logs retain the existing local
timestamp format. Activity entries also propagate to the main logger/stderr.
Expected activity-write I/O/encoding failures emit event-specific diagnostics;
programming errors are not silently swallowed. Logs can contain request details:
protect the selected directory with your account's normal access controls.

## Migration and containers

Existing repository `logs/` files are left untouched. To keep using them, explicitly
set `PAL_LOG_DIR` to that old directory's absolute path. Otherwise the new location
starts with new files. PAL never copies, moves or deletes old logs automatically.

The image sets `PAL_STATE_DIR=/var/lib/pal`, owned by its non-root user. Compose
mounts the named `pal-mcp-state` volume there, so `/app` can remain read-only.
The health check resolves the same log directory and tests writability with a
unique temporary file. See [Docker setup](../docker/README.md) for mounts and
migration of existing bind-mounted logs. A custom container path must have a
matching writable mount and permissions for the configured container user.

## Private conversation transcripts

Set `PAL_TRANSCRIPTS=true` to record conversation audit streams under
`<state directory>/transcripts/<canonical UUID>.jsonl`. The default is false.
Prompts, raw replies, file/image references and available turn metadata are
sensitive persistent data. Choose a private local state directory, with separate
directories for mutually untrusted projects and for separate server processes.
`PAL_LOG_DIR` has no effect on transcripts.

Schema version 1 starts with a `thread` event (thread ID, timestamp, initiating
tool and parent ID), followed by `turn` events with one-based memory turn numbers,
timestamp, role, raw content, tool, files/images, model/provider and available
model metadata. Null usage/provenance remains unknown. Events append only after
successful memory writes; a failed append warns in logs while preserving the
answer and memory. A missing turn number therefore indicates an audit gap.
Workflow-state-only updates do not create synthetic conversation turns.

POSIX transcript directories use mode 0700 and regular files use 0600; existing
permissions are tightened before access. Symlinked configured roots, transcript
directories and files, hard-linked files, and nonregular files are rejected.
The state owner is trusted; this is not an OS sandbox against that owner.
Windows recording fails closed with a diagnostic because chmod cannot establish
private Windows ACLs. Native Windows ACL/reparse-point support remains a follow-up
requiring Windows contract tests before activation; normal memory/logging still work.

Startup removes only owned, single-link regular files with canonical UUID names
whose last modification is older than 30 days. Unrelated files and links remain
untouched. Cleanup errors warn without preventing startup. Retention is a startup
policy, not a disk quota or a background timer. Disabling recording stops both
appends and pruning, leaving existing files for explicit operator removal.

Writes are serialized within one process. A short write is completed; an ordinary
append failure attempts to restore the previous length. A crash may leave an
incomplete final line; the next append separates it from the next valid record.
These are best-effort audit records, not fsync-backed recovery. Transcripts never
restore continuations: process restart and memory TTL retain their existing behavior.

## MCP conversation resources

With `PAL_TRANSCRIPTS=true`, MCP resource discovery lists `pal://threads` and a
`pal://threads/{thread_id}` template. Both return `application/json` schema-v1
envelopes. The resources capability is advertised even while disabled, but
discovery returns no entries and all reads are hidden when recording is disabled,
including retained files. Resources are audit views, not resumable conversations.

| Resource | Contents | Limits |
| --- | --- | --- |
| `pal://threads` | `threads` with UUID, URI, file byte size and Unix modification timestamp, sorted by UUID | 100 summaries; 10,000 directory entries examined |
| `pal://threads/{thread_id}` | Complete validated `records` in file order | 1 MiB examined plus one overflow byte; 1,000 physical records |

The index includes `total_threads`, `skipped_files`, `limit` and `truncated`.
Unrecognized filenames, links and nonregular files are skipped. Exceeding the
directory scan cap fails explicitly instead of returning a nondeterministic subset.
Thread reads include `truncated` and diagnostics for `skipped_malformed_records`,
`interrupted_final_lines`, `examined_records`, `byte_limit` and `record_limit`.
Malformed JSON, invalid event shape/version/thread identity and incomplete final
lines are omitted with counts. Non-finite numbers, unencodable Unicode and records
nested beyond 64 JSON containers are also skipped. A partial line at the byte limit is truncation,
not evidence of a corrupt file. Diagnostics count only examined records. V1 has no
cursor or pagination: later records/threads require operator inspection of the
private files when a view is truncated. No subscriptions or change notifications.

Thread IDs must be canonical lowercase hyphenated UUIDs. Queries, fragments,
percent-encoded IDs and paths are rejected. Missing, unknown, unsafe-file and
disabled resources use MCP error -32002 (`Resource not found`). Permission failures
and storage/configuration failures use -32603 with distinct safe messages. Local
path/error details are not exposed to the client. Reads use the same private
directory/file policy as recording and run outside the event-loop thread.

The mapping follows the retained
[MCP 2025-11-25 resource contract](https://modelcontextprotocol.io/specification/2025-11-25/server/resources#error-handling)
and was verified against Python SDK 1.30.0; MCP 2.x migration guidance has a different
mapping and does not govern this implementation.

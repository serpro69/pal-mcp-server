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

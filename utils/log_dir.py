"""One log path and rotation policy for server, launchers and diagnostic readers."""

import argparse
from pathlib import Path

from utils.env import get_env
from utils.state_dir import StateDirectoryError, get_state_dir, resolve_directory

MAIN_LOG_NAME = "mcp_server.log"
ACTIVITY_LOG_NAME = "mcp_activity.log"
MAIN_LOG_MAX_BYTES = 20 * 1024 * 1024
MAIN_LOG_BACKUPS = 5  # Active + five backups: approximately 120 MiB.
ACTIVITY_LOG_MAX_BYTES = 10 * 1024 * 1024
ACTIVITY_LOG_BACKUPS = 2  # Active + two backups: approximately 30 MiB.


def get_log_dir(*, create: bool = False) -> Path:
    """PAL_LOG_DIR overrides only logs; state selection is still validated."""
    state = get_state_dir()
    configured = get_env("PAL_LOG_DIR")
    return resolve_directory(configured or state / "logs", "PAL_LOG_DIR" if configured else "state logs", create=create)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="Create the selected directory")
    args = parser.parse_args()
    try:
        print(get_log_dir(create=args.create))
    except StateDirectoryError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Portable operator-selected state paths; never fall back to the package tree."""

import os
import stat
import sys
from pathlib import Path

from utils.env import get_env


class StateDirectoryError(ValueError):
    """State/log configuration cannot select or create a usable directory."""


def resolve_directory(value: str | Path, setting: str, *, create: bool = False, resolve_links: bool = True) -> Path:
    """Expand home, require an absolute path and diagnose invalid ancestors."""
    try:
        if "\x00" in str(value):
            raise ValueError("NUL is not valid in a path")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("an absolute path is required")
        if resolve_links:
            path = path.resolve()
        for ancestor in (path, *path.parents):
            try:
                mode = ancestor.stat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(mode):
                raise ValueError(f"not a directory: {ancestor}")
            break
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path
    except (OSError, RuntimeError, ValueError) as exc:
        raise StateDirectoryError(f"Invalid {setting} directory: {exc}") from exc


def get_state_dir(*, create: bool = False, resolve_links: bool = True) -> Path:
    """PAL_STATE_DIR > XDG_STATE_HOME/pal > platform per-user state."""
    configured = get_env("PAL_STATE_DIR")
    if configured:
        return resolve_directory(configured, "PAL_STATE_DIR", create=create, resolve_links=resolve_links)
    xdg = get_env("XDG_STATE_HOME")
    if xdg:
        root = resolve_directory(xdg, "XDG_STATE_HOME")
        return resolve_directory(root / "pal", "XDG_STATE_HOME/pal", create=create, resolve_links=resolve_links)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        root = resolve_directory(os.environ["LOCALAPPDATA"], "LOCALAPPDATA")
        return resolve_directory(root / "pal", "LOCALAPPDATA/pal", create=create, resolve_links=resolve_links)
    try:
        home = Path.home()
    except (RuntimeError, OSError) as exc:
        raise StateDirectoryError("Cannot resolve the user home; set an absolute PAL_STATE_DIR") from exc
    suffix = Path("AppData/Local/pal") if sys.platform == "win32" else Path(".local/state/pal")
    return resolve_directory(home / suffix, "per-user state", create=create, resolve_links=resolve_links)

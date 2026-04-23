"""
Security configuration and path validation constants

This module contains security-related constants and configurations
for file access control.
"""

from functools import lru_cache
from pathlib import Path

# Dangerous system paths - block these AND all their subdirectories
# These are system directories where user code should never reside
DANGEROUS_SYSTEM_PATHS = {
    "/",
    "/etc",
    "/usr",
    "/bin",
    "/var",
    "/root",
    "C:\\Windows",
    "C:\\Program Files",
}

# User home container paths - block ONLY the exact path, not subdirectories
# Subdirectory access (e.g., /home/user/project) is controlled by is_home_directory_root()
# This allows users to work in their home subdirectories while blocking overly broad access
DANGEROUS_HOME_CONTAINERS = {
    "/home",
    "C:\\Users",
}

# Sensitive subdirectories inside the user's home directory.
# These typically contain credentials, private keys, and secrets and should
# never be exposed to the LLM. Block the exact path AND all subdirectories,
# resolved relative to $HOME at check time. Entries may contain '/' to
# describe multi-component paths (e.g., '.config/gcloud').
DANGEROUS_HOME_SUBDIRECTORIES = {
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".azure",
    ".docker",
    ".config/gcloud",
}

# Combined set of absolute dangerous paths. Home subdirectories are not
# included here — they are evaluated dynamically against $HOME in
# is_dangerous_path() via _resolved_home_sensitive_paths().
DANGEROUS_PATHS = DANGEROUS_SYSTEM_PATHS | DANGEROUS_HOME_CONTAINERS


@lru_cache(maxsize=1)
def _resolved_home_sensitive_paths() -> tuple[Path, ...]:
    """Return the fully-resolved paths of DANGEROUS_HOME_SUBDIRECTORIES.

    Resolving both sides of the comparison closes the symlink-bypass class:
    if ``~/.ssh`` is a symlink to ``~/.dotfiles/.ssh`` (chezmoi, Stow, yadm),
    the resolved input path must match the resolved sensitive path.

    Cached for the life of the process. Tests that change $HOME must call
    ``_resolved_home_sensitive_paths.cache_clear()`` to pick up the new value.
    """
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        return ()
    return tuple((home / s).resolve() for s in DANGEROUS_HOME_SUBDIRECTORIES)


# Directories to exclude from recursive file search
# These typically contain generated code, dependencies, or build artifacts
EXCLUDED_DIRS = {
    # Python
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "*.egg-info",
    ".eggs",
    "wheels",
    ".Python",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "htmlcov",
    ".coverage",
    "coverage",
    # Node.js / JavaScript
    "node_modules",
    ".next",
    ".nuxt",
    "bower_components",
    ".sass-cache",
    # Version Control
    ".git",
    ".svn",
    ".hg",
    # Build Output
    "build",
    "dist",
    "target",
    "out",
    # IDEs
    ".idea",
    ".vscode",
    ".sublime",
    ".atom",
    ".brackets",
    # Temporary / Cache
    ".cache",
    ".temp",
    ".tmp",
    "*.swp",
    "*.swo",
    "*~",
    # OS-specific
    ".DS_Store",
    "Thumbs.db",
    # Java / JVM
    ".gradle",
    ".m2",
    # Documentation build
    "_build",
    "site",
    # Mobile development
    ".expo",
    ".flutter",
    # Package managers
    "vendor",
}


def is_dangerous_path(path: Path) -> bool:
    """
    Check if a path is in or under a dangerous directory.

    This function handles two categories of dangerous paths differently:

    1. System paths (DANGEROUS_SYSTEM_PATHS): Block the path AND all subdirectories.
       Example: /etc is dangerous, so /etc/passwd is also blocked.

    2. Home containers (DANGEROUS_HOME_CONTAINERS): Block ONLY the exact path.
       Example: /home is blocked, but /home/user/project is allowed.
       Subdirectory access control is delegated to is_home_directory_root().

    Args:
        path: Path to check

    Returns:
        True if the path is dangerous and should not be accessed

    Security:
        Fixes path traversal vulnerability (CWE-22) while preserving
        user access to home subdirectories.
    """
    try:
        resolved = path.resolve()

        def _dangerous_variants(p: Path) -> set[Path]:
            variants = {p}
            # Only resolve paths that are absolute on the current platform.
            # This avoids turning Windows-style strings into nonsense absolute paths on POSIX.
            if p.is_absolute():
                try:
                    variants.add(p.resolve())
                except Exception:
                    pass
            return variants

        # Check 1: Root directory (filesystem root)
        if resolved.parent == resolved:
            return True

        # Check 2: System paths - block exact match AND all subdirectories
        for dangerous in DANGEROUS_SYSTEM_PATHS:
            # Skip root "/" - already handled above
            if dangerous == "/":
                continue

            for dangerous_path in _dangerous_variants(Path(dangerous)):
                # is_relative_to() correctly handles both exact matches and subdirectories.
                # Resolving the dangerous base path also handles platform symlinks
                # (e.g., macOS /etc -> /private/etc, /var -> /private/var).
                if resolved == dangerous_path or resolved.is_relative_to(dangerous_path):
                    return True

        # Check 3: Home containers - block ONLY exact match
        # Subdirectories like /home/user/project should pass through here
        # and be handled by is_home_directory_root() in resolve_and_validate_path()
        for container in DANGEROUS_HOME_CONTAINERS:
            for container_path in _dangerous_variants(Path(container)):
                if resolved == container_path:
                    return True

        # Check 4: Sensitive subdirectories under the user's home directory
        # (e.g., ~/.ssh, ~/.aws, ~/.gnupg hold credentials and private keys).
        # Both sides are resolved (see _resolved_home_sensitive_paths) so
        # symlinked dotfile managers cannot bypass the check. Any failure
        # falls through to the outer except, which fails closed.
        for sensitive_path in _resolved_home_sensitive_paths():
            if resolved.is_relative_to(sensitive_path):
                return True

        return False

    except Exception:
        return True  # If we can't resolve, consider it dangerous

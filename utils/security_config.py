"""
Security configuration and path validation constants

This module contains security-related constants and configurations
for file access control.
"""

from pathlib import Path

# Dangerous paths that should never be scanned
# These would give overly broad access and pose security risks
DANGEROUS_PATHS = {
    "/",
    "/etc",
    "/usr",
    "/bin",
    "/var",
    "/root",
    "/home",
    "C:\\",
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Users",
}

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

    Uses PREFIX MATCHING to block dangerous directories AND their subdirectories.
    For example, if "/etc" is in DANGEROUS_PATHS, both "/etc" and "/etc/passwd"
    will be blocked.

    Args:
        path: Path to check

    Returns:
        True if the path is dangerous and should not be accessed

    Security:
        Fixes path traversal vulnerability (CWE-22) reported in:
        - https://github.com/BeehiveInnovations/zen-mcp-server/issues/293
        - https://github.com/BeehiveInnovations/zen-mcp-server/issues/312
    """
    try:
        resolved = path.resolve()
        resolved_str = str(resolved)

        # Check 1: Root directory (filesystem root)
        if resolved.parent == resolved:
            return True

        # Check 2: Exact match or subdirectory of dangerous paths
        for dangerous in DANGEROUS_PATHS:
            # Skip root "/" - already handled above
            if dangerous == "/":
                continue

            # Exact match
            if resolved_str == dangerous:
                return True

            # Subdirectory check: path starts with dangerous + separator
            # Use os.sep for platform-appropriate separator
            if resolved_str.startswith(dangerous + "/") or resolved_str.startswith(dangerous + "\\"):
                return True

        return False

    except Exception:
        return True  # If we can't resolve, consider it dangerous

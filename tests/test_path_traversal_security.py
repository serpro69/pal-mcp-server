"""
Test path traversal security fix.

Fixes vulnerability reported in:
- https://github.com/BeehiveInnovations/zen-mcp-server/issues/293
- https://github.com/BeehiveInnovations/zen-mcp-server/issues/312

The vulnerability: is_dangerous_path() only did exact string matching,
so /etc was blocked but /etc/passwd was allowed.
"""

from pathlib import Path

from utils.security_config import is_dangerous_path


class TestPathTraversalFix:
    """Test that subdirectories of dangerous paths are now blocked."""

    def test_exact_match_still_works(self):
        """Test that exact dangerous paths are still blocked."""
        assert is_dangerous_path(Path("/etc")) is True
        assert is_dangerous_path(Path("/usr")) is True
        assert is_dangerous_path(Path("/var")) is True

    def test_subdirectory_now_blocked(self):
        """Test that subdirectories are now blocked (the fix)."""
        # These were allowed before the fix
        assert is_dangerous_path(Path("/etc/passwd")) is True
        assert is_dangerous_path(Path("/etc/shadow")) is True
        assert is_dangerous_path(Path("/etc/hosts")) is True
        assert is_dangerous_path(Path("/var/log/auth.log")) is True

    def test_deeply_nested_blocked(self):
        """Test that deeply nested paths are blocked."""
        assert is_dangerous_path(Path("/etc/ssh/sshd_config")) is True
        assert is_dangerous_path(Path("/usr/local/bin/python")) is True

    def test_root_blocked(self):
        """Test that root directory is blocked."""
        assert is_dangerous_path(Path("/")) is True

    def test_safe_paths_allowed(self):
        """Test that safe paths are still allowed."""
        # User project directories should be allowed
        assert is_dangerous_path(Path("/tmp/test")) is False
        assert is_dangerous_path(Path("/tmp/myproject/src")) is False

    def test_similar_names_not_blocked(self):
        """Test that paths with similar names are not blocked."""
        # /etcbackup should NOT be blocked (it's not under /etc)
        assert is_dangerous_path(Path("/tmp/etcbackup")) is False
        assert is_dangerous_path(Path("/tmp/my_etc_files")) is False


class TestRegressionPrevention:
    """Regression tests for the specific vulnerability."""

    def test_etc_passwd_blocked(self):
        """Test /etc/passwd is blocked (common attack target)."""
        assert is_dangerous_path(Path("/etc/passwd")) is True

    def test_etc_shadow_blocked(self):
        """Test /etc/shadow is blocked (password hashes)."""
        assert is_dangerous_path(Path("/etc/shadow")) is True

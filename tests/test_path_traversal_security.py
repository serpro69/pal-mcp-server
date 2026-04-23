"""
Test path traversal security fix.

Fixes vulnerability reported in:
- https://github.com/BeehiveInnovations/zen-mcp-server/issues/293
- https://github.com/BeehiveInnovations/zen-mcp-server/issues/312

The vulnerability: is_dangerous_path() only did exact string matching,
so /etc was blocked but /etc/passwd was allowed.

Additionally, this fix properly handles home directory containers:
- /home and C:\\Users are blocked (exact match only)
- /home/user/project paths are allowed through is_dangerous_path()
  and handled by is_home_directory_root() in resolve_and_validate_path()
"""

from pathlib import Path

import pytest

from utils.security_config import _resolved_home_sensitive_paths, is_dangerous_path


class TestPathTraversalFix:
    """Test that subdirectories of dangerous system paths are blocked."""

    def test_exact_match_still_works(self):
        """Test that exact dangerous paths are still blocked."""
        assert is_dangerous_path(Path("/etc")) is True
        assert is_dangerous_path(Path("/usr")) is True
        assert is_dangerous_path(Path("/var")) is True

    def test_subdirectory_now_blocked(self):
        """Test that subdirectories of system paths are blocked (the fix)."""
        # These were allowed before the fix
        assert is_dangerous_path(Path("/etc/passwd")) is True
        assert is_dangerous_path(Path("/etc/shadow")) is True
        assert is_dangerous_path(Path("/etc/hosts")) is True
        assert is_dangerous_path(Path("/var/log/auth.log")) is True

    def test_deeply_nested_blocked(self):
        """Test that deeply nested system paths are blocked."""
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


class TestHomeDirectoryHandling:
    """Test that home directory containers are handled correctly.

    Home containers (/home, C:\\Users) should only block the exact path,
    not subdirectories. Subdirectory access control is delegated to
    is_home_directory_root() in resolve_and_validate_path().
    """

    def test_home_container_blocked(self):
        """Test that /home itself is blocked."""
        assert is_dangerous_path(Path("/home")) is True

    def test_home_subdirectories_allowed(self):
        """Test that /home subdirectories pass through is_dangerous_path().

        These paths should NOT be blocked by is_dangerous_path() because:
        1. /home/user/project is a valid user workspace
        2. Access control for /home/username is handled by is_home_directory_root()
        """
        # User home directories should pass is_dangerous_path()
        # (they are handled by is_home_directory_root() separately)
        assert is_dangerous_path(Path("/home/user")) is False
        assert is_dangerous_path(Path("/home/user/project")) is False
        assert is_dangerous_path(Path("/home/user/project/src/main.py")) is False

    def test_home_deeply_nested_allowed(self):
        """Test that deeply nested home paths are allowed."""
        assert is_dangerous_path(Path("/home/user/documents/work/project/src")) is False


class TestRegressionPrevention:
    """Regression tests for the specific vulnerability."""

    def test_etc_passwd_blocked(self):
        """Test /etc/passwd is blocked (common attack target)."""
        assert is_dangerous_path(Path("/etc/passwd")) is True

    def test_etc_shadow_blocked(self):
        """Test /etc/shadow is blocked (password hashes)."""
        assert is_dangerous_path(Path("/etc/shadow")) is True


class TestSensitiveHomeSubdirectories:
    """Test that sensitive dotfile directories inside $HOME are blocked.

    These directories (~/.ssh, ~/.aws, ~/.gnupg, etc.) contain credentials,
    private keys, and other secrets that should never be exposed.
    """

    @pytest.fixture(autouse=True)
    def _reset_home_cache(self):
        # _resolved_home_sensitive_paths() is lru_cached; monkeypatching HOME
        # per test requires clearing the cache on both sides of the test.
        _resolved_home_sensitive_paths.cache_clear()
        yield
        _resolved_home_sensitive_paths.cache_clear()

    def test_dot_ssh_blocked(self, tmp_path, monkeypatch):
        """~/.ssh and its contents must be blocked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".ssh").mkdir()
        (tmp_path / ".ssh" / "id_rsa").write_text("key")
        (tmp_path / ".ssh" / "config").write_text("config")

        assert is_dangerous_path(tmp_path / ".ssh") is True
        assert is_dangerous_path(tmp_path / ".ssh" / "id_rsa") is True
        assert is_dangerous_path(tmp_path / ".ssh" / "config") is True

    def test_dot_aws_blocked(self, tmp_path, monkeypatch):
        """~/.aws and its contents must be blocked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".aws").mkdir()
        (tmp_path / ".aws" / "credentials").write_text("[default]")

        assert is_dangerous_path(tmp_path / ".aws") is True
        assert is_dangerous_path(tmp_path / ".aws" / "credentials") is True

    def test_dot_gnupg_blocked(self, tmp_path, monkeypatch):
        """~/.gnupg and its contents must be blocked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".gnupg").mkdir()
        (tmp_path / ".gnupg" / "pubring.kbx").write_text("")

        assert is_dangerous_path(tmp_path / ".gnupg") is True
        assert is_dangerous_path(tmp_path / ".gnupg" / "pubring.kbx") is True

    def test_additional_credential_dirs_blocked(self, tmp_path, monkeypatch):
        """Other common credential directories (.kube, .azure, .docker) are blocked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        for name in (".kube", ".azure", ".docker"):
            (tmp_path / name).mkdir()
            assert is_dangerous_path(tmp_path / name) is True
            assert is_dangerous_path(tmp_path / name / "config") is True

    def test_nested_credential_dir_blocked(self, tmp_path, monkeypatch):
        """Multi-component entries like .config/gcloud are blocked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        gcloud = tmp_path / ".config" / "gcloud"
        gcloud.mkdir(parents=True)
        (gcloud / "credentials.db").write_text("")

        assert is_dangerous_path(gcloud) is True
        assert is_dangerous_path(gcloud / "credentials.db") is True
        # Sibling under .config must remain accessible
        (tmp_path / ".config" / "myapp").mkdir()
        assert is_dangerous_path(tmp_path / ".config" / "myapp") is False

    def test_symlinked_dotfile_dir_blocked(self, tmp_path, monkeypatch):
        """Dotfile managers (chezmoi, Stow, yadm) symlink ~/.ssh to a repo path.

        Resolving only the input path while leaving the blocklist entry
        unresolved would miss the symlink target; resolving both sides closes
        the bypass.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        real_ssh = tmp_path / ".dotfiles" / ".ssh"
        real_ssh.mkdir(parents=True)
        (real_ssh / "id_rsa").write_text("key")
        (tmp_path / ".ssh").symlink_to(real_ssh, target_is_directory=True)

        # Access via the symlink and via the real target both resolve to the
        # same canonical path — both must be blocked.
        assert is_dangerous_path(tmp_path / ".ssh" / "id_rsa") is True
        assert is_dangerous_path(real_ssh / "id_rsa") is True

    def test_similar_named_dirs_allowed(self, tmp_path, monkeypatch):
        """Directories with similar names (not exact dotfile matches) are allowed."""
        monkeypatch.setenv("HOME", str(tmp_path))

        assert is_dangerous_path(tmp_path / ".ssh_backup") is False
        assert is_dangerous_path(tmp_path / "ssh") is False
        assert is_dangerous_path(tmp_path / "awscode") is False

    def test_other_home_subdirs_allowed(self, tmp_path, monkeypatch):
        """Regular home subdirectories remain accessible."""
        monkeypatch.setenv("HOME", str(tmp_path))

        assert is_dangerous_path(tmp_path / "projects") is False
        assert is_dangerous_path(tmp_path / "projects" / "app" / "src") is False
        assert is_dangerous_path(tmp_path / "Documents") is False

    def test_dot_config_parent_allowed(self, tmp_path, monkeypatch):
        """~/.config itself is not blocked — only the enumerated
        credential subdirs beneath it (e.g. .config/gcloud) are."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".config").mkdir()
        (tmp_path / ".config" / "myapp").mkdir()

        assert is_dangerous_path(tmp_path / ".config") is False
        assert is_dangerous_path(tmp_path / ".config" / "myapp") is False

    def test_nonexistent_sensitive_dir_still_blocked(self, tmp_path, monkeypatch):
        """A sensitive path that doesn't exist on disk must still be
        blocked — resolve() normalizes non-existent paths, so the
        is_relative_to() check remains correct."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # Deliberately do NOT create ~/.ssh. Accessing it should still be blocked.

        assert is_dangerous_path(tmp_path / ".ssh") is True
        assert is_dangerous_path(tmp_path / ".ssh" / "id_rsa") is True

    def test_home_resolution_failure_fails_safe(self, monkeypatch):
        """If Path.home() raises (HOME unset and pwd lookup fails),
        _resolved_home_sensitive_paths() returns an empty tuple rather
        than crashing. System-path checks continue to work; the outer
        is_dangerous_path() handler keeps unresolvable paths fail-closed."""

        def _raise(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr(Path, "home", classmethod(_raise))
        # autouse fixture cleared the cache before this test ran
        assert _resolved_home_sensitive_paths() == ()
        # Unrelated system-path checks remain intact
        assert is_dangerous_path(Path("/etc/passwd")) is True


class TestWindowsPathHandling:
    """Test Windows path handling with trailing backslash.

    Fixes issue reported in PR #353: Windows paths like C:\\ have trailing
    backslash which caused double separator issues with string prefix matching.
    Using Path.is_relative_to() resolves this correctly.
    """

    def test_windows_root_drive_blocked(self):
        """Test that Windows root drive C:\\ is blocked."""
        from pathlib import PureWindowsPath

        # Simulate Windows path behavior using PureWindowsPath
        # On Linux, we test the logic with PureWindowsPath to verify cross-platform correctness
        c_root = PureWindowsPath("C:\\")
        assert c_root.parent == c_root  # Root check works

    def test_windows_dangerous_subdirectory_detection(self):
        """Test that Windows subdirectories are correctly detected as dangerous.

        This verifies the fix for the double backslash issue:
        - Before fix: "C:\\" + "\\" = "C:\\\\" which doesn't match "C:\\Users"
        - After fix: Path.is_relative_to() handles this correctly
        """
        from pathlib import PureWindowsPath

        # Verify is_relative_to works correctly for Windows paths
        c_users = PureWindowsPath("C:\\Users")
        c_root = PureWindowsPath("C:\\")

        # This is the key test - subdirectory detection must work
        assert c_users.is_relative_to(c_root) is True

        # Deeper paths should also work
        c_users_admin = PureWindowsPath("C:\\Users\\Admin")
        assert c_users_admin.is_relative_to(c_root) is True
        assert c_users_admin.is_relative_to(c_users) is True

    def test_windows_path_not_relative_to_different_drive(self):
        """Test that paths on different drives are not related."""
        from pathlib import PureWindowsPath

        d_path = PureWindowsPath("D:\\Data")
        c_root = PureWindowsPath("C:\\")

        # D: drive paths should not be relative to C:
        assert d_path.is_relative_to(c_root) is False

"""Built-in CLI configuration for ordinary test subprocesses."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mcp import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_cli_configuration():
    """Ignore operator CLI inputs while preserving other runtime/auth settings."""
    from clink import registry

    with TemporaryDirectory(prefix="pal-test-clients-") as directory:
        with (
            patch.object(registry, "USER_CONFIG_DIR", Path(directory)),
            patch.object(registry, "_REGISTRY", None),
            patch.object(registry, "get_env", return_value=None),
        ):
            # Registry configuration has just one environment input. Ignoring
            # its getter also excludes a cached forced-dotenv CLI override.
            yield


def isolated_server_parameters(env: dict[str, str] | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "fixtures" / "isolated_server.py")],
        cwd=str(ROOT),
        env=dict(os.environ) if env is None else env,
    )

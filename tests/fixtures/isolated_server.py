"""Start a real test server without personal PAL CLI manifests or dotenv input."""

import os
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ["PAL_SKIP_DOTENV"] = "true"

    from tests.cli_isolation import isolated_cli_configuration

    with isolated_cli_configuration():
        import server

        server.run()


if __name__ == "__main__":
    main()

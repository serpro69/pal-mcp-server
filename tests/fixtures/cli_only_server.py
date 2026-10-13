"""Isolated launcher for real-stdio clink acceptance; never imported by pytest."""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    violations = root / "forbidden-calls.txt"

    def forbid(label):
        def fail(*args, **kwargs):
            with violations.open("a", encoding="utf-8") as stream:
                stream.write(label + "\n")
            raise AssertionError(f"CLI-only acceptance forbids {label}")

        return fail

    # Install before any PAL import: even a caught dotenv read must fail the test.
    import dotenv

    dotenv.dotenv_values = forbid("dotenv_values")
    dotenv.load_dotenv = forbid("load_dotenv")
    assert os.environ["PAL_SKIP_DOTENV"] == "true"

    import clink.registry as cli_registry
    from providers.configuration import provider_classes, provider_configurations
    from providers.registry import ModelProviderRegistry
    from utils.model_context import ModelContext

    # Preserve shipped manifests and normal operator overrides, isolating only
    # the personal configuration path before server constructs any tool objects.
    assert cli_registry._REGISTRY is None
    cli_registry.USER_CONFIG_DIR = root / "user-cli-config"
    settings_present = sorted(
        {
            name
            for declaration in provider_configurations().values()
            for name in declaration.environment_names()
            if os.environ.get(name)
        }
    )
    assert not settings_present, settings_present
    assert not ModelProviderRegistry()._providers
    assert not ModelProviderRegistry()._initialized_providers
    for name in (
        "register_provider",
        "get_provider",
        "get_provider_for_model",
        "get_preferred_fallback_model",
        "resolve_model_identity",
    ):
        setattr(ModelProviderRegistry, name, staticmethod(forbid(name)))
    for factory in provider_classes():
        factory.generate_content = forbid(f"{factory.__name__}.generate_content")
    # Patch the constructor itself so existing imported class aliases are guarded.
    ModelContext.__init__ = forbid("ModelContext")

    (root / "isolation.json").write_text(
        json.dumps(
            {
                "provider_settings_present": settings_present,
                "user_cli_config": str(cli_registry.USER_CONFIG_DIR),
                "pytest_loaded": "pytest" in sys.modules,
            }
        ),
        encoding="utf-8",
    )

    import server

    try:
        server.run()
    finally:
        (root / "server-stopped").write_text("stopped", encoding="utf-8")


if __name__ == "__main__":
    main()

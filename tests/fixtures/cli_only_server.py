"""Isolated launcher for real-stdio clink acceptance; never imported by pytest."""

import json
import os
import sys
from pathlib import Path


def observe_directory_calls(root, server):
    """Opt-in test observations around the real SDK handler and OS spawn call."""
    import asyncio
    import re

    from mcp.types import CallToolRequest

    from clink.registry import get_registry
    from utils import conversation_memory as memory

    observations = root / "observations"
    observations.mkdir()
    seed_file = root / "seeds.json"
    seeds = json.loads(seed_file.read_text(encoding="utf-8")) if seed_file.exists() else {}
    seeded = {}
    for name, definition in seeds.items():
        thread_id = memory.create_thread(definition["tool"], definition["context"])
        for role, content in definition.get("turns", []):
            memory.add_turn(thread_id, role, content, tool_name=definition["tool"], required=True)
        seeded[name] = thread_id
    (root / "seeded.json").write_text(json.dumps(seeded), encoding="utf-8")

    def snapshot():
        storage = memory.get_storage()
        with storage._lock:
            stored = dict(storage._store)
        registry = get_registry()
        return {
            "storage": stored,
            "cwd": os.getcwd(),
            "clients": {name: registry.get_client(name).model_dump(mode="json") for name in registry.list_clients()},
        }

    sdk_handler = server.server.request_handlers[CallToolRequest]

    async def observed_handler(request):
        if request.params.name != "clink":
            return await sdk_handler(request)
        stage = re.findall(r"CLINK_TEST_STAGE=(\w+)", request.params.arguments["prompt"])[-1]
        before = snapshot()
        try:
            # Includes the SDK's input-schema validation; never dispatch around it.
            return await sdk_handler(request)
        finally:
            with (observations / f"{stage}.json").open("x", encoding="utf-8") as stream:
                json.dump({"before": before, "after": snapshot()}, stream)

    server.server.request_handlers[CallToolRequest] = observed_handler
    native_spawn = asyncio.create_subprocess_exec

    async def observed_spawn(*args, **kwargs):
        process = await native_spawn(*args, **kwargs)
        with (root / "spawns.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": process.pid, "cwd": kwargs.get("cwd"), "argv": args}) + "\n")
        return process

    asyncio.create_subprocess_exec = observed_spawn


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

    if os.environ.get("CLINK_TEST_OBSERVE") == "true":
        observe_directory_calls(root, server)

    try:
        server.run()
    finally:
        (root / "server-stopped").write_text("stopped", encoding="utf-8")


if __name__ == "__main__":
    main()

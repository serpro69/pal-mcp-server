"""Project selection through real MCP discovery, SDK validation and stdio calls."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_cli_only_mcp as cli
from tests.test_clink_working_directory_errors import validation_metadata
from tests.test_clink_working_directory_subprocess import child_gate, receive_child

VENDORS = ("agy", "claude", "codex", "gemini")
cli_server = cli.cli_server


@pytest.fixture
def projects(cli_server):
    home = cli_server.root / "home"
    home.mkdir()
    a, b, configured = [home / name for name in ("project A", "project B", "configured")]
    for path in (a, b, configured):
        path.mkdir()
        (path / "project.marker").write_text(path.name, encoding="utf-8")
    alias = home / "alias"
    alias.symlink_to(a, target_is_directory=True)
    cli_server.vendors = VENDORS
    cli_server.observe_directories = True
    cli_server.client_overrides = {
        name: {
            "env": {
                "CLINK_TEST_SCENARIO": "directory",
                "CLINK_TEST_MARKER": "project.marker",
                "CLINK_TEST_FAILURE_STAGE": "failed_promotion",
            }
        }
        for name in VENDORS
    }
    return SimpleNamespace(a=a, b=b, configured=configured, alias=alias)


def binding(path):
    return (
        {"mode": "scratch"}
        if path is None
        else {
            "mode": "directory",
            "launch_path": str(path),
            "canonical_path": str(path.resolve()),
        }
    )


def audit(server, stage):
    return json.loads((server.root / "observations" / f"{stage}.json").read_text(encoding="utf-8"))


def capture(server, vendor, stage):
    return json.loads((server.root / "captures" / vendor / f"{stage}.json").read_text(encoding="utf-8"))


def thread_after(server, stage, thread_id):
    stored = audit(server, stage)["after"]["storage"][f"thread:{thread_id}"]
    return json.loads(stored[0])


def assert_unchanged(server, stage, *, launched=False, vendor=None):
    observed = audit(server, stage)
    assert observed["after"] == observed["before"]  # Includes exact serialized state and expiration timestamps.
    paths = list((server.root / "captures").glob(f"*/{stage}.json"))
    assert len(paths) == int(launched)
    if launched:
        assert paths[0].parent.name == vendor


def assert_success(server, vendor, stage, result, path, source):
    observed = audit(server, stage)
    assert observed["before"]["clients"] == observed["after"]["clients"]
    assert observed["before"]["cwd"] == observed["after"]["cwd"]
    assert os.path.samefile(observed["after"]["cwd"], cli.REPO)
    captured = capture(server, vendor, stage)
    assert not server.provider_settings.intersection(captured["environment_names"])
    spawn = next(
        item
        for line in (server.root / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        if (item := json.loads(line))["pid"] == captured["pid"]
    )
    assert json.loads(result["content"]) == {"cwd": captured["cwd"], "marker": captured["marker"]}
    assert result["metadata"]["working_directory"] == {
        "mode": "scratch" if path is None else "directory",
        "launch_path": None if path is None else str(path),
        "canonical_path": None if path is None else str(path.resolve()),
        "source": source,
    }
    thread_id = result["continuation_offer"]["continuation_id"]
    assert thread_after(server, stage, thread_id)["initial_context"]["clink_working_directory"] == binding(path)
    if path is None:
        assert captured["marker"] is None
        assert not Path(spawn["cwd"]).exists()
        assert Path(spawn["cwd"]).name.startswith("pal-clink-")
    else:
        assert spawn["cwd"] == str(path)
        assert os.path.samefile(captured["cwd"], path)
        assert captured["marker"] == (path / "project.marker").read_text(encoding="utf-8")
    return thread_id


async def call(session, vendor, stage, *, error=False, **fields):
    result = await session.call_tool(
        "clink", {"cli_name": vendor, "prompt": f"CLINK_TEST_STAGE={stage} INPUT_{stage}", **fields}
    )
    return cli.payload(result, error=error)


def assert_directory_error(result, expected):
    assert result["continuation_offer"] is None
    details = dict(result["metadata"])
    execution = details.pop("execution")
    assert isinstance(execution["operations"], list)
    assert details == expected


def seed(server, definitions):
    (server.root / "seeds.json").write_text(json.dumps(definitions), encoding="utf-8")


def seeded_ids(server):
    return json.loads((server.root / "seeded.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vendor", VENDORS)
async def test_mcp_schema_projects_aliases_conflicts_and_validation_boundaries(cli_server, projects, vendor):
    async with cli_server.connect() as (session, initialized):
        assert "No PAL model providers are configured" in initialized.instructions
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        schema = tools["clink"].inputSchema
        assert schema["properties"]["working_directory_absolute_path"]["type"] == ["string", "null"]
        assert "working_directory_absolute_path" not in schema["required"]
        assert "working_dir" not in schema["properties"]
        assert "anyOf" not in schema  # Anthropic API rejects top-level anyOf; rule is enforced at runtime
        assert not cli_server.stages()

        first = await call(session, vendor, "first", working_directory_absolute_path="~/alias/./")
        thread_id = assert_success(cli_server, vendor, "first", first, projects.alias, "request")
        for stage, fields in (("omitted", {}), ("equivalent", {"working_directory_absolute_path": str(projects.a)})):
            result = await call(session, vendor, stage, continuation_id=thread_id, **fields)
            assert assert_success(cli_server, vendor, stage, result, projects.alias, "conversation") == thread_id
        requested = f"{projects.b}/./"
        conflict = await call(
            session,
            vendor,
            "conflict",
            error=True,
            continuation_id=thread_id,
            working_directory_absolute_path=requested,
        )
        assert "start a new conversation" in conflict["content"]
        assert_directory_error(
            conflict,
            validation_metadata(
                "WorkingDirectoryConflictError",
                bound=binding(projects.alias),
                requested=requested,
                normalized=str(projects.b),
                canonical=str(projects.b.resolve()),
                new=True,
            ),
        )
        assert_unchanged(cli_server, "conflict")
        fresh = await call(session, vendor, "fresh", working_directory_absolute_path=str(projects.b))
        assert assert_success(cli_server, vendor, "fresh", fresh, projects.b, "request") != thread_id

        for i, value in enumerate((42, False, [], {}, 1.5)):
            stage = f"wrong_type_{i}"
            rejected = await session.call_tool(
                "clink",
                {
                    "cli_name": vendor,
                    "prompt": f"CLINK_TEST_STAGE={stage}",
                    "continuation_id": thread_id,
                    "working_directory_absolute_path": value,
                },
            )
            assert rejected.isError and rejected.structuredContent is None
            assert rejected.content[0].text.startswith("Input validation error:")
            assert_unchanged(cli_server, stage)
        for i, value in enumerate(("", "relative", str(projects.a / "missing"))):
            stage = f"invalid_path_{i}"
            rejected = await call(
                session, vendor, stage, error=True, continuation_id=thread_id, working_directory_absolute_path=value
            )
            assert_directory_error(
                rejected,
                validation_metadata(
                    "InvalidWorkingDirectoryError",
                    bound=binding(projects.alias),
                    requested=value,
                    normalized=None if value == "" else value,
                ),
            )
            assert_unchanged(cli_server, stage)
        continued = await call(session, vendor, "after_rejections", continuation_id=thread_id)
        assert_success(cli_server, vendor, "after_rejections", continued, projects.alias, "conversation")
        assert continued["metadata"]["history"]["retained_messages"] == 6


@pytest.mark.parametrize("vendor", VENDORS)
async def test_mcp_manifest_override_scratch_promotion_and_failed_promotion(cli_server, projects, vendor):
    other = VENDORS[(VENDORS.index(vendor) + 1) % len(VENDORS)]
    cli_server.client_overrides[other]["working_dir"] = str(projects.configured)
    async with cli_server.connect() as (session, _):
        first = await call(session, vendor, "scratch")
        thread_id = assert_success(cli_server, vendor, "scratch", first, None, "scratch")
        inherited = await call(session, other, "scratch_inherited", continuation_id=thread_id)
        assert_success(cli_server, other, "scratch_inherited", inherited, None, "conversation")
        failed = await call(
            session,
            other,
            "failed_promotion",
            error=True,
            continuation_id=thread_id,
            working_directory_absolute_path=str(projects.alias),
        )
        assert failed["continuation_offer"] is None
        assert failed["metadata"]["return_code"] == 7
        assert failed["metadata"]["working_directory"]["source"] == "request"
        assert_unchanged(cli_server, "failed_promotion", launched=True, vendor=other)
        assert thread_after(cli_server, "failed_promotion", thread_id)["initial_context"][
            "clink_working_directory"
        ] == {"mode": "scratch"}
        still_scratch = await call(session, other, "after_failure", continuation_id=thread_id)
        assert_success(cli_server, other, "after_failure", still_scratch, None, "conversation")
        promoted = await call(
            session, other, "promoted", continuation_id=thread_id, working_directory_absolute_path=str(projects.alias)
        )
        assert assert_success(cli_server, other, "promoted", promoted, projects.alias, "request") == thread_id
        history = capture(cli_server, other, "promoted")["prompt"]
        assert "INPUT_scratch" in history and first["content"] in history
        assert "INPUT_failed_promotion" not in history
        assert promoted["metadata"]["history"]["retained_messages"] == 6
        following = await call(session, vendor, "promoted_followup", continuation_id=thread_id)
        assert_success(cli_server, vendor, "promoted_followup", following, projects.alias, "conversation")
        fallback = await call(session, other, "manifest")
        assert_success(cli_server, other, "manifest", fallback, projects.configured, "configuration")
        overridden = await call(session, other, "override", working_directory_absolute_path=str(projects.b))
        assert_success(cli_server, other, "override", overridden, projects.b, "request")


@pytest.mark.parametrize("vendor", VENDORS)
async def test_mcp_legacy_and_api_origin_threads_bind_only_after_success(cli_server, projects, vendor):
    definitions = {
        "legacy": {
            "tool": "clink",
            "context": {"cli_name": vendor, "working_dir": str(projects.b), "origin": "legacy"},
        },
        "api": {"tool": "chat", "context": {"working_directory_absolute_path": str(projects.b), "origin": "api"}},
        "corrupt": {"tool": "clink", "context": {"clink_working_directory": None}},
    }
    for name, definition in definitions.items():
        definition["turns"] = [["user", f"OLD_INPUT_{name}"], ["assistant", f"OLD_REPLY_{name}"]]
    seed(cli_server, definitions)
    cli_server.client_overrides[vendor]["working_dir"] = str(projects.configured)
    async with cli_server.connect() as (session, _):
        ids = seeded_ids(cli_server)
        failed = await call(session, vendor, "failed_promotion", error=True, continuation_id=ids["api"])
        assert failed["metadata"]["return_code"] == 7
        assert_unchanged(cli_server, "failed_promotion", launched=True, vendor=vendor)
        assert (
            "clink_working_directory" not in thread_after(cli_server, "failed_promotion", ids["api"])["initial_context"]
        )
        for name in ("legacy", "api"):
            result = await call(session, vendor, name, continuation_id=ids[name])
            assert assert_success(cli_server, vendor, name, result, projects.configured, "configuration") == ids[name]
            assert result["metadata"]["history"]["retained_messages"] == 2
            prompt = capture(cli_server, vendor, name)["prompt"]
            assert f"OLD_INPUT_{name}" in prompt and f"OLD_REPLY_{name}" in prompt
            context = thread_after(cli_server, name, ids[name])["initial_context"]
            assert all(context[key] == value for key, value in definitions[name]["context"].items())
        corrupt = await call(session, vendor, "corrupt", error=True, continuation_id=ids["corrupt"])
        assert_directory_error(corrupt, validation_metadata("InvalidWorkingDirectoryBindingError", new=True))
        assert_unchanged(cli_server, "corrupt")


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("configured_fallback", [False, True])
async def test_mcp_distinct_bound_threads_overlap_without_crossing_projects(
    cli_server, projects, vendor, configured_fallback
):
    paths = (projects.alias, projects.b, projects.configured if configured_fallback else None)
    seed(
        cli_server,
        {
            str(i): {
                "tool": "clink",
                "context": {"clink_working_directory": binding(path)},
                "turns": [["user", f"THREAD_{i}_INPUT"], ["assistant", f"THREAD_{i}_REPLY"]],
            }
            for i, path in enumerate(paths)
        },
    )
    cli_server.client_overrides[vendor]["working_dir"] = str(projects.configured)
    cli_server.client_overrides[vendor]["env"]["CLINK_TEST_SCENARIO"] = "held-directory"
    async with child_gate() as gate:
        async with cli_server.connect(gate_port=gate.port) as (session, _):
            ids = seeded_ids(cli_server)
            tasks = [
                asyncio.create_task(call(session, vendor, f"parallel_{i}", continuation_id=ids[str(i)]))
                for i in range(3)
            ]
            try:
                writers = [await receive_child(gate) for _ in tasks]
                assert all(not task.done() for task in tasks)
                assert cli_server.stages() == {f"parallel_{i}" for i in range(3)}
                for i, path in enumerate(paths):
                    captured = capture(cli_server, vendor, f"parallel_{i}")
                    assert Path(captured["cwd"]).is_dir()
                    if path is not None:
                        assert os.path.samefile(captured["cwd"], path)
                    assert f"THREAD_{i}_INPUT" in captured["prompt"] and f"THREAD_{i}_REPLY" in captured["prompt"]
                    assert all(f"THREAD_{j}_INPUT" not in captured["prompt"] for j in range(3) if j != i)
                for writer in writers:
                    writer.write(b"G")
                    await writer.drain()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for i, (path, result) in enumerate(zip(paths, results)):
                assert assert_success(cli_server, vendor, f"parallel_{i}", result, path, "conversation") == ids[str(i)]

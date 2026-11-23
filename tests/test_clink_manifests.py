"""Registration contracts shared by startup, discovery and explicit registry reloads."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from clink.constants import BUILTIN_PROMPTS_DIR
from clink.models import GenericCLIClientConfig
from clink.registry import ClinkRegistry, RegistryLoadError

FIXTURES = Path(__file__).parent / "fixtures"
MANIFESTS = FIXTURES / "clink_generic"


@pytest.fixture
def manifest():
    return json.loads((MANIFESTS / "team-ask.json").read_text(encoding="utf-8"))


@pytest.fixture
def source(tmp_path, monkeypatch):
    directory = tmp_path / "clients"
    directory.mkdir()
    monkeypatch.setattr("clink.registry.get_env", lambda name: str(directory))
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", tmp_path / "absent")
    return directory


@pytest.mark.parametrize("key", ["name", "command", "safe_args", "edit_args", "env_passthrough", "custom"])
def test_generic_requires_explicit_fields(manifest, key):
    del manifest[key]
    with pytest.raises(ValidationError, match=key):
        GenericCLIClientConfig.model_validate(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", " \t"),
        ("name", "client\n"),
        ("command", "'' argument"),
        ("command", " "),
        ("command", "'unclosed"),
        ("command", "bad\0command"),
        ("safe_args", None),
        ("safe_args", "--safe"),
        ("safe_args", [1]),
        ("safe_args", ["bad\0arg"]),
        ("edit_args", None),
        ("additional_args", "--other"),
        ("model_args", "--model"),
        ("env_passthrough", "ACCESS_TOKEN"),
        ("env_passthrough", [""]),
        ("env_passthrough", ["A=B"]),
        ("env_passthrough", ["BAD\0KEY"]),
        ("env", {"": "value"}),
        ("env", {"A=B": "value"}),
        ("env", {"A": "bad\0value"}),
        ("env", {"A": 2}),
        ("roles", {" ": {}}),
        ("roles", {"bad\x7f": {}}),
        ("roles", {"r": {"role_args": "x"}}),
        ("roles", {"r": {"role_args": ["a\0b"]}}),
        ("roles", {"r": {"typo": True}}),
        ("roles", {"r": {"role_args": [True]}}),
        ("output_to_file", None),
        ("runner", "codex"),
        ("parser", "agy_jsonl"),
        ("typo", 1),
    ],
)
def test_generic_rejects_invalid_fields_before_coercion(manifest, field, value):
    manifest[field] = value
    with pytest.raises(ValidationError, match=field):
        GenericCLIClientConfig.model_validate(manifest)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("prompt", {"mode": "stdin", "flag": "-q"}),
        ("prompt", {"mode": "flag"}),
        ("prompt", {"mode": "flag", "flag": "---q"}),
        ("prompt", {"mode": "flag", "flag": "--q\n"}),
        ("prompt", {"mode": "flag", "flag": "-1"}),
        ("prompt", {"mode": "file"}),
        ("output", {"format": "text", "path": []}),
        ("output", {"format": "json"}),
        ("output", {"format": "json", "path": []}),
        ("output", {"format": "json", "path": [""]}),
        ("output", {"format": "json", "path": ["a", 1]}),
        ("output", {"format": "jsonl"}),
        ("workspace", "git"),
        ("failure_hint", " "),
        ("failure_hint", "a\nb"),
        ("failure_hint", "x" * 241),
        ("log_stderr_on_failure", 1),
        ("log_stderr_on_failure", "false"),
        ("unexpected", True),
    ],
)
def test_custom_protocol_rejects_invalid_fields(manifest, key, value):
    manifest["custom"][key] = value
    with pytest.raises(ValidationError, match=key):
        GenericCLIClientConfig.model_validate(manifest)


@pytest.mark.parametrize("key", ["prompt", "output", "workspace", "failure_hint"])
def test_generic_requires_protocol_fields(manifest, key):
    del manifest["custom"][key]
    with pytest.raises(ValidationError, match=key):
        GenericCLIClientConfig.model_validate(manifest)


@pytest.mark.parametrize("flag", ["-q", "-question", "--question"])
def test_flag_preserves_spelling_and_reserves_whole_names(manifest, flag):
    manifest["custom"].update(prompt={"mode": "flag", "flag": flag}, log_stderr_on_failure=True)
    manifest["additional_args"] = ["-quiet", "--questionnaire=x"]
    manifest["custom"]["output"] = {"format": "json", "path": ["literal.key"]}
    protocol = GenericCLIClientConfig.model_validate(manifest).custom
    assert protocol.prompt.flag == flag and protocol.output.path == ["literal.key"]
    assert protocol.log_stderr_on_failure is True
    for spelling in ("-", "--"):
        for suffix in ("", "=value"):
            manifest["additional_args"] = [spelling + flag.lstrip("-") + suffix]
            with pytest.raises(ValidationError, match="additional_args conflicts"):
                GenericCLIClientConfig.model_validate(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "team-ask --m=x"),
        ("safe_args", ["-m"]),
        ("edit_args", ["--m=x"]),
        ("model_args", ["-m", "{model}"]),
        ("roles", {"r": {"role_args": ["--m"]}}),
        ("additional_args", ["--"]),
    ],
)
def test_reserved_options_in_every_argument_bucket(manifest, field, value):
    manifest["custom"]["prompt"] = {"mode": "flag", "flag": "--m"}
    manifest[field] = value
    with pytest.raises(ValidationError, match=r"conflicts with custom.prompt.flag"):
        GenericCLIClientConfig.model_validate(manifest)


def test_generic_replacement_and_resource_resolution(source, manifest):
    (source / "01.json").write_text(json.dumps({**manifest, "roles": {"old": {}}}))
    (source / "role.txt").write_text("Operator prompt")
    shadow = source / "systemprompts" / "clink" / "generic_default.txt"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("Shadow prompt")
    (source / "02.json").write_text(
        json.dumps({**manifest, "name": "Team-Ask", "roles": {"other": {}, "explicit": {"prompt_path": "role.txt"}}})
    )
    client = ClinkRegistry().get_client("TEAM-ASK")
    neutral = BUILTIN_PROMPTS_DIR / "generic_default.txt"
    assert neutral.is_file()
    assert client.name == "Team-Ask" and set(client.roles) == {"default", "explicit", "other"}
    assert client.get_role(None).prompt_path == client.get_role("other").prompt_path == neutral
    assert client.get_role("explicit").prompt_path == source / "role.txt"
    assert client.custom.log_stderr_on_failure is False
    assert client.safe_args == client.edit_args == client.env_passthrough == []


def test_shipped_override_keeps_native_defaults_and_legacy_fields(source):
    path = source / "override.json"
    path.write_text(json.dumps({"name": "CoDeX", "command": "other", "safe_args": "-s", "legacy_field": 1}))
    client = ClinkRegistry().get_client("codex")
    assert client.executable == ["other"] and client.safe_args == ["-s"] and client.edit_args == []
    assert client.runner == "codex" and client.parser == "codex_jsonl" and client.custom is None
    path.write_text(json.dumps({"name": "CoDeX", "command": "other", "custom": None}))
    with pytest.raises(RegistryLoadError, match="custom"):
        ClinkRegistry()


def test_caseless_replacement_preserves_later_display_name(source, manifest):
    for index, name in enumerate(("Straße", "STRASSE")):
        (source / f"{index}.json").write_text(json.dumps({**manifest, "name": name}))
    registry = ClinkRegistry()
    assert registry.get_client("Straße").name == "STRASSE"
    assert "Straße" not in registry.list_clients()


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (b"", "line 1, column 1"),
        (b"{}", "nonempty JSON object"),
        (b"[]", "nonempty JSON object"),
        (b'{"damaged": "PRIVATE",\n}', "line 2, column 1"),
        (b'{"safe_args": ["PRIVATE"], "safe_args": []}', "duplicate member 'safe_args'"),
        (b'{"nested": {"role_args": [], "role_args": []}}', "duplicate member 'role_args'"),
        (b'{"env":{"TOKEN=PRIVATE":"a","TOKEN=PRIVATE":"b"}}', "duplicate member 'TOKEN=[redacted]'"),
        (
            b'{"name":"codex-work","command":"codex"}',
            "generic manifest: missing required fields: safe_args, edit_args, env_passthrough, custom",
        ),
        (b'{"name":"codex"}', "command must specify an executable"),
        (b"""{"name":"codex","command":"'PRIVATE"}""", "command has invalid quoting"),
        (rb'{"name":"codex","command":"codex","working_dir":"PRIVATE\u0000"}', "working_dir"),
        (b'\xef\xbb\xbf{"name":"codex"}', "byte-order mark"),
        (b'{"name":"codex","history_token_budget":"PRIVATE"}', "history_token_budget"),
        (b'{"name":"codex","command":"codex","roles":{"r":{"prompt_path":"PRIVATE"}}}', "prompt_path"),
        (b'{"name":"codex","env":{"PRIVATE":"\xff"}}', "UTF-8"),
        (b'{"text":"NaN",\n"n": NaN}', "line 2, column 6"),
        (rb'{"text":"\"Infinity\"","n":Infinity}', "line 1, column 28"),
        (b'{"text":"-Infinity",\n"n": -Infinity}', "line 2, column 6"),
    ],
)
def test_bad_later_file_is_fatal_and_reload_is_atomic(source, manifest, contents, reason, caplog):
    registry = ClinkRegistry()
    previous = registry.get_client("codex")
    (source / "01.json").write_text(json.dumps(manifest))
    bad = source / "02.json"
    bad.write_bytes(contents)
    with pytest.raises(RegistryLoadError) as caught:
        registry.reload()
    for diagnostic in (str(caught.value), caplog.text):
        assert str(bad) in diagnostic and reason in diagnostic
        assert "PRIVATE" not in diagnostic
    assert registry.get_client("codex") is previous
    assert "team-ask" not in registry.list_clients()


def test_empty_directory_is_valid_but_an_explicit_missing_source_is_fatal(source):
    assert ClinkRegistry().list_clients() == ["agy", "claude", "codex", "gemini"]
    source.rmdir()
    with pytest.raises(RegistryLoadError, match="source missing"):
        ClinkRegistry()


@pytest.mark.parametrize("filename", ["broken.json", ".hidden.json"])
def test_dangling_manifest_fails_with_file_location(source, filename):
    link = source / filename
    link.symlink_to(source / "missing")
    with pytest.raises(RegistryLoadError) as caught:
        ClinkRegistry()
    assert str(link) in str(caught.value)


def test_dangling_personal_directory_is_fatal(source, tmp_path, monkeypatch):
    personal = tmp_path / "personal"
    personal.symlink_to(tmp_path / "absent", target_is_directory=True)
    monkeypatch.setattr("clink.registry.USER_CONFIG_DIR", personal)
    with pytest.raises(RegistryLoadError, match="personal"):
        ClinkRegistry()


def test_non_json_explicit_file_is_fatal(source, monkeypatch):
    other = source / "client.txt"
    other.write_text("{}")
    monkeypatch.setattr("clink.registry.get_env", lambda name: str(other))
    with pytest.raises(RegistryLoadError, match="client.txt"):
        ClinkRegistry()


@pytest.mark.parametrize("method", ["stat", "read_text"])
def test_unreadable_directory_entry_names_the_manifest(source, monkeypatch, method):
    entry = source / "unreadable.json"
    entry.write_text("{}")
    original = getattr(Path, method)

    def denied(path, *args, **kwargs):
        if path == entry:
            raise PermissionError("PRIVATE")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, denied)
    with pytest.raises(RegistryLoadError) as caught:
        ClinkRegistry()
    assert str(entry) in str(caught.value) and "PermissionError" in str(caught.value)
    assert "PRIVATE" not in str(caught.value)


def test_unlistable_source_is_fatal(source, monkeypatch):
    iterdir = Path.iterdir

    def list_directory(path):
        if path == source:
            raise PermissionError("PRIVATE")
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", list_directory)
    with pytest.raises(RegistryLoadError) as caught:
        ClinkRegistry()
    assert str(source) in str(caught.value) and "PermissionError" in str(caught.value)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    ("document", "location"),
    [
        ('{"secret":"PRIVATE",\n}', "line 2, column 1"),
        ('{"name":"claude","safe_args":{"bad":"PRIVATE"}}', "safe_args"),
        ({"env": {"TOKEN=PRIVATE": ""}}, "env.[key]"),
        ('{"name":"claude","env":{"TOKEN=PRIVATE":42}}', "env.TOKEN=[redacted]"),
        ({"roles": {"TOKEN=PRIVATE": {"role_args": 42}}}, "roles.TOKEN=[redacted].role_args"),
    ],
)
def test_bad_override_stops_startup_before_handshake(tmp_path, manifest, document, location):
    path = tmp_path / "override.json"
    path.write_text(json.dumps({**manifest, **document}) if isinstance(document, dict) else document)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    }
    proc = subprocess.run(
        [sys.executable, str(FIXTURES / "cli_only_server.py"), str(tmp_path)],
        input=json.dumps(init) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ},
            "PAL_SKIP_DOTENV": "true",
            "CLI_CLIENTS_CONFIG_PATH": str(path),
            "PAL_LOG_DIR": str(tmp_path / "logs"),
            "PAL_STATE_DIR": str(tmp_path / "state"),
        },
    )
    assert proc.returncode != 0 and not proc.stdout
    for sink in (proc.stderr, (tmp_path / "logs" / "mcp_server.log").read_text(encoding="utf-8")):
        assert str(path) in sink and location in sink
        assert "PRIVATE" not in sink and "input_value=" not in sink

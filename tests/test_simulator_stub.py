"""Strict stub wire failures, credential isolation and real stdio scenarios."""

import http.client
import json
import os
import threading
from urllib.parse import urlsplit

import pytest

from providers.configuration import provider_configurations
from simulator_tests import stub_provider as stub


def valid_payload():
    return {"model": stub.MODEL, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 256}


def request(server, payload, route="/v1/chat/completions", method="POST", **headers):
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        client.request(
            method,
            route,
            json.dumps(payload),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {stub.API_KEY}",
                **headers,
            },
        )
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "unexpected"},
        {"stream": True},
        {"stream": 0},
        {"stream": "false"},
        {"messages": []},
        {"messages": "hello"},
        {"messages": [None]},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "user", "content": []}]},
        {"messages": [{"role": "assistant", "content": "x"}]},
        {"messages": [{"role": "user", "content": "x", "extra": 1}]},
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_tokens": 4097},
        {"temperature": float("nan")},
        {"temperature": True},
        {"temperature": 3},
        {"unexpected": 1},
    ],
)
def test_schema_is_strict(changes):
    with pytest.raises(ValueError):
        stub.validate_request({**valid_payload(), **changes})


def test_wire_routes_models_schema_and_deterministic_response():
    with stub.running_stub() as server:
        assert server.server_address[0] == "127.0.0.1"
        status, first = request(server, valid_payload())
        assert status == 200
        assert request(server, valid_payload()) == (status, first)
        assert first["choices"][0]["message"]["content"] == "Deterministic simulator response."
        assert request(server, {**valid_payload(), "model": "wrong"})[0] == 400
        assert request(server, {**valid_payload(), "stream": True})[0] == 400
        assert request(server, valid_payload(), route="/v1/responses")[0] == 404
        assert request(server, valid_payload(), route="/v1/models?unexpected=1", method="GET")[0] == 404
        assert request(server, valid_payload(), Authorization="Bearer external-key")[0] == 401
        assert request(server, valid_payload(), **{"Content-Type": "text/plain"})[0] == 400
        assert request(server, valid_payload(), route="/v1/responses", method="PUT")[0] == 501
        assert request(server, {**valid_payload(), "temperature": 10**400})[0] == 400
        assert request(server, None, route="/v1/models", method="GET")[1]["data"][0]["id"] == stub.MODEL
        assert server.accepted == 2 and server.rejected == 8
    assert server.socket.fileno() == -1
    assert not any(thread.name == "pal-stub" for thread in threading.enumerate())


@pytest.mark.parametrize("body", [b"{", b"[]", b'{"model":"pal-stub","model":"pal-stub"}', b"\xff"])
def test_invalid_json_rejected(body):
    with stub.running_stub() as server:
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            client.request(
                "POST",
                "/v1/chat/completions",
                body,
                {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {stub.API_KEY}",
                },
            )
            response = client.getresponse()
            assert response.status == 400
            response.read()
        finally:
            client.close()


def test_isolation_uses_every_provider_declaration(tmp_path):
    declared = {name for config in provider_configurations().values() for name in config.environment_names()}
    inherited = dict.fromkeys(declared, "sentinel-external-value")
    inherited.update(PAL_MCP_FORCE_ENV_OVERRIDE="true", DISABLED_TOOLS="chat", SIMULATOR_MODEL="paid-model")
    env = stub.isolated_environment("http://127.0.0.1:12345/v1", tmp_path, inherited)
    custom = {"CUSTOM_API_URL", "CUSTOM_API_KEY", "CUSTOM_MODELS_CONFIG_PATH", "CUSTOM_ALLOWED_MODELS"}
    assert all(env[name] == "" for name in declared - custom)
    assert "sentinel-external-value" not in env.values()
    assert env["PAL_MCP_FORCE_ENV_OVERRIDE"] == "false"
    assert env["SIMULATOR_MODEL"] == stub.MODEL and env["DISABLED_TOOLS"] == ""
    assert inherited["PAL_MCP_FORCE_ENV_OVERRIDE"] == "true"


def test_isolation_skips_forced_dotenv_before_reading(monkeypatch, tmp_path):
    from utils import env as config

    forced = {
        "PAL_MCP_FORCE_ENV_OVERRIDE": "true",
        "OPENAI_API_KEY": "external-secret",
        "CUSTOM_API_URL": "https://external.invalid/v1",
    }
    monkeypatch.setattr(config, "_DOTENV_VALUES", forced)
    monkeypatch.setattr(config, "_FORCE_ENV_OVERRIDE", True)

    def forbidden(*args, **kwargs):
        pytest.fail("isolated reload must not read or load the dotenv file")

    monkeypatch.setattr(config, "_read_dotenv_values", forbidden)
    monkeypatch.setattr(config, "load_dotenv", forbidden)
    for name, value in stub.isolated_environment("http://127.0.0.1:12345/v1", tmp_path).items():
        monkeypatch.setenv(name, value)
    config.reload_env()
    assert config.get_env("OPENAI_API_KEY") == ""
    assert config.get_env("CUSTOM_API_URL") == "http://127.0.0.1:12345/v1"
    assert not config.env_override_enabled() and config.get_all_env() == {}


def test_full_ci_stdio_scenarios_reap_all_children(monkeypatch):
    import mcp.client.stdio as stdio

    processes = []
    original = stdio._create_platform_compatible_process

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(stdio, "_create_platform_compatible_process", capture)
    assert stub.run_ci() == 0
    assert len(processes) == 3
    assert all(process.returncode is not None for process in processes)
    assert not any(thread.name == "pal-stub" for thread in threading.enumerate())


def test_failed_selected_scenario_fails_ci_and_closes_stub(monkeypatch):
    from simulator_tests.test_provider_agnostic import SessionContinuationTest

    monkeypatch.setattr(SessionContinuationTest, "run_test", lambda self: False)
    assert stub.run_ci() == 1
    assert not any(thread.name == "pal-stub" for thread in threading.enumerate())


def test_rejected_probe_fails_ci_even_when_scenarios_pass(monkeypatch):
    from communication_simulator_test import CommunicationSimulator

    original = CommunicationSimulator.run_full_test_suite

    def probe_then_run(self):
        target = urlsplit(os.environ["CUSTOM_API_URL"])
        client = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
        try:
            client.request("PUT", "/v1/responses", b"")
            response = client.getresponse()
            assert response.status == 501
            response.read()
        finally:
            client.close()
        assert original(self)
        return True

    monkeypatch.setattr(CommunicationSimulator, "run_full_test_suite", probe_then_run)
    assert stub.run_ci() == 1

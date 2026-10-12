"""Strict loopback Chat Completions fixture and credential-free simulator entry point.

Run with ``python -m simulator_tests.stub_provider``. This tests wire/state
contracts only; it never evaluates the model reasoning expected by live scenarios.
"""

import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from unittest.mock import patch

from providers.configuration import provider_configurations

MODEL = "pal-stub"
API_KEY = "pal-stub-key"
MAX_BODY_BYTES = 1024 * 1024


def validate_request(payload):
    """Reject requests outside the one deliberately supported wire contract."""
    if not isinstance(payload, dict):
        raise ValueError("body must be an object")
    allowed = {"model", "messages", "stream", "temperature", "max_tokens"}
    if payload.keys() - allowed:
        raise ValueError("unsupported request fields")
    if payload.get("model") != MODEL:
        raise ValueError("unknown model")
    if "stream" in payload and payload["stream"] is not False:
        raise ValueError("streaming is unsupported")
    limit = payload.get("max_tokens")
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise ValueError("max_tokens must be an integer in 1..4096")
    if "temperature" in payload:
        temperature = payload["temperature"]
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
            raise ValueError("invalid temperature")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 100:
        raise ValueError("messages must be a nonempty bounded list")
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("invalid message fields")
        if message["role"] not in ("system", "user", "assistant"):
            raise ValueError("unsupported message role")
        if not isinstance(message["content"], str) or not message["content"].strip():
            raise ValueError("message content must be nonempty text")
    if messages[-1]["role"] != "user":
        raise ValueError("last message must be from the user")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class StubServer(ThreadingHTTPServer):
    daemon_threads = False  # server_close joins request workers; each socket has a deadline.

    def __init__(self):
        self.accepted = 0
        self.rejected = 0
        self._lock = Lock()
        super().__init__(("127.0.0.1", 0), StubHandler)

    def record(self, successful):
        with self._lock:
            if successful:
                self.accepted += 1
            else:
                self.rejected += 1

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server_port}/v1"


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log request headers or prompts.

    def respond(self, status, payload):
        data = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reject(self, status, message):
        self.server.record(False)
        self.respond(status, {"error": {"message": message, "type": "invalid_request_error"}})

    def send_error(self, code, message=None, explain=None):
        self.reject(code, "unsupported HTTP request")

    def do_GET(self):
        if self.path != "/v1/models":
            return self.reject(404, "unknown route")
        self.respond(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "pal-stub"}]})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self.reject(404, "unknown route")
        if self.headers.get("Authorization") != f"Bearer {API_KEY}":
            return self.reject(401, "only the isolated stub credential is accepted")
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            return self.reject(400, "expected length-delimited JSON")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("invalid request length")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            payload = json.loads(raw, object_pairs_hook=_unique_object)
            validate_request(payload)
        except (ValueError, UnicodeError, RecursionError):
            return self.reject(400, "invalid request schema or JSON")
        self.server.record(True)
        self.respond(
            200,
            {
                "id": "pal-stub-response",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Deterministic simulator response."},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            },
        )


@contextmanager
def running_stub():
    with StubServer() as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, name="pal-stub")
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=6)
            if thread.is_alive():
                raise RuntimeError("stub server did not stop")


def isolated_environment(base_url: str, state: Path, inherited=None):
    env = dict(os.environ if inherited is None else inherited)
    # Empty values, rather than removal, also prevent loading credentials from dotenv.
    for declaration in provider_configurations().values():
        for name in declaration.environment_names():
            env[name] = ""
    repo = Path(__file__).resolve().parents[1]
    env.update(
        CUSTOM_API_URL=base_url,
        CUSTOM_API_KEY=API_KEY,
        CUSTOM_MODELS_CONFIG_PATH=str(repo / "conf" / "stub_models.json"),
        CUSTOM_ALLOWED_MODELS=MODEL,
        SIMULATOR_MODEL=MODEL,
        DEFAULT_MODEL=MODEL,
        PAL_MCP_FORCE_ENV_OVERRIDE="false",
        PAL_SKIP_DOTENV="true",
        PAL_STATE_DIR=str(state),
        PAL_LOG_DIR=str(state / "logs"),
        PAL_WORKSPACE_ROOT=str(repo),
        PAL_TRANSCRIPTS="false",
        DISABLED_TOOLS="",
        DISABLED_MODELS="",
        PREFERRED_MODELS="",
        MAX_CONVERSATION_TURNS="50",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    return env


def run_ci() -> int:
    from communication_simulator_test import CommunicationSimulator, run_test_suite

    with TemporaryDirectory(prefix="pal-stub-") as state, running_stub() as server:
        with patch.dict(os.environ, isolated_environment(server.base_url, Path(state)), clear=True):
            result = run_test_suite(CommunicationSimulator(ci_mode=True))
        return result if result else int(server.accepted == 0 or server.rejected != 0)


if __name__ == "__main__":
    raise SystemExit(run_ci())

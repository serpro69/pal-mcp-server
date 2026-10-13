"""Fake executable that records real runner input and emits vendor wire formats."""

import json
import os
import re
import socket
import sys
from pathlib import Path


def main() -> None:
    vendor, capture_dir, gate_port = sys.argv[1:4]
    prompt = sys.stdin.read()
    stage = re.findall(r"CLINK_TEST_STAGE=(\w+)", prompt)[-1]
    capture = Path(capture_dir) / f"{stage}.json"
    # Refuse duplicate launches, including any accidental retry of inference.
    with capture.open("x", encoding="utf-8") as stream:
        json.dump({"prompt": prompt, "argv": sys.argv[4:], "environment_names": sorted(os.environ)}, stream)

    first_user = f"FIRST_USER_MARKER_{vendor}"
    first_reply = f"FIRST_ASSISTANT_MARKER_{vendor}"
    if stage == "first":
        assert first_user in prompt
        reply = first_reply
    else:
        # A follow-up cannot succeed just by returning another continuation ID.
        # Its answer depends on receiving both sides of the prior exchange.
        assert prompt.count(first_user) == 1
        assert prompt.count(first_reply) == 1
        assert prompt.index(first_user) < prompt.index(first_reply)
        reply = f"CONTEXT_OK_{vendor}_{stage}"

    if stage == "held":
        with socket.create_connection(("127.0.0.1", int(gate_port)), timeout=15) as gate:
            gate.sendall(b"R")
            assert gate.recv(1) == b"G", "Test must release the held CLI"

    if vendor == "gemini":
        print(json.dumps({"response": reply}))
    elif vendor == "claude":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply}))
    elif vendor == "codex":
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}))
        print(json.dumps({"type": "turn.completed"}))
    else:
        raise AssertionError(f"Unknown fake vendor: {vendor}")


if __name__ == "__main__":
    main()

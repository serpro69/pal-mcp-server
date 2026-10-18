"""Fake executable that records real runner input and emits vendor wire formats."""

import json
import os
import re
import socket
import sys
from pathlib import Path


def main() -> None:
    vendor, capture_dir, gate_port = sys.argv[1:4]
    raw_input = sys.stdin.read()
    if vendor == "agy":
        event = json.loads(raw_input)
        assert event["event"] == "user"
        prompt = event["message"]["content"]
        assert isinstance(prompt, str)
    else:
        prompt = raw_input
    scenario = os.environ.get("CLINK_TEST_SCENARIO", "history")
    assert scenario in {"history", "directory", "held-directory", "failure"}, "Unknown fixture scenario"
    stage = re.findall(r"CLINK_TEST_STAGE=(\w+)", prompt)[-1]
    capture = Path(capture_dir) / f"{stage}.json"
    observation = {
        "prompt": prompt,
        "stdin": raw_input,
        "argv": sys.argv[4:],
        "environment_names": sorted(os.environ),
        "cwd": os.getcwd(),
        "pid": os.getpid(),
    }
    marker_name = os.environ.get("CLINK_TEST_MARKER")
    if marker_name is not None:
        marker = Path(marker_name)
        assert not marker.is_absolute() and ".." not in marker.parts, "Marker must be a relative project file"
        observation["marker"] = marker.read_text(encoding="utf-8") if marker.exists() else None
    # Refuse duplicate launches, including any accidental retry of inference.
    with capture.open("x", encoding="utf-8") as stream:
        json.dump(observation, stream)

    first_user = f"FIRST_USER_MARKER_{vendor}"
    first_reply = f"FIRST_ASSISTANT_MARKER_{vendor}"
    if scenario == "failure":
        print("Requested fixture failure", file=sys.stderr)
        sys.exit(7)
    if scenario != "history":
        reply = json.dumps({"cwd": observation["cwd"], "marker": observation.get("marker")})
    elif stage == "first":
        assert first_user in prompt
        reply = first_reply
    else:
        # A follow-up cannot succeed just by returning another continuation ID.
        # Its answer depends on receiving both sides of the prior exchange.
        assert prompt.count(first_user) == 1
        assert prompt.count(first_reply) == 1
        assert prompt.index(first_user) < prompt.index(first_reply)
        reply = f"CONTEXT_OK_{vendor}_{stage}"

    if stage == "held" or scenario == "held-directory":
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
    elif vendor == "agy":
        print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": reply}}))
    else:
        raise AssertionError(f"Unknown fake vendor: {vendor}")


if __name__ == "__main__":
    main()

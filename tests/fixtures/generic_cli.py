"""Local fixture for generic clink prompt delivery and output parsing."""

import json
import os
import socket
import sys
import time
from pathlib import Path


def main():
    mode = os.environ.get("FIXTURE_MODE", "reply")
    if "FIXTURE_MODE_FILE" in os.environ:
        mode = Path(os.environ["FIXTURE_MODE_FILE"]).read_text().strip()
    if mode in ("limits", "stdout", "stderr"):
        size = int(os.environ["FIXTURE_LIMIT"])
        if mode == "stdout":
            # Complete stderr must still be discarded if stdout subsequently overflows.
            print("PRIVATE_OVERFLOW_DETAIL", file=sys.stderr, flush=True)
            os.close(sys.stderr.fileno())
        # Deliberately fill the pipes before reading stdin.
        sys.stdout.buffer.write(b"x" * (size + (mode == "stdout")))
        sys.stdout.buffer.flush()
        if mode != "stdout":
            sys.stderr.buffer.write(b"y" * (size + (mode == "stderr")))
            sys.stderr.buffer.flush()
        if mode in ("stdout", "stderr"):
            time.sleep(60)  # Excess must fail while the stream is still open.
    stdin = sys.stdin.buffer.read().decode("utf-8")
    args = sys.argv[1:]
    flag = os.environ.get("FIXTURE_PROMPT_FLAG")
    if flag and (len(args) < 2 or args[-2] != flag):
        sys.exit("Missing fixture prompt flag")
    prompt = args[-1] if flag else stdin
    capture = {
        "prompt": prompt,
        "stdin": stdin,
        "argv": args,
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "configured_token": os.environ.get("TEST_CLI_TOKEN"),
        "vendor_key_present": "GEMINI_API_KEY" in os.environ,
    }
    Path(os.environ["FIXTURE_CAPTURE"]).write_text(json.dumps(capture), encoding="utf-8")
    if mode in ("hold", "hold-closed-stderr"):
        print("HELD_DIAGNOSTIC", file=sys.stderr, flush=True)
        if mode == "hold-closed-stderr":
            os.close(sys.stderr.fileno())
        with socket.create_connection(("127.0.0.1", int(os.environ["FIXTURE_PORT"]))) as gate:
            gate.sendall(b"R")
            gate.recv(1)
    elif mode == "failure":
        print("PLAUSIBLE_PRIVATE_ANSWER")
        details = f"{prompt} {os.environ.get('TEST_CLI_TOKEN', '')} {os.environ.get('HTTPS_PROXY', '')}"
        sys.stderr.buffer.write(f"{details}\nNATIVE_FAILURE_DETAIL\n".encode())
        sys.exit(7)
    elif mode == "bad-utf8":
        print("NATIVE_ENCODING_DETAIL", file=sys.stderr)
        sys.stdout.buffer.write(b"\xff")
    elif mode == "blank":
        print("NATIVE_PARSE_DETAIL", file=sys.stderr)
        print(" ")
    elif mode == "json":
        reply = {"reply": {"text": "  Fixture answer ✓  \n"}}
        sys.stdout.buffer.write(os.environ.get("FIXTURE_JSON", json.dumps(reply)).encode("utf-8"))
    elif mode == "reply":
        print("STDERR_PRIVATE", file=sys.stderr)
        sys.stdout.buffer.write("  Fixture answer ✓  \n".encode())


if __name__ == "__main__":
    main()

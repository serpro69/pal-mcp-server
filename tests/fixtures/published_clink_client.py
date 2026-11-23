#!/usr/bin/env python3
"""Named stand-ins for the two operator-guide CLIs on the isolated test PATH."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    stdin = sys.stdin.buffer.read()
    if Path(sys.argv[0]).name == "team-ask":
        assert sys.argv[1:] == []
        assert "Read the sample." in stdin.decode("utf-8")
        sys.stdout.write("Sample received.")
        return

    assert stdin == b""
    mode, flag, prompt = sys.argv[1:]
    assert (mode, flag) == ("--read-only", "--question")
    git_env = {"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, env=git_env).strip()
    assert Path(root).samefile(Path.cwd())
    assert "Review ALPHA." in prompt
    if "Recall the review." in prompt:
        assert "Ready" in prompt
        reply = "ALPHA was Ready."
    else:
        reply = Path("decision.txt").read_text(encoding="utf-8")
    print(json.dumps({"reply": {"text": reply}}))


if __name__ == "__main__":
    main()

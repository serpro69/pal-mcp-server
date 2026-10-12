"""Check captured request contracts before measuring serialized UTF-8 prefixes."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def serialized(request: dict[str, Any]) -> bytes:
    """Canonical JSON is a measurement representation, not a wire-format change."""
    return json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def common_prefix_bytes(previous: bytes, current: bytes) -> int:
    for index, (left, right) in enumerate(zip(previous, current)):
        if left != right:
            return index
    return min(len(previous), len(current))


def request_text(request: dict[str, Any]) -> str:
    """Read textual input from actual Chat Completions/Responses wire payloads."""
    if not isinstance(request, dict):
        raise ValueError("Captured request must be an object")
    messages = request.get("messages", request.get("input", []))
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise ValueError("Captured input must be a message list")
    values = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            values.append(content)
        elif isinstance(content, list):
            if not all(isinstance(part, dict) for part in content):
                raise ValueError("Captured content parts must be objects")
            values.extend(part["text"] for part in content if isinstance(part.get("text"), str))
    if isinstance(request.get("instructions"), str):
        values.insert(0, request["instructions"])
    return "\n".join(values)


def prefix_report(captures: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(captures, list) or not all(isinstance(c, dict) for c in captures):
        raise ValueError("Captures must be a list of objects")
    previous = {}
    rows = []
    for capture in captures:
        request = capture["request"]
        text = request_text(request)
        contract = capture["contract"]
        if not isinstance(contract, dict) or not any(contract.get(field) for field in ("required_once", "required")):
            raise ValueError("A capture requires an explicit nonempty completeness contract")
        for field in ("required_once", "required", "forbidden"):
            values = contract.get(field, [])
            if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
                raise ValueError("Completeness sentinels must be nonempty strings")
        failures = []
        if capture.get("instructions_preserved") is False:
            failures.append({"kind": "missing_system_instructions"})
        for value in contract.get("required_once", []):
            count = text.count(value)
            if count != 1:
                failures.append({"kind": "required_once", "sentinel": value, "count": count})
        for value in contract.get("required", []):
            if value not in text:
                failures.append({"kind": "missing", "sentinel": value})
        for value in contract.get("forbidden", []):
            if value in text:
                failures.append({"kind": "forbidden", "sentinel": value})
        wire = serialized(request)
        key = (capture["scenario"], capture["provider"], capture["model"])
        old = previous.get(key)
        row = {
            "scenario": capture["scenario"],
            "provider": capture["provider"],
            "model": capture["model"],
            "request_bytes": len(wire),
            "complete": not failures,
            "failures": failures,
            "lcp_bytes": common_prefix_bytes(old, wire) if old is not None else None,
            "previous_request_bytes": len(old) if old is not None else None,
        }
        rows.append(row)
        previous[key] = wire
    complete = bool(rows) and all(row["complete"] for row in rows)
    # Failed completeness is never presented as a successful prefix optimization.
    if not complete:
        for row in rows:
            row["lcp_bytes"] = None
    return {"schema_version": 1, "complete": complete, "requests": rows}


def compare_runs(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare identical fixture scenarios across processes, only after completeness."""
    if not prefix_report(left)["complete"] or not prefix_report(right)["complete"]:
        return {"complete": False, "comparisons": []}

    def identity(c):
        return c["scenario"], c["provider"], c["model"]

    if [identity(c) for c in left] != [identity(c) for c in right]:
        raise ValueError("Cross-run scenarios/models must match in order")
    comparisons = []
    for index, (before, after) in enumerate(zip(left, right)):
        a, b = serialized(before["request"]), serialized(after["request"])
        comparisons.append(
            {
                "request": index + 1,
                "scenario": before["scenario"],
                "identical": a == b,
                "lcp_bytes": common_prefix_bytes(a, b),
                "before_sha256": hashlib.sha256(a).hexdigest(),
                "after_sha256": hashlib.sha256(b).hexdigest(),
            }
        )
    return {"complete": True, "comparisons": comparisons}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, help="Synthetic capture JSON from python -m tests.cache_capture")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path, help="A second run of the same synthetic scenarios")
    args = parser.parse_args()
    try:
        captures = json.loads(args.captures.read_text(encoding="utf-8"))["captures"]
        report = prefix_report(captures)
        if args.compare:
            other = json.loads(args.compare.read_text(encoding="utf-8"))["captures"]
            report["cross_run"] = compare_runs(captures, other)
            report["complete"] = report["complete"] and report["cross_run"]["complete"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Prefix report failed: {exc}\n")
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if not report["complete"]:
        parser.exit(1, "Request completeness failed; prefix scores withheld.\n")


if __name__ == "__main__":
    main()

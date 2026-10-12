"""Summarize per-call usage from explicit JSONL transcript paths; no pricing claims."""

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from utils.usage import USAGE_FIELDS, known_usage

MAX_RECORD_BYTES = 4 * 1024 * 1024


def read_events(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with path.open("rb") as stream:
            while line := stream.readline(MAX_RECORD_BYTES + 1):
                if len(line) > MAX_RECORD_BYTES:
                    raise ValueError(f"Transcript record exceeds {MAX_RECORD_BYTES} bytes in {path}")
                try:
                    event = json.loads(line)
                except (ValueError, RecursionError):
                    yield {"_invalid_record": True}
                    continue
                yield event if isinstance(event, dict) else {"_invalid_record": True}


def usage_report(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    groups = defaultdict(list)
    seen = set()
    diagnostics = {"invalid_records": 0, "duplicate_turns": 0, "legacy_assistant_turns": 0, "invalid_calls": 0}
    for event in events:
        if not isinstance(event, dict) or event.get("_invalid_record"):
            diagnostics["invalid_records"] += 1
            continue
        if event.get("event") != "turn" or event.get("role") != "assistant":
            continue
        thread, turn = event.get("thread_id"), event.get("turn")
        if event.get("schema_version") != 1 or not isinstance(thread, str) or type(turn) is not int or turn < 1:
            diagnostics["invalid_records"] += 1
            continue
        identity = (thread, turn)
        if identity in seen:
            diagnostics["duplicate_turns"] += 1
            continue
        seen.add(identity)
        metadata = event.get("model_metadata")
        if not isinstance(metadata, dict) or "provider_calls" not in metadata:
            # Legacy usage and workflow aggregates do not establish per-call coverage.
            diagnostics["legacy_assistant_turns"] += 1
            continue
        calls = metadata["provider_calls"]
        if not isinstance(calls, list):
            diagnostics["invalid_records"] += 1
            continue
        for index, call in enumerate(calls, 1):
            if (
                not isinstance(call, dict)
                or type(call.get("call_index")) is not int
                or call.get("call_index") != index
                or not isinstance(call.get("provider"), str)
                or not call["provider"]
                or not isinstance(call.get("model"), str)
                or not call["model"]
            ):
                diagnostics["invalid_calls"] += 1
                continue
            groups[(call["provider"], call["model"])].append(known_usage(call.get("usage")))
    rows = []
    for (provider, model), calls in sorted(groups.items()):
        fields = {}
        for field in USAGE_FIELDS:
            values = [usage[field] for usage in calls if field in usage]
            fields[field] = {
                "known_calls": len(values),
                "missing_calls": len(calls) - len(values),
                "sum": sum(values) if values else None,
            }
        paired = [
            u
            for u in calls
            if "input_tokens" in u and "cached_input_tokens" in u and u["cached_input_tokens"] <= u["input_tokens"]
        ]
        denominator = sum(u["input_tokens"] for u in paired)
        rows.append(
            {
                "provider": provider,
                "model": model,
                "calls": len(calls),
                "fields": fields,
                "cache_read_ratio": (
                    sum(u["cached_input_tokens"] for u in paired) / denominator if denominator else None
                ),
                "cache_ratio_known_calls": len(paired),
                "inconsistent_cache_counts": sum(
                    1
                    for u in calls
                    if "input_tokens" in u
                    and "cached_input_tokens" in u
                    and u["cached_input_tokens"] > u["input_tokens"]
                ),
            }
        )
    return {"schema_version": 1, "groups": rows, "diagnostics": diagnostics}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcripts", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = usage_report(read_events(args.transcripts))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cache report failed: {exc}\n")
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()

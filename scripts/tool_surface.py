"""Capture tool contracts and description costs: python -m scripts.tool_surface.

Measures all exported capabilities (including disabled tools) under the current
model/CLI configuration. Does not start the server, call providers or write logs.
"""

import argparse
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _description_bytes(value: Any) -> int:
    if isinstance(value, dict):
        return sum(
            _json_bytes(item) if key == "description" and isinstance(item, str) else _description_bytes(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_description_bytes(item) for item in value)
    return 0


def semantic_schema(value: Any) -> Any:
    """Retain every schema constraint/default while separating description prose."""
    if isinstance(value, dict):
        return {key: semantic_schema(item) for key, item in value.items() if key != "description"}
    if isinstance(value, list):
        return [semantic_schema(item) for item in value]
    return value


def capture_surface(instances: Iterable[Any] | None = None) -> dict[str, Any]:
    if instances is None:
        import tools

        instances = (getattr(tools, name)() for name in tools.__all__)
    entries = {}
    for tool in instances:
        name = tool.get_name()
        if name in entries:
            raise ValueError(f"Duplicate capability name: {name}")
        schema = tool.get_input_schema()
        description = tool.get_description()
        entries[name] = {
            "schema": semantic_schema(schema),
            "description_bytes": _json_bytes(description) + _description_bytes(schema),
            "serialized_bytes": _json_bytes({"name": name, "description": description, "inputSchema": schema}),
            "annotations": tool.get_annotations(),
        }
        if name == "clink":
            # Local edit opt-in, path restrictions and policy wording are an
            # explicit compatibility boundary, including their descriptions.
            entries[name]["safety_schema"] = schema
            entries[name]["description"] = description

    prompt_dir = Path(__file__).resolve().parents[1] / "systemprompts"
    prompts = {
        str(path.relative_to(prompt_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(prompt_dir.rglob("*"))
        if path.suffix in (".py", ".txt")
    }
    return {
        "snapshot_version": 1,
        "tools": dict(sorted(entries.items())),
        "totals": {
            "tools": len(entries),
            "description_bytes": sum(entry["description_bytes"] for entry in entries.values()),
            "serialized_bytes": sum(entry["serialized_bytes"] for entry in entries.values()),
        },
        "systemprompts_sha256": prompts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON snapshot to this path; defaults to stdout")
    args = parser.parse_args()
    output = json.dumps(capture_surface(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()

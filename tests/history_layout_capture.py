"""Task 41 test-only layout experiments; run python -m tests.history_layout_capture.

All requests use the real dispatcher/SDK and a fake HTTP transport. Candidates
are scoped patches, never runtime options. No file snapshots persist in memory.
"""

import argparse
import asyncio
import json
import os
import re
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from scripts.cache_prefix import common_prefix_bytes, prefix_report, request_text, serialized
from simulator_tests.stub_provider import isolated_environment
from tests.test_conversation_storage_errors import FaultStorage

SCENARIOS = ("fixed", "added", "touched", "edited", "compacted")
LAYOUTS = ("current", "first_seen", "trailing_mtime", "hysteresis")
CALLS = 14
HISTORY_LIMIT = 1000


def first_seen_files(context):
    return list(dict.fromkeys(path for turn in context.turns for path in turn.files or []))


def install_candidate(stack, layout, memory, file_utils):
    """Apply one experimental policy, preserving the runtime implementation."""
    if layout == "first_seen":
        stack.enter_context(patch.object(memory, "get_conversation_file_list", first_seen_files))
    elif layout == "trailing_mtime":
        original = file_utils.read_file_content

        def trailing_mtime(*args, **kwargs):
            content, tokens = original(*args, **kwargs)
            header = re.search(r"^--- BEGIN FILE: (.+) \(Last modified: ([^)]+)\) ---$", content, re.MULTILINE)
            if header:
                content = content.replace(header[0], f"--- BEGIN FILE: {header[1]} ---", 1)
                content = content.replace(
                    f"\n--- END FILE: {header[1]} ---",
                    f"\nFile provenance: {header[1]} (Last modified: {header[2]})\n--- END FILE: {header[1]} ---",
                    1,
                )
                tokens = file_utils.estimate_tokens(content)
            return content, tokens

        stack.enter_context(patch.object(file_utils, "read_file_content", trailing_mtime))
    elif layout == "hysteresis":
        original = memory.build_conversation_history
        cutoffs = {}

        def compact(context, model_context=None, *args, **kwargs):
            history, tokens = original(context, model_context, *args, **kwargs)
            starts = list(re.finditer(r"\n--- Turn (\d+) \(", history))
            if not starts or int(starts[0][1]) == 1:
                return history, tokens
            cutoff = cutoffs.get(context.thread_id, 1)
            if int(starts[0][1]) > cutoff:
                # Drop one quarter of currently available turns at each overflow.
                cutoff = int(starts[len(starts) // 4][1])
                cutoffs[context.thread_id] = cutoff
            kept = [match for match in starts if int(match[1]) >= cutoff]
            history = history[: starts[0].start()] + history[kept[0].start() :]
            history = re.sub(r"Showing \d+ most recent turns", f"Showing {len(kept)} most recent turns", history)
            return history, model_context.estimate_tokens(history)

        stack.enter_context(patch.object(memory, "build_conversation_history", compact))


async def capture_layout(root: Path, scenario: str, layout: str) -> dict:
    if scenario not in SCENARIOS or layout not in LAYOUTS:
        raise ValueError("Unknown experiment scenario/layout")
    from utils import env as env_config

    env = isolated_environment("", root)
    env.update(
        OPENAI_API_KEY="synthetic-layout-key", DEFAULT_MODEL="gpt-4.1", LOG_LEVEL="ERROR", PAL_WORKSPACE_ROOT=str(root)
    )
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, env, clear=True))
        stack.enter_context(patch.object(env_config, "_FORCE_ENV_OVERRIDE", False))
        import server
        from providers.openai import OpenAIModelProvider
        from providers.registry import ModelProviderRegistry
        from providers.shared import ProviderType
        from utils import conversation_memory as memory
        from utils import file_utils, model_restrictions
        from utils.model_context import ModelContext

        provider = OpenAIModelProvider("synthetic-layout-key")
        registry = ModelProviderRegistry()
        stack.enter_context(patch.object(registry, "_providers", {ProviderType.OPENAI: OpenAIModelProvider}))
        stack.enter_context(patch.object(registry, "_initialized_providers", {ProviderType.OPENAI: provider}))
        stack.enter_context(patch.object(model_restrictions, "_restriction_service", None))
        stack.enter_context(patch.object(memory, "get_storage", return_value=FaultStorage()))
        stack.enter_context(patch.object(memory, "MAX_CONVERSATION_TURNS", 100))
        if scenario == "compacted":
            allocation = ModelContext.calculate_token_allocation

            def constrained(context, *args, **kwargs):
                return replace(allocation(context, *args, **kwargs), history_tokens=HISTORY_LIMIT)

            stack.enter_context(patch.object(ModelContext, "calculate_token_allocation", constrained))
        install_candidate(stack, layout, memory, file_utils)
        captures, coverage, expected, paths = [], [], [], []

        def respond(request):
            if not request.url.path.endswith("/chat/completions"):
                raise AssertionError(f"Unexpected synthetic route: {request.url.path}")
            captures.append(
                {"scenario": scenario, "provider": "openai", "model": "gpt-4.1", "request": json.loads(request.content)}
            )
            return httpx.Response(
                200,
                json={
                    "id": "fixture",
                    "model": "gpt-4.1",
                    "created": 0,
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": f"[[REPLY_{len(captures):02d}]]"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        provider._test_transport = httpx.MockTransport(respond)
        thread_id = None
        try:
            for index in range(CALLS):
                if index == 0 or (scenario == "added" and index % 3 == 0):
                    path = root / f"file-{len(paths)}.py"
                    path.write_text(
                        f"VALUE = '[[FILE_{len(paths)}_V0]]'\n" + "# stable context\n" * 24, encoding="utf-8"
                    )
                    os.utime(path, (1_700_000_000, 1_700_000_000))
                    paths.append(path)
                if scenario in ("touched", "edited") and index:
                    if scenario == "edited":
                        paths[0].write_text(
                            f"VALUE = '[[FILE_0_V{index}]]'\n" + "# stable context\n" * 24, encoding="utf-8"
                        )
                    os.utime(paths[0], (1_700_000_000 + index, 1_700_000_000 + index))
                prompt = f"[[INPUT_{index:02d}]] " + "Retain this reasoning context. " * 16
                result = json.loads(
                    (
                        await server.handle_call_tool(
                            "chat",
                            {
                                "prompt": prompt,
                                "model": "gpt-4.1",
                                "absolute_file_paths": [str(paths[-1])],
                                "working_directory_absolute_path": str(root),
                                **({"continuation_id": thread_id} if thread_id else {}),
                            },
                        )
                    )[0].text
                )
                if result.get("status") == "error":
                    raise AssertionError(result)
                thread_id = result["continuation_offer"]["continuation_id"]
                capture = captures[-1]
                text = request_text(capture["request"])
                visible = [value for value in expected if value in text]
                omitted = [value for value in expected if value not in text]
                if scenario != "compacted" and omitted:
                    raise AssertionError("Unconstrained history lost content")
                if visible != expected[len(omitted) :]:
                    raise AssertionError("Compaction must retain a contiguous newest suffix")
                if expected and visible[-2:] != expected[-2:]:
                    raise AssertionError("Compaction dropped the latest complete exchange")
                if omitted:
                    marker = re.search(r"Showing (\d+) most recent turns out of (\d+) total", text)
                    # The admitted current input has a blank placeholder inside history.
                    if marker is None or (int(marker[1]), int(marker[2])) != (len(visible) + 1, len(expected) + 1):
                        raise AssertionError("Missing or untruthful history omission marker")
                file_sentinels = [f"[[FILE_{i}_V{index if scenario == 'edited' else 0}]]" for i in range(len(paths))]
                forbidden = [f"[[FILE_0_V{i}]]" for i in range(index)] if scenario == "edited" else []
                capture["contract"] = {"required_once": [prompt, *visible, *file_sentinels], "forbidden": forbidden}
                capture["instructions_preserved"] = server.TOOLS["chat"].get_system_prompt().strip() in text
                if "Last modified:" not in text:
                    raise AssertionError(f"File provenance was lost: {scenario}/{layout}/{index}")
                history_tokens = None
                if "=== CONVERSATION HISTORY" in text:
                    history = text[text.index("=== CONVERSATION HISTORY") : text.index("=== NEW USER INPUT ===")]
                    history_tokens = ModelContext("gpt-4.1").estimate_tokens(history)
                    if scenario == "compacted" and history_tokens > HISTORY_LIMIT + 400:
                        raise AssertionError("History exceeded selection allowance plus fixed envelope overhead")
                coverage.append(
                    {
                        "call": index + 1,
                        "visible_prior_turns": len(visible),
                        "omitted_prior_turns": len(omitted),
                        "history_tokens": history_tokens,
                    }
                )
                expected.extend([prompt, f"[[REPLY_{index + 1:02d}]]"])
            if [turn.content for turn in memory.get_thread(thread_id).turns] != expected:
                raise AssertionError("Stored audit history is incomplete")
            # Normalize only synthetic paths/UUIDs; preserve contents and timestamps.
            for capture in captures:
                wire = (
                    json.dumps(capture["request"])
                    .replace(str(root), "<fixture-root>")
                    .replace(thread_id, "fixture-thread")
                )
                capture["request"] = json.loads(wire)
            report = prefix_report(captures)
            if not report["complete"]:
                raise AssertionError(report)
            return {
                "scenario": scenario,
                "layout": layout,
                "captures": captures,
                "coverage": coverage,
                "prefix_report": report,
                "audit_turns": len(expected),
            }
        finally:
            await provider.aclose()
            for path in paths:
                path.unlink()


async def measure(root: Path) -> dict:
    results = []
    # Use identical physical paths, not just normalized output: path length affects budgets.
    directory = root / "fixture"
    directory.mkdir()
    for scenario in SCENARIOS:
        for layout in LAYOUTS:
            results.append(await capture_layout(directory, scenario, layout))
    return {
        "schema_version": 1,
        "scope": "Test-only layouts, actual dispatcher/SDK, synthetic HTTP; no savings/quality claim",
        "results": results,
    }


def summarize(report: dict) -> dict:
    rows = []
    for result in report["results"]:
        requests = [serialized(capture["request"]) for capture in result["captures"]]
        lcps = [common_prefix_bytes(a, b) for a, b in zip(requests, requests[1:])]
        # Prefix rewrites before the previous history's first turn, not append growth.
        early = 0
        for before, lcp in zip(requests, lcps):
            marker = before.find(b"--- Turn 1 (")
            early += int(marker >= 0 and lcp < marker)
        omitted = [call["omitted_prior_turns"] for call in result["coverage"]]
        compactions = sum(left != right for left, right in zip(omitted, omitted[1:]))
        rows.append(
            {
                "scenario": result["scenario"],
                "layout": result["layout"],
                "complete": result["prefix_report"]["complete"],
                "audit_turns": result["audit_turns"],
                "lcp_bytes": lcps,
                "early_rewrites": early,
                "compaction_events": compactions,
                "coverage": result["coverage"],
            }
        )
    return {"schema_version": 1, "scope": report["scope"], "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Full synthetic captures")
    parser.add_argument("--summary", type=Path, help="Compact retained measurements")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="pal-layout-") as directory:
        report = asyncio.run(measure(Path(directory)))
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.summary:
        args.summary.write_text(json.dumps(summarize(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"runs": len(report["results"]), "requests": CALLS * len(report["results"]), "complete": True}))


if __name__ == "__main__":
    main()

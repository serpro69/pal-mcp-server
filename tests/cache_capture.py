"""Reproduce synthetic dispatcher-to-SDK captures: python -m tests.cache_capture.

No external calls: the actual OpenAI serializer runs against an HTTPX transport.
Only fixture paths/UUIDs are normalized in saved evidence, never production prompts.
"""

import argparse
import asyncio
import json
import os
import re
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from scripts.cache_prefix import prefix_report, request_text
from scripts.cache_report import read_events, usage_report


async def capture_scenarios(root: Path) -> dict:
    from providers.configuration import provider_configurations
    from utils import env as env_config

    env = {name: "" for declaration in provider_configurations().values() for name in declaration.environment_names()}
    env.update(
        PAL_SKIP_DOTENV="true",
        OPENAI_API_KEY="synthetic-capture-key",
        PAL_TRANSCRIPTS="true",
        PAL_STATE_DIR=str(root / "state"),
        PAL_LOG_DIR=str(root / "logs"),
        PAL_WORKSPACE_ROOT=str(root),
        DISABLED_MODELS="",
        PREFERRED_MODELS="",
        DISABLED_TOOLS="",
        LOG_LEVEL="ERROR",
        JULES_API_KEY="",
    )
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, env))
        stack.enter_context(patch.object(env_config, "_FORCE_ENV_OVERRIDE", False))
        import server
        from providers.openai import OpenAIModelProvider
        from providers.registry import ModelProviderRegistry
        from providers.shared import ProviderType
        from tests.test_conversation_storage_errors import FaultStorage
        from tools.thinkdeep import ThinkDeepTool
        from utils import conversation_memory as memory
        from utils import model_restrictions

        provider = OpenAIModelProvider("synthetic-capture-key")
        registry = ModelProviderRegistry()
        stack.enter_context(patch.object(registry, "_providers", {ProviderType.OPENAI: OpenAIModelProvider}))
        stack.enter_context(patch.object(registry, "_initialized_providers", {ProviderType.OPENAI: provider}))
        stack.enter_context(patch.object(model_restrictions, "_restriction_service", None))
        stack.enter_context(patch.object(memory, "get_storage", return_value=FaultStorage()))
        stack.enter_context(patch.object(memory, "MAX_CONVERSATION_TURNS", 100))
        captures, audits = [], []
        active = {}
        thread_names = {}

        def normalize(value):
            if isinstance(value, str):
                value = value.replace(str(root), "<fixture-root>")
                return re.sub(
                    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                    lambda m: thread_names.setdefault(m[0], f"thread-{len(thread_names) + 1}"),
                    value,
                )
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        def respond(request):
            payload = json.loads(request.content)
            capture = {**active, "provider": "openai", "model": payload["model"], "request": normalize(payload)}
            captures.append(capture)
            count = len(captures)
            answer = f"[[ANSWER_{count:02d}]]"
            details = {"cached_tokens": 0 if count % 2 else 800}
            if count % 3:
                details["cache_write_tokens"] = 50
            if request.url.path.endswith("/responses"):
                body = {
                    "id": "synthetic-response",
                    "object": "response",
                    "model": payload["model"],
                    "created_at": 0,
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 10,
                        "total_tokens": 1010,
                        "input_tokens_details": details,
                    },
                    "output": [
                        {
                            "type": "message",
                            "id": "m1",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": answer, "annotations": []}],
                        }
                    ],
                }
            elif request.url.path.endswith("/chat/completions"):
                body = {
                    "id": "synthetic-response",
                    "model": payload["model"],
                    "created": 0,
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 10,
                        "total_tokens": 1010,
                        "prompt_tokens_details": details,
                    },
                    "choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                }
            else:
                raise AssertionError(f"Unexpected capture route: {request.url.path}")
            return httpx.Response(200, json=body)

        provider._test_transport = httpx.MockTransport(respond)

        async def invoke(name, args, scenario, required_once, *, required=(), forbidden=(), stance="neutral"):
            active.clear()
            active.update(
                scenario=scenario,
                contract={
                    "required_once": list(required_once),
                    "required": list(required),
                    "forbidden": list(forbidden),
                },
            )
            start = len(captures)
            result = json.loads((await server.handle_call_tool(name, args))[0].text)
            if result.get("status") in ("error", "consensus_failed"):
                raise AssertionError(f"Synthetic {scenario} dispatch failed: {result}")
            for capture in captures[start:]:
                instructions = (
                    server.TOOLS[name]._get_stance_enhanced_prompt(stance)
                    if name == "consensus"
                    else server.TOOLS[name].get_system_prompt()
                )
                capture["instructions_preserved"] = instructions.strip() in request_text(capture["request"])
            return result

        def audit(thread_id, expected):
            actual = [turn.content for turn in memory.get_thread(thread_id).turns]
            audits.append({"thread": normalize(thread_id), "complete": actual == expected, "turns": len(actual)})
            if actual != expected:
                raise AssertionError(f"Audit content mismatch in {normalize(thread_id)}")

        try:
            primary = root / "alpha.py"
            secondary = root / "beta.py"
            primary.write_text("VALUE = '[[FILE_ORIGINAL]]'\n", encoding="utf-8")
            secondary.write_text("VALUE = '[[FILE_ADDED]]'\n", encoding="utf-8")
            for path in (primary, secondary):
                os.utime(path, (1_700_000_000, 1_700_000_000))
            thread_id = None
            expected = []
            for index in range(14):
                if index == 8:
                    os.utime(primary, (1_700_000_010, 1_700_000_010))
                if index == 9:
                    primary.write_text("VALUE = '[[FILE_EDITED]]'\n", encoding="utf-8")
                    os.utime(primary, (1_700_000_020, 1_700_000_020))
                sentinels = ["[[FILE_EDITED]]" if index >= 9 else "[[FILE_ORIGINAL]]"]
                files = [str(primary)]
                if index >= 7:
                    files.append(str(secondary))
                    sentinels.append("[[FILE_ADDED]]")
                prompt = f"[[CHAT_INPUT_{index:02d}]]"
                result = await invoke(
                    "chat",
                    {
                        "prompt": prompt,
                        "model": "gpt-5-mini" if index == 6 else "gpt-4.1",
                        "working_directory_absolute_path": str(root),
                        "absolute_file_paths": files,
                        **({"continuation_id": thread_id} if thread_id else {}),
                    },
                    "chat_files",
                    [*expected, prompt, *sentinels],
                    forbidden=["[[FILE_ORIGINAL]]"] if index >= 9 else [],
                )
                thread_id = result["continuation_offer"]["continuation_id"]
                expected.extend([prompt, f"[[ANSWER_{len(captures):02d}]]"])
            audit(thread_id, expected)

            # Multi-step workflow plus cross-tool history and real customization probe.
            hook = stack.enter_context(
                patch.object(
                    ThinkDeepTool,
                    "customize_expert_analysis_prompt",
                    autospec=True,
                    return_value="[[CUSTOMIZATION_HOOK]]",
                )
            )
            local = await invoke(
                "thinkdeep",
                {
                    "step": "[[WORK_STEP_1]]",
                    "findings": "[[FINDINGS_1]]",
                    "step_number": 1,
                    "total_steps": 2,
                    "next_step_required": True,
                    "model": "gpt-4.1",
                    "continuation_id": thread_id,
                    "relevant_files": [str(primary), str(secondary)],
                    "relevant_context": ["SYMBOL_Z", "SYMBOL_A", "SYMBOL_M"],
                },
                "workflow_local",
                [],
            )
            local_reply = memory.get_thread(thread_id).turns[-1].content
            if len(captures) != 14:
                raise AssertionError("Local workflow step unexpectedly called a model")
            await invoke(
                "thinkdeep",
                {
                    "step": "[[WORK_STEP_2]]",
                    "findings": "[[FINDINGS_2]]",
                    "step_number": 2,
                    "total_steps": 2,
                    "next_step_required": False,
                    "model": "gpt-4.1",
                    "continuation_id": local["continuation_offer"]["continuation_id"],
                },
                "workflow_expert",
                [*expected, "[[WORK_STEP_1]]", "[[WORK_STEP_2]]", "[[FILE_EDITED]]", "[[FILE_ADDED]]"],
                required=["[[FINDINGS_1]]", "[[FINDINGS_2]]"],
            )
            expected.extend(["[[WORK_STEP_1]]", local_reply, "[[WORK_STEP_2]]", f"[[ANSWER_{len(captures):02d}]]"])
            audit(thread_id, expected)

            # Consensus starts with generic history but each fresh participant is blinded.
            consensus_thread = memory.create_thread("chat", {})
            memory.add_turn(consensus_thread, "user", "[[GENERIC_HISTORY]]", tool_name="chat")
            forbidden = ["[[GENERIC_HISTORY]]", "[[CONSENSUS_SUMMARY]]", "[[CONSENSUS_FINDINGS]]"]
            first = await invoke(
                "consensus",
                {
                    "step": "[[ORIGINAL_PROPOSAL]]",
                    "findings": "initial caller assessment",
                    "step_number": 1,
                    "total_steps": 3,
                    "next_step_required": True,
                    "continuation_id": consensus_thread,
                    "relevant_files": [str(primary)],
                    "models": [{"model": "gpt-4.1", "stance": "for"}, {"model": "gpt-5-mini", "stance": "against"}],
                },
                "consensus",
                ["[[ORIGINAL_PROPOSAL]]", "[[FILE_EDITED]]"],
                required=["SUPPORTIVE"],
                forbidden=forbidden,
                stance="for",
            )
            first_verdict = f"[[ANSWER_{len(captures):02d}]]"
            await invoke(
                "consensus",
                {
                    "step": "[[CONSENSUS_SUMMARY]]",
                    "findings": "[[CONSENSUS_FINDINGS]]",
                    "step_number": 2,
                    "total_steps": 3,
                    "next_step_required": True,
                    "continuation_id": first["continuation_offer"]["continuation_id"],
                    "relevant_files": [str(primary)],
                },
                "consensus",
                ["[[ORIGINAL_PROPOSAL]]", "[[FILE_EDITED]]"],
                required=["CRITICAL"],
                forbidden=[*forbidden, first_verdict],
                stance="against",
            )
            second_verdict = f"[[ANSWER_{len(captures):02d}]]"
            await invoke(
                "consensus",
                {
                    "step": "[[FINAL_SYNTHESIS]]",
                    "findings": "Caller synthesis",
                    "step_number": 3,
                    "total_steps": 3,
                    "next_step_required": False,
                    "continuation_id": consensus_thread,
                },
                "consensus_local",
                [],
            )
            final_reply = memory.get_thread(consensus_thread).turns[-1].content
            audit(
                consensus_thread,
                [
                    "[[GENERIC_HISTORY]]",
                    "[[ORIGINAL_PROPOSAL]]",
                    first_verdict,
                    "[[CONSENSUS_SUMMARY]]",
                    second_verdict,
                    "[[FINAL_SYNTHESIS]]",
                    final_reply,
                ],
            )

            await invoke(
                "chat",
                {"prompt": "[[RESPONSES_INPUT]]", "model": "o3-pro", "working_directory_absolute_path": str(root)},
                "responses",
                ["[[RESPONSES_INPUT]]"],
            )
            review = await invoke(
                "codereview",
                {
                    "step": "[[REVIEW_STEP_1]]",
                    "findings": "[[REVIEW_FINDING_1]]",
                    "step_number": 1,
                    "total_steps": 2,
                    "next_step_required": True,
                    "model": "gpt-4.1",
                    "relevant_files": [str(primary)],
                },
                "codereview_local",
                [],
            )
            await invoke(
                "codereview",
                {
                    "step": "[[REVIEW_STEP_2]]",
                    "findings": "[[REVIEW_FINDING_2]]",
                    "step_number": 2,
                    "total_steps": 2,
                    "next_step_required": False,
                    "model": "gpt-4.1",
                    "continuation_id": review["continuation_offer"]["continuation_id"],
                },
                "codereview",
                ["[[REVIEW_STEP_1]]", "[[REVIEW_STEP_2]]", "[[FILE_EDITED]]"],
                required=["[[REVIEW_FINDING_1]]", "[[REVIEW_FINDING_2]]"],
            )
            events = list(read_events(sorted((root / "state" / "transcripts").glob("*.jsonl"))))
            usage = usage_report(events)
            if sum(group["calls"] for group in usage["groups"]) != len(captures):
                raise AssertionError(
                    "Persisted usage does not cover every captured call; private POSIX transcripts must be available"
                )
            probes = {
                "chat_web_search_guidance_present": "WEB SEARCH CAPABILITY" in request_text(captures[0]["request"]),
                "chat_tool_specific_search_guidance_present": server.TOOLS["chat"].get_websearch_guidance()
                in request_text(captures[0]["request"]),
                "thinkdeep_customization_hook_called": hook.call_count > 0,
                "codereview_contains_literal_newlines": "=== CODE REVIEW REQUEST ===\\n"
                in request_text(captures[-1]["request"]),
            }
            return {
                "schema_version": 1,
                "scope": "synthetic dispatcher to real SDK serializer with fake HTTP transport",
                "normalization": "Fixture directory and thread UUIDs only; fixture mtimes fixed.",
                "captures": captures,
                "audits": audits,
                "investigations": probes,
                "prefix_report": prefix_report(captures),
                "usage_report": usage,
            }
        finally:
            await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="pal-cache-capture-") as directory:
        result = asyncio.run(capture_scenarios(Path(directory)))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "requests": len(result["captures"]),
                "complete": result["prefix_report"]["complete"],
                "investigations": result["investigations"],
            }
        )
    )
    if not result["prefix_report"]["complete"]:
        parser.exit(1, "Captured request completeness failed; inspect output diagnostics.\n")


if __name__ == "__main__":
    main()

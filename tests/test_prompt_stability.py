"""Complete provider requests must be stable across process hash seeds."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import server
from scripts.cache_prefix import compare_runs, request_text
from tests.cache_capture import capture_scenarios
from tools.consensus import ConsensusTool
from tools.shared.base_models import ConsolidatedFindings

pytestmark = pytest.mark.no_mock_provider


@pytest.mark.skipif(sys.platform == "win32", reason="Private transcript capture requires POSIX")
def test_complete_wire_requests_are_identical_across_hash_seeds(tmp_path):
    runs = []
    for seed in (1, 7, 42):
        output = tmp_path / f"seed-{seed}.json"
        subprocess.run(
            [sys.executable, "-m", "tests.cache_capture", "--output", str(output)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONHASHSEED": str(seed), "PAL_SKIP_DOTENV": "true"},
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        runs.append(json.loads(output.read_text(encoding="utf-8")))
    for run in runs:
        assert run["prefix_report"]["complete"]
        assert all(capture["instructions_preserved"] for capture in run["captures"])
        assert all(audit["complete"] for audit in run["audits"])
        # Volatile totals follow all historic content; attribution and current files survive.
        text = request_text(run["captures"][2]["request"])
        assert text.index("[[CHAT_INPUT_00]]") < text.index("Turn 5/100")
        assert "using chat via openai" in text
        assert "Last modified:" in text
        assert "[[FILE_ORIGINAL]]" in text
    for run in runs[1:]:
        comparison = compare_runs(runs[0]["captures"], run["captures"])
        assert comparison["complete"]
        assert all(row["identical"] for row in comparison["comparisons"])


@pytest.mark.parametrize(
    "name", ["analyze", "codereview", "debug", "precommit", "refactor", "secaudit", "testgen", "thinkdeep"]
)
def test_workflow_symbol_rendering_is_sorted_and_complete(name):
    tool = type(server.TOOLS[name])()
    findings = ConsolidatedFindings(relevant_context={"SYMBOL_Z", "SYMBOL_A", "SYMBOL_M"}, findings=["FINDING"])
    text = tool.prepare_expert_analysis_context(findings)
    assert text.index("SYMBOL_A") < text.index("SYMBOL_M") < text.index("SYMBOL_Z")
    assert all(text.count(symbol) == 1 for symbol in findings.relevant_context)
    assert "FINDING" in text


def test_consensus_stances_follow_identical_instructions_and_keep_custom_stance():
    tool = ConsensusTool()
    prompts = [tool._get_stance_enhanced_prompt(stance) for stance in ("for", "against", "neutral")]
    prefixes = [prompt.split("PERSPECTIVE FRAMEWORK\n")[0] for prompt in prompts]
    assert prefixes[0] == prefixes[1] == prefixes[2]
    for prefix in prefixes:
        assert "MANDATORY RESPONSE FORMAT" in prefix
        assert "Bad ideas must be called out regardless of stance" in prefix
    assert "SUPPORTIVE" in prompts[0]
    assert "CRITICAL" in prompts[1]
    assert "BALANCED" in prompts[2]
    custom = tool._get_stance_enhanced_prompt("for", "CUSTOM STANCE")
    assert custom.split("PERSPECTIVE FRAMEWORK\n")[0] == prefixes[0]
    assert custom.count("CUSTOM STANCE") == 1
    assert "{stance_prompt}" not in custom


@pytest.mark.skipif(sys.platform == "win32", reason="Private transcript capture requires POSIX")
async def test_same_model_stances_share_instructions_without_leaking_verdicts(tmp_path):
    result = await capture_scenarios(tmp_path, consensus_model="gpt-4.1")
    assert result["prefix_report"]["complete"]
    pairs = [row for row in result["prefix_report"]["requests"] if row["scenario"] == "consensus"]
    assert pairs[0]["model"] == pairs[1]["model"]
    assert pairs[1]["lcp_bytes"] > 5000

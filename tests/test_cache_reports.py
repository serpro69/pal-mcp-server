"""Measurement gates must reject omission, duplication and invented cache rates."""

import copy
import sys

import pytest

from scripts.cache_prefix import common_prefix_bytes, compare_runs, prefix_report, request_text
from scripts.cache_report import read_events, usage_report
from tests.cache_capture import capture_scenarios

pytestmark = pytest.mark.no_mock_provider


def event(turn, calls):
    return {
        "event": "turn",
        "role": "assistant",
        "schema_version": 1,
        "thread_id": "fixture-thread",
        "turn": turn,
        "model_metadata": {
            "provider_calls": [
                {"call_index": i, "provider": "p", "model": "m", "usage": u} for i, u in enumerate(calls, 1)
            ],
            "usage": {"input_tokens": 999999},
        },
    }


def test_usage_coverage_uses_paired_denominators_and_never_aggregate_usage():
    events = [
        event(
            2,
            [
                {"input_tokens": 100, "cached_input_tokens": 50},
                {"input_tokens": 100},
                {"cached_input_tokens": 90},
                {"input_tokens": 0, "cached_input_tokens": 0},
                {"input_tokens": 5, "cached_input_tokens": 7},
            ],
        ),
        event(4, []),
    ]
    report = usage_report([*events, events[0]])
    group = report["groups"][0]
    assert group["calls"] == 5 and group["cache_read_ratio"] == 0.5
    assert group["cache_ratio_known_calls"] == 2 and group["inconsistent_cache_counts"] == 1
    assert group["fields"]["input_tokens"] == {"known_calls": 4, "missing_calls": 1, "sum": 205}
    assert group["fields"]["cache_write_tokens"]["sum"] is None
    assert report["diagnostics"]["duplicate_turns"] == 1


def test_unknown_zero_and_malformed_counts_do_not_merge():
    row = usage_report(
        [
            event(
                2,
                [
                    {},
                    {"cached_input_tokens": 0, "input_tokens": 0},
                    {"cached_input_tokens": True, "input_tokens": "7", "cache_write_tokens": -2},
                ],
            )
        ]
    )["groups"][0]
    assert row["fields"]["cached_input_tokens"] == {"known_calls": 1, "missing_calls": 2, "sum": 0}
    assert row["cache_read_ratio"] is None
    assert usage_report([event(2, [])])["groups"] == []


def test_prefix_gate_rejects_omission_duplication_and_forbidden_content():
    base = {
        "scenario": "fixture",
        "provider": "p",
        "model": "m",
        "request": {"messages": [{"role": "user", "content": "REQUIRED"}]},
        "contract": {"required_once": ["REQUIRED"], "forbidden": ["FORBIDDEN"]},
    }
    assert prefix_report([base, base])["complete"]
    for text in ("", "REQUIRED REQUIRED", "REQUIRED FORBIDDEN"):
        broken = copy.deepcopy(base)
        broken["request"]["messages"][0]["content"] = text
        report = prefix_report([base, broken])
        assert not report["complete"] and all(row["lcp_bytes"] is None for row in report["requests"])
    assert not prefix_report([])["complete"]
    assert common_prefix_bytes("Ωx".encode(), "Ωy".encode()) == 2


def test_cross_run_comparison_retains_differences_and_refuses_incomplete_requests():
    a = {
        "scenario": "fixture",
        "provider": "p",
        "model": "m",
        "request": {"messages": [{"role": "user", "content": "REQ one two"}]},
        "contract": {"required_once": ["REQ"]},
    }
    b = copy.deepcopy(a)
    b["request"]["messages"][0]["content"] = "REQ two one"
    result = compare_runs([a], [b])
    assert result["complete"] and not result["comparisons"][0]["identical"]
    b["request"]["messages"][0]["content"] = "one two"
    assert compare_runs([a], [b]) == {"complete": False, "comparisons": []}
    b = copy.deepcopy(a)
    b["model"] = "other"
    with pytest.raises(ValueError, match="must match"):
        compare_runs([a], [b])
    a["contract"] = {}
    with pytest.raises(ValueError, match="completeness contract"):
        prefix_report([a])


def test_report_counts_malformed_records_and_legacy_turns(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('not-json\n[]\n{"event":"turn","role":"assistant","schema_version":1,"thread_id":"t","turn":2}\n')
    report = usage_report(read_events([path]))
    assert not report["groups"]
    assert report["diagnostics"]["invalid_records"] == 2
    assert report["diagnostics"]["legacy_assistant_turns"] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="Private transcript capture requires POSIX")
async def test_dispatcher_capture_completeness_audits_and_usage(tmp_path):
    result = await capture_scenarios(tmp_path)
    assert result["prefix_report"]["complete"]
    assert len(result["captures"]) == 19
    assert all(c["instructions_preserved"] for c in result["captures"])
    assert all(audit["complete"] for audit in result["audits"])
    assert [audit["turns"] for audit in result["audits"]] == [28, 32, 7]
    groups = result["usage_report"]["groups"]
    assert sum(group["calls"] for group in groups) == 19
    assert sum(group["fields"]["input_tokens"]["sum"] for group in groups) == 19000
    assert result["usage_report"]["diagnostics"] == {
        "invalid_records": 0,
        "duplicate_turns": 0,
        "legacy_assistant_turns": 0,
        "invalid_calls": 0,
    }
    consensus = [c for c in result["captures"] if c["scenario"] == "consensus"]
    assert len(consensus) == 2 and consensus[0]["model"] != consensus[1]["model"]
    assert result["investigations"] == {
        "chat_web_search_guidance_present": True,
        "chat_tool_specific_search_guidance_present": True,
        "thinkdeep_customization_hook_called": True,
        "codereview_contains_literal_newlines": False,
    }
    for capture in result["captures"]:
        text = request_text(capture["request"])
        if capture["scenario"] == "chat_files":
            assert text.count("WEB SEARCH CAPABILITY:") == 1
            assert text.count("When discussing topics, consider if searches for these would help:") == 1
        elif capture["scenario"] == "workflow_expert":
            assert text.count("DEEP THINKING ANALYSIS VALIDATION") == 1
            assert text.count("[[PROBLEM_CONTEXT]]") == 1
            assert text.count("[[FINDINGS_2]]") == 1
            assert text.count("- Focus Areas: [[FOCUS_AREA]]") == 1
        elif capture["scenario"] == "codereview":
            assert "=== CODE REVIEW REQUEST ===\n" in text
            assert "=== AGENT'S CODE REVIEW INVESTIGATION ===\n" in text
            # Stored local replies are JSON and legitimately escape newlines.
            # The newly assembled expert context must use actual separators.
            assert "=== CODE REVIEW REQUEST ===\\n" not in text
            assert "=== AGENT'S CODE REVIEW INVESTIGATION ===\\n" not in text


async def test_capture_fails_when_transcript_side_channel_drops_usage(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.transcripts.record_event", lambda *args: None)
    with pytest.raises(AssertionError, match="Persisted usage does not cover every captured call"):
        await capture_scenarios(tmp_path)

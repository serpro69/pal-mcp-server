"""Reject prompt-prefix wins that cost fresh context or alter file admission."""

import pytest

from tests.history_layout_capture import CALLS, HISTORY_LIMIT, LAYOUTS, SCENARIOS, first_seen_files, measure, summarize
from utils.conversation_memory import ConversationTurn, ThreadContext, get_conversation_file_list
from utils.file_utils import FileReadReport, read_files

pytestmark = pytest.mark.no_mock_provider


async def test_layout_experiments_preserve_current_content_and_truthful_compaction(tmp_path):
    report = await measure(tmp_path)
    assert len(report["results"]) == len(LAYOUTS) * len(SCENARIOS)
    for result in report["results"]:
        assert result["prefix_report"]["complete"]
        assert result["audit_turns"] == 2 * CALLS
        assert len(result["captures"]) == CALLS
        assert all(capture["instructions_preserved"] for capture in result["captures"])
        if result["scenario"] == "compacted":
            assert result["coverage"][-1]["omitted_prior_turns"] > 0
            assert max(call["history_tokens"] or 0 for call in result["coverage"]) <= HISTORY_LIMIT + 400
        else:
            assert all(call["omitted_prior_turns"] == 0 for call in result["coverage"])
    rows = {(row["scenario"], row["layout"]): row for row in summarize(report)["rows"]}
    current = rows["compacted", "current"]
    hysteresis = rows["compacted", "hysteresis"]
    # This candidate discards more usable turns without reducing rewrites.
    assert sum(c["visible_prior_turns"] for c in hysteresis["coverage"]) < sum(
        c["visible_prior_turns"] for c in current["coverage"]
    )
    assert hysteresis["compaction_events"] >= current["compaction_events"]
    assert rows["fixed", "first_seen"]["lcp_bytes"] == rows["fixed", "current"]["lcp_bytes"]
    assert sum(rows["added", "first_seen"]["lcp_bytes"]) > sum(rows["added", "current"]["lcp_bytes"])
    assert sum(rows["touched", "trailing_mtime"]["lcp_bytes"]) > sum(rows["touched", "current"]["lcp_bytes"])
    assert sum(rows["edited", "trailing_mtime"]["lcp_bytes"]) <= sum(rows["edited", "current"]["lcp_bytes"])


def test_first_seen_can_displace_current_attachment_under_file_budget(tmp_path):
    old, current = tmp_path / "old.py", tmp_path / "current.py"
    old.write_text("[[OLD]]\n" + "x" * 1800, encoding="utf-8")
    current.write_text("[[CURRENT]]\n" + "x" * 1800, encoding="utf-8")
    stamp = "2026-10-12T00:00:00Z"
    context = ThreadContext(
        thread_id="12345678-1234-1234-1234-123456789012",
        tool_name="chat",
        initial_context={},
        created_at=stamp,
        last_updated_at=stamp,
        turns=[
            ConversationTurn(role="user", content="old file", timestamp=stamp, files=[str(old)]),
            ConversationTurn(role="user", content="current file", timestamp=stamp, files=[str(current)]),
        ],
    )
    baseline, candidate = FileReadReport(), FileReadReport()
    before = read_files(get_conversation_file_list(context), max_tokens=650, reserve_tokens=0, report=baseline)
    after = read_files(first_seen_files(context), max_tokens=650, reserve_tokens=0, report=candidate)
    assert baseline.included == [str(current)]
    assert candidate.included == [str(old)]
    assert "[[CURRENT]]" in before and "[[CURRENT]]" not in after
    assert "SKIPPED FILES" in before and "SKIPPED FILES" in after

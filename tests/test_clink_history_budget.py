"""Complete-exchange CLI history, independent of provider context or tokenizers."""

from uuid import uuid4

import pytest

from clink.constants import DEFAULT_HISTORY_TOKEN_BUDGET
from clink.conversation import render_history
from utils import conversation_memory as memory
from utils.token_utils import estimate_tokens


def turn(role, content, **kwargs):
    return memory.ConversationTurn(role=role, content=content, timestamp="2026-10-13T00:00:00Z", **kwargs)


def thread(turns):
    return memory.ThreadContext(
        thread_id=str(uuid4()),
        created_at="2026-10-13T00:00:00Z",
        last_updated_at="2026-10-13T00:00:00Z",
        tool_name="chat",
        turns=turns,
        initial_context={},
    )


def test_default_budget_and_empty_history():
    result = render_history(thread([]))
    assert result.text == "" and result.turns == []
    assert result.metadata == {
        "estimated_tokens": 0,
        "token_budget": 16_384,
        "retained_messages": 0,
        "omitted_messages": 0,
        "incomplete_messages": 0,
        "incomplete_legacy_omitted": False,
    }
    assert DEFAULT_HISTORY_TOKEN_BUDGET == 16_384


def test_newest_suffix_keeps_multi_assistant_exchanges_and_order():
    turns = [
        turn("user", "OLD_QUESTION" + "x" * 2000),
        turn("assistant", "OLD_ANSWER"),
        turn("user", "MIDDLE_QUESTION"),
        turn("assistant", "MIDDLE_ANSWER"),
        turn("assistant", "MIDDLE_SECOND_ANSWER"),
        turn("user", "LATEST_QUESTION"),
        turn("assistant", "LATEST_ANSWER"),
    ]
    context = thread(turns)
    before = context.model_dump_json()
    result = render_history(context, token_budget=300)
    assert result.turns == turns[2:]
    assert "OLD_QUESTION" not in result.text and "OLD_ANSWER" not in result.text
    positions = [result.text.index(item.content) for item in turns[2:]]
    assert positions == sorted(positions)
    assert "Omitted 2 messages" in result.text
    assert "Turn 3 (Agent)" in result.text
    assert result.metadata["retained_messages"] == 5
    assert result.metadata["omitted_messages"] == 2
    assert result.metadata["estimated_tokens"] == estimate_tokens(result.text) <= 300
    assert context.model_dump_json() == before


def test_oversized_middle_exchange_does_not_allow_skipping_back_to_an_older_one():
    turns = [
        turn("user", "OLDEST"),
        turn("assistant", "old answer"),
        turn("user", "MIDDLE"),
        turn("assistant", "x" * 3000),
        turn("user", "NEWEST"),
        turn("assistant", "new answer"),
    ]
    result = render_history(thread(turns), token_budget=300)
    assert result.turns == turns[-2:]
    assert "OLDEST" not in result.text and "MIDDLE" not in result.text
    assert result.metadata["omitted_messages"] == 4


def test_exact_final_budget_includes_wrappers_and_guidance():
    context = thread([turn("user", "question"), turn("assistant", "answer")])
    complete = render_history(context)
    exact_budget = estimate_tokens(complete.text)
    assert exact_budget > estimate_tokens("questionanswer")
    assert render_history(context, token_budget=exact_budget).text == complete.text
    with pytest.raises(ValueError, match="newest complete exchange.*history-only budget"):
        render_history(context, token_budget=exact_budget - 1)


def test_omission_notice_is_included_in_final_budget():
    latest = [turn("user", "latest question"), turn("assistant", "latest answer")]
    context = thread(latest)
    budget = estimate_tokens(render_history(context).text)
    context.turns = [turn("user", "x" * 5000), turn("assistant", "old answer")] + latest
    with pytest.raises(ValueError, match="newest complete exchange"):
        render_history(context, token_budget=budget)


@pytest.mark.parametrize("field", ["files", "images"])
def test_path_references_are_rendered_and_charged_to_budget(field):
    reference = "/unread-historical-" + "x" * 4000
    context = thread([turn("user", "question", **{field: [reference]}), turn("assistant", "answer")])
    result = render_history(context)
    assert f"{field.title()}: {reference}" in result.text
    assert result.metadata["estimated_tokens"] == estimate_tokens(result.text)
    with pytest.raises(ValueError, match="including wrappers and references"):
        render_history(context, token_budget=300)


@pytest.mark.parametrize(
    "legacy",
    [
        [("user", "UNANSWERED")],
        [("assistant", "ORPHAN")],
        [("system", "UNSUPPORTED")],
        [("user", "UNANSWERED"), ("system", "UNSUPPORTED"), ("assistant", "ORPHAN")],
    ],
)
def test_nonempty_history_without_a_complete_exchange_is_an_error(legacy):
    with pytest.raises(ValueError, match="no complete exchange"):
        render_history(thread([turn(role, content) for role, content in legacy]))


def test_incomplete_legacy_groups_are_counted_and_never_repaired():
    context = thread(
        [
            turn("assistant", "ORPHAN"),
            turn("user", "UNANSWERED"),
            turn("user", "COMPLETE_QUESTION"),
            turn("assistant", "COMPLETE_ANSWER"),
            turn("assistant", "SECOND_ANSWER"),
            turn("user", "TRAILING_USER"),
        ]
    )
    before = context.model_dump_json()
    result = render_history(context)
    assert result.turns == context.turns[2:5]
    assert result.metadata["incomplete_messages"] == 3
    assert result.metadata["omitted_messages"] == 3
    assert result.metadata["incomplete_legacy_omitted"] is True
    assert "3 from incomplete legacy groups" in result.text
    for marker in ("ORPHAN", "UNANSWERED", "TRAILING_USER"):
        assert marker not in result.text
    assert context.model_dump_json() == before


@pytest.mark.parametrize("visible", ["DISPLAYED_REPLY", ""])
def test_assistant_display_projection_preserves_raw_record(visible):
    context = thread(
        [
            turn("user", "ORIGINAL_INPUT", model_metadata={"clink": {"visible_reply": "WRONG_USER_TEXT"}}),
            turn(
                "assistant", "RAW_REPLY", model_metadata={"clink": {"visible_reply": visible}, "usage": {"tokens": 10}}
            ),
        ]
    )
    result = render_history(context)
    assert "ORIGINAL_INPUT" in result.text and "WRONG_USER_TEXT" not in result.text
    assert "RAW_REPLY" not in result.text
    if visible:
        assert visible in result.text
    assert context.turns[1].content == "RAW_REPLY"
    assert context.turns[1].model_metadata["usage"] == {"tokens": 10}


@pytest.mark.parametrize("metadata", [None, {}, {"clink": None}, {"clink": {"visible_reply": None}}])
def test_legacy_reply_without_display_projection_uses_raw_content(metadata):
    result = render_history(thread([turn("user", "question"), turn("assistant", "RAW_REPLY", model_metadata=metadata)]))
    assert "RAW_REPLY" in result.text

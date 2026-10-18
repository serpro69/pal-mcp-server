"""Legacy quick scenarios must submit current schemas and fail on MCP errors."""

from types import SimpleNamespace

import jsonschema
import pytest

from simulator_tests.test_basic_conversation import BasicConversationTest
from simulator_tests.test_per_tool_deduplication import PerToolDeduplicationTest
from tools.chat import ChatTool
from tools.codereview import CodeReviewTool
from tools.precommit import PrecommitTool


@pytest.mark.parametrize("scenario_type,count", [(BasicConversationTest, 3), (PerToolDeduplicationTest, 4)])
@pytest.mark.parametrize("fail_at", [None, 1, 2, 3])
def test_quick_scenarios_submit_valid_requests_and_reject_tool_errors(
    monkeypatch, tmp_path, scenario_type, count, fail_at
):
    monkeypatch.chdir(tmp_path)
    scenario = scenario_type()
    schemas = {
        "chat": ChatTool().get_input_schema(),
        "precommit": PrecommitTool().get_input_schema(),
        "codereview": CodeReviewTool().get_input_schema(),
    }
    calls = []
    continuation = "11111111-1111-1111-1111-111111111111"

    def invoke(name, arguments):
        jsonschema.validate(arguments, schemas[name])
        calls.append((name, arguments))
        failed = len(calls) == fail_at
        scenario.last_result = SimpleNamespace(isError=failed)
        # An error must fail even with nonempty content and a continuation-shaped value.
        return ("synthetic tool error" if failed else "synthetic answer"), continuation

    monkeypatch.setattr(scenario, "call_mcp_tool", invoke)
    monkeypatch.setattr(
        scenario,
        "get_server_logs_since",
        lambda *args: f"[FILE_PROCESSING] dummy_code.py new_feature.py continuation {continuation}",
    )
    assert scenario.run_test() is (fail_at is None)
    assert len(calls) == (count if fail_at is None else fail_at)
    if fail_at is None and scenario_type is PerToolDeduplicationTest:
        precommit = [arguments for name, arguments in calls if name == "precommit"]
        assert [request["step_number"] for request in precommit] == [1, 2, 3]
        assert [request["next_step_required"] for request in precommit] == [True, True, False]
    assert not (tmp_path / "dummy_code.py").exists()
    assert not (tmp_path / "new_feature.py").exists()

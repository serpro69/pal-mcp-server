"""Transport/state contracts; no routing or model-reasoning expectations."""

import json
import os
from uuid import uuid4

from .base_test import BaseSimulatorTest


class ProviderAgnosticTest(BaseSimulatorTest):
    provider_agnostic = True

    def invoke(self, tool: str, **arguments):
        text, continuation = self.call_mcp_tool(tool, arguments)
        if not text or self.last_result.isError:
            raise AssertionError(f"{tool} failed: {text}")
        return json.loads(text), continuation

    def chat(self, **arguments):
        return self.invoke("chat", model=self.model, working_directory_absolute_path=os.getcwd(), **arguments)


class SessionContinuationTest(ProviderAgnosticTest):
    test_name = "session_continuation"
    test_description = "Stdio chat continuity and stored-turn accounting"

    def run_test(self) -> bool:
        first, thread = self.chat(prompt="Reply briefly: this is the first scenario turn.")
        if not thread or not first.get("content"):
            return False
        second, continued = self.chat(prompt="Reply briefly to the second turn.", continuation_id=thread)
        return (
            continued == thread
            and bool(second.get("content"))
            and second["continuation_offer"]["remaining_turns"] == first["continuation_offer"]["remaining_turns"] - 2
        )


class WorkflowSessionTest(ProviderAgnosticTest):
    test_name = "workflow_session"
    test_description = "Stdio workflow restoration and cross-tool continuation"

    def run_test(self) -> bool:
        _, thread = self.invoke(
            "planner",
            step="Plan a small documentation update.",
            step_number=1,
            total_steps=2,
            next_step_required=True,
        )
        if not thread:
            return False
        final, continued = self.invoke(
            "planner",
            step="Review the update and finish.",
            step_number=2,
            total_steps=2,
            next_step_required=False,
            continuation_id=thread,
        )
        if continued != thread or final.get("status") == "error":
            return False
        chat, continued = self.chat(prompt="Acknowledge the completed plan briefly.", continuation_id=thread)
        return continued == thread and bool(chat.get("content"))


class SessionErrorTest(ProviderAgnosticTest):
    test_name = "session_errors"
    test_description = "Stdio invalid continuation errors and session recovery"

    def run_test(self) -> bool:
        self.call_mcp_tool(
            "chat",
            {
                "prompt": "Unknown thread",
                "continuation_id": str(uuid4()),
                "model": self.model,
                "working_directory_absolute_path": os.getcwd(),
            },
        )
        if self.last_result is None or not self.last_result.isError:
            return False
        result, thread = self.chat(prompt="Reply briefly after the expected error.")
        return bool(thread and result.get("content"))

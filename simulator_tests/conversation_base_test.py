"""Compatibility helpers for conversation scenarios, now using real MCP dispatch.

The historical call_mcp_tool_direct name is retained for scenario callers. Every
call uses the same stdio process, including workflow continuations and cleanup.
"""

from .base_test import BaseSimulatorTest


class ConversationBaseTest(BaseSimulatorTest):
    def setUp(self):
        self.setup_test_files()

    def call_mcp_tool_direct(self, tool_name: str, params: dict):
        return super().call_mcp_tool(tool_name, params)

    def _extract_continuation_id_from_response(self, response_text: str):
        return self._extract_continuation_id(response_text)

    def tearDown(self):
        self.cleanup_test_files()

    @property
    def test_name(self) -> str:
        return self.__class__.__name__

    @property
    def test_description(self) -> str:
        return "MCP conversation scenario"

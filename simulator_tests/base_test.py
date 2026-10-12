#!/usr/bin/env python3
"""
Base Test Class for Communication Simulator Tests

Provides common functionality and utilities for all simulator tests.
"""

import json
import logging
import os
import subprocess
import sys
from contextlib import ExitStack
from typing import Optional

from .log_utils import LogUtils
from .server_session import SimulatorSession


class BaseSimulatorTest:
    """Base class for all communication simulator tests"""

    provider_agnostic = False

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.test_files = {}
        self.test_dir = None
        self._session = None
        self._resources = ExitStack()
        self.last_result = None
        self.model = os.getenv("SIMULATOR_MODEL", "auto").strip() or "auto"

        # Configure logging first
        log_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")
        self.logger = logging.getLogger(self.__class__.__name__)

        self.python_path = self._get_python_path()

    def _get_python_path(self) -> str:
        """Use the interpreter that has the simulator's dependencies installed."""
        return sys.executable

    def run(self) -> bool:
        """Own the entire scenario, including failed setup and interrupted calls."""
        try:
            return self.run_test()
        finally:
            self.close_session()

    def close_session(self):
        try:
            self._resources.__exit__(*sys.exc_info())
        finally:
            self._session = None

    def setup_test_files(self):
        """Create test files for the simulation"""
        # Test Python file
        python_content = '''"""
Sample Python module for testing MCP conversation continuity
"""

def fibonacci(n):
    """Calculate fibonacci number recursively"""
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

def factorial(n):
    """Calculate factorial iteratively"""
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result

class Calculator:
    """Simple calculator class"""

    def __init__(self):
        self.history = []

    def add(self, a, b):
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        return result

    def multiply(self, a, b):
        result = a * b
        self.history.append(f"{a} * {b} = {result}")
        return result
'''

        # Test configuration file
        config_content = """{
  "database": {
    "host": "localhost",
    "port": 5432,
    "name": "testdb",
    "ssl": true
  },
  "cache": {
    "redis_url": "redis://localhost:6379",
    "ttl": 3600
  },
  "logging": {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  }
}"""

        # Create files in the current project directory
        current_dir = os.getcwd()
        self.test_dir = os.path.join(current_dir, "test_simulation_files")
        os.makedirs(self.test_dir, exist_ok=True)

        test_py = os.path.join(self.test_dir, "test_module.py")
        test_config = os.path.join(self.test_dir, "config.json")

        with open(test_py, "w") as f:
            f.write(python_content)
        with open(test_config, "w") as f:
            f.write(config_content)

        # Ensure absolute paths for MCP server compatibility
        self.test_files = {"python": os.path.abspath(test_py), "config": os.path.abspath(test_config)}
        self.logger.debug(f"Created test files with absolute paths: {list(self.test_files.values())}")

    def call_mcp_tool(self, tool_name: str, params: dict) -> tuple[Optional[str], Optional[str]]:
        """Call tools on the scenario's persistent, lazily opened stdio session."""
        self.last_result = None
        try:
            if self._session is None:
                self._session = self._resources.enter_context(SimulatorSession())
            self.last_result = self._session.call_tool(tool_name, dict(params))
            response = "\n".join(item.text for item in self.last_result.content if item.type == "text")
            return response, self._extract_continuation_id(response)
        except Exception:
            self.logger.exception("MCP tool call failed: %s", tool_name)
            return None, None

    def _extract_continuation_id(self, response_text: str) -> Optional[str]:
        """Extract continuation_id from response metadata"""
        try:
            # Parse the response text as JSON to look for continuation metadata
            response_data = json.loads(response_text)

            # Look for continuation_id in various places
            if isinstance(response_data, dict):
                # Check for direct continuation_id field (new workflow tools)
                if "continuation_id" in response_data:
                    return response_data["continuation_id"]

                # Check metadata
                metadata = response_data.get("metadata", {})
                if "thread_id" in metadata:
                    return metadata["thread_id"]

                # Check follow_up_request
                follow_up = response_data.get("follow_up_request", {})
                if follow_up and "continuation_id" in follow_up:
                    return follow_up["continuation_id"]

                # Check continuation_offer
                continuation_offer = response_data.get("continuation_offer", {})
                if continuation_offer and "continuation_id" in continuation_offer:
                    return continuation_offer["continuation_id"]

            self.logger.debug(f"No continuation_id found in response: {response_data}")
            return None

        except json.JSONDecodeError as e:
            self.logger.debug(f"Failed to parse response for continuation_id: {e}")
            return None

    def run_command(self, cmd: list[str], check: bool = True, capture_output: bool = False, **kwargs):
        """Run a shell command with logging"""
        if self.verbose:
            self.logger.debug(f"Running: {' '.join(cmd)}")

        return subprocess.run(cmd, check=check, capture_output=capture_output, **kwargs)

    def create_additional_test_file(self, filename: str, content: str) -> str:
        """Create an additional test file for mixed scenario testing"""
        if not hasattr(self, "test_dir") or not self.test_dir:
            raise RuntimeError("Test directory not initialized. Call setup_test_files() first.")

        file_path = os.path.join(self.test_dir, filename)
        with open(file_path, "w") as f:
            f.write(content)
        # Return absolute path for MCP server compatibility
        return os.path.abspath(file_path)

    def cleanup_test_files(self):
        """Clean up test files without ending a scenario's MCP session."""
        if hasattr(self, "test_dir") and self.test_dir and os.path.exists(self.test_dir):
            import shutil

            shutil.rmtree(self.test_dir)
            self.logger.debug(f"Removed test files directory: {self.test_dir}")

    # ============================================================================
    # Log Utility Methods (delegate to LogUtils)
    # ============================================================================

    def get_server_logs_since(self, since_time: Optional[str] = None) -> str:
        """Get server logs from both main and activity log files."""
        return LogUtils.get_server_logs_since(since_time)

    def get_recent_server_logs(self, lines: int = 500) -> str:
        """Get recent server logs from the main log file."""
        return LogUtils.get_recent_server_logs(lines)

    def get_server_logs_subprocess(self, lines: int = 500) -> str:
        """Get server logs using subprocess (alternative method)."""
        return LogUtils.get_server_logs_subprocess(lines)

    def check_server_logs_for_errors(self, lines: int = 500) -> list[str]:
        """Check server logs for error messages."""
        return LogUtils.check_server_logs_for_errors(lines)

    def extract_conversation_usage_logs(self, logs: str) -> list[dict[str, int]]:
        """Extract token budget calculation information from logs."""
        return LogUtils.extract_conversation_usage_logs(logs)

    def extract_conversation_token_usage(self, logs: str) -> list[int]:
        """Extract conversation token usage values from logs."""
        return LogUtils.extract_conversation_token_usage(logs)

    def extract_thread_creation_logs(self, logs: str) -> list[dict[str, str]]:
        """Extract thread creation logs with parent relationships."""
        return LogUtils.extract_thread_creation_logs(logs)

    def extract_history_traversal_logs(self, logs: str) -> list[dict[str, any]]:
        """Extract conversation history traversal logs."""
        return LogUtils.extract_history_traversal_logs(logs)

    def validate_file_deduplication_in_logs(self, logs: str, tool_name: str, test_file: str) -> bool:
        """Validate that logs show file deduplication behavior."""
        return LogUtils.validate_file_deduplication_in_logs(logs, tool_name, test_file)

    def search_logs_for_pattern(
        self, pattern: str, logs: Optional[str] = None, case_sensitive: bool = False
    ) -> list[str]:
        """Search logs for a specific pattern."""
        return LogUtils.search_logs_for_pattern(pattern, logs, case_sensitive)

    def get_log_file_info(self) -> dict[str, dict[str, any]]:
        """Get information about log files."""
        return LogUtils.get_log_file_info()

    def run_test(self) -> bool:
        """Run the test - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement run_test()")

    @property
    def test_name(self) -> str:
        """Get the test name - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement test_name property")

    @property
    def test_description(self) -> str:
        """Get the test description - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement test_description property")

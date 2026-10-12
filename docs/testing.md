# Testing Guide

This project includes comprehensive test coverage through unit tests and integration simulator tests.

## Running Tests

### Prerequisites
- Environment set up: `./run-server.sh`
  - Use `./run-server.sh -f` to automatically follow logs after starting

### Unit Tests

Run all unit tests with pytest:
```bash
# Run all tests with verbose output
python -m pytest -xvs

# Run specific test file
python -m pytest tests/test_providers.py -xvs
```

### Simulator Tests

Each scenario owns one MCP stdio server for all its calls, using the Python
interpreter that launched the simulator. The process and client close on success,
failure and interruption. Historical `call_mcp_tool_direct` scenarios now use the
same real MCP dispatch path. Install the dev requirements for the AnyIO bridge.

`--tests`, `--individual`, `--quick`, `--ci` and `--list-tests` are mutually
exclusive. Summaries count only selected tests and remain visible after failures;
a selected failure produces a nonzero exit code.

`--ci` selects only provider-agnostic transport/state scenarios, controlled by
`SIMULATOR_MODEL` (default `auto`). These check continuation, workflow restoration,
cross-tool calls and explicit errors without judging the model's prose. Routing,
model-thinking and response-quality scenarios stay in the existing live tiers.

```bash
# Use your existing registered local model; do not change the default local model.
CUSTOM_API_URL=http://localhost:11434/v1 SIMULATOR_MODEL=local-llama \
  python communication_simulator_test.py --ci
```

For the deterministic tier, run `python -m simulator_tests.stub_provider`. It
binds an ephemeral IPv4 loopback port, supplies only `pal-stub` through a separate
catalog, and runs all three `--ci` scenarios. Provider metadata supplies the full
credential/configuration isolation list; the process-only `PAL_SKIP_DOTENV=true`
switch prevents even a forced-override dotenv file from restoring external
credentials. State/log files are temporary. No default local model changes.

The stub accepts only `/v1/models` and non-streaming `/v1/chat/completions`, with
strict model, message and output-limit validation. Any rejected request or selected
scenario failure fails the run. The server thread, request workers and each MCP
child are closed before exit. This tier runs on PRs and pushes to `main` in the
existing Python matrix. It validates integration contracts; local-model and paid
reasoning/routing tests remain separate, explicitly configured runs.

Simulator tests replicate real-world Claude CLI interactions with the standalone MCP server. Unlike unit tests that test isolated functions, simulator tests validate the complete end-to-end flow including:
- Actual MCP protocol communication
- Standalone server interactions
- Multi-turn conversations across tools
- Log output validation

**Important**: Simulator tests require `LOG_LEVEL=DEBUG` in your `.env` file to validate detailed execution logs.

#### Monitoring Logs During Tests

**Important**: The MCP stdio protocol interferes with stderr output during tool execution. Tool execution logs are written to local log files. This is a known limitation of the stdio-based MCP protocol.

To monitor logs during test execution:

```bash
# Start server and automatically follow logs
./run-server.sh -f

# Or manually monitor main server logs (includes all tool execution details)
tail -f -n 500 logs/mcp_server.log

# Monitor MCP activity logs (tool calls and completions)  
tail -f logs/mcp_activity.log

# Check log file sizes (logs rotate at 20MB)
ls -lh logs/mcp_*.log*
```

**Log Rotation**: All log files are configured with automatic rotation at 20MB to prevent disk space issues. The server keeps:
- 10 rotated files for mcp_server.log (200MB total)
- 5 rotated files for mcp_activity.log (100MB total)

**Why logs appear in files**: The MCP stdio_server captures stderr during tool execution to prevent interference with the JSON-RPC protocol communication. This means tool execution logs are written to files rather than displayed in console output.

#### Running All Simulator Tests
```bash
# Run all simulator tests
python communication_simulator_test.py

# Run with verbose output for debugging
python communication_simulator_test.py --verbose

# Keep server logs after tests for inspection
python communication_simulator_test.py --keep-logs
```

#### Running Individual Tests
To run a single simulator test in isolation (useful for debugging or test development):

```bash
# Run a specific test by name
python communication_simulator_test.py --individual basic_conversation

# Examples of available tests:
python communication_simulator_test.py --individual content_validation
python communication_simulator_test.py --individual cross_tool_continuation
python communication_simulator_test.py --individual memory_validation
```

#### Other Options
```bash
# List all available simulator tests with descriptions
python communication_simulator_test.py --list-tests

# Run multiple specific tests (not all)
python communication_simulator_test.py --tests basic_conversation content_validation

```

### Code Quality Checks

Before committing, ensure all linting passes:
```bash
# Run all linting checks
ruff check .
black --check .
isort --check-only .

# Auto-fix issues
ruff check . --fix
black .
isort .
```

## What Each Test Suite Covers

### Unit Tests
Test isolated components and functions:
- **Provider functionality**: Model initialization, API interactions, capability checks
- **Tool operations**: All MCP tools (chat, analyze, debug, etc.)
- **Conversation memory**: Threading, continuation, history management
- **File handling**: Path validation, token limits, deduplication
- **Auto mode**: Model selection logic and fallback behavior

### HTTP Recording/Replay Tests (HTTP Transport Recorder)
Tests for expensive API calls (like o3-pro) use custom recording/replay:
- **Real API validation**: Tests against actual provider responses
- **Cost efficiency**: Record once, replay forever
- **Provider compatibility**: Validates fixes against real APIs
- Uses HTTP Transport Recorder for httpx-based API calls
- See [HTTP Recording/Replay Testing Guide](./vcr-testing.md) for details

### Simulator Tests
Validate real-world usage scenarios by simulating actual Claude prompts:
- **Basic conversations**: Multi-turn chat functionality with real prompts
- **Cross-tool continuation**: Context preservation across different tools
- **File deduplication**: Efficient handling of repeated file references
- **Model selection**: Proper routing to configured providers
- **Token allocation**: Context window management in practice
- **Redis validation**: Conversation persistence and retrieval

## Contributing

For detailed contribution guidelines, testing requirements, and code quality standards, please see our [Contributing Guide](./contributions.md).

### Quick Testing Reference

```bash
# Run quality checks
./code_quality_checks.sh

# Run unit tests
python -m pytest -xvs

# Run simulator tests (for tool changes)
python communication_simulator_test.py
```

Remember: All tests must pass before submitting a PR. See the [Contributing Guide](./contributions.md) for complete requirements.

# Testing Guide

This project includes comprehensive test coverage through unit tests and integration simulator tests.

## Running Tests

### Prerequisites
- Environment set up: `./run-server.sh`
  - Use `./run-server.sh -f` to automatically follow logs after starting

### Unit Tests

Run all unit tests with pytest:
```bash
# Run unit tests without live integration calls
python -m pytest tests/ -v -m "not integration"

# Run specific test file
python -m pytest tests/test_providers.py -xvs
```

### CLI manifest isolation

Pytest uses the shipped `conf/cli_clients/` manifests by default. Before test
collection imports the server or builds tool schemas, `tests/conftest.py`
redirects the personal `~/.pal/cli_clients/` source to an unused temporary path,
clears `CLI_CLIENTS_CONFIG_PATH`, and resets the cached clink registry. The
environment override and cache are reset again before each test. This keeps
personal roles, commands, model settings, and malformed manifests out of ordinary
test results, including `tests/test_tool_surface.py`.

Tests that exercise configuration may still use `monkeypatch` to set
`CLI_CLIENTS_CONFIG_PATH` or `clink.registry.USER_CONFIG_DIR` to their own fixtures.
The registry's production precedence is unchanged. Pytest restores the original
session configuration on shutdown, including when tests fail; it does not edit
personal manifests or change `HOME`, `CODEX_HOME`, or `CLAUDE_CONFIG_DIR`.

Ordinary subprocess tests use `tests/cli_isolation.py`: the stdio bootstrap in
`tests/fixtures/isolated_server.py` and the request-capture helper activate it
before importing the server. It isolates personal manifests and the inherited
CLI override in the fresh interpreter too. The ordinary stdio bootstrap uses its
explicit child environment and disables checkout dotenv loading. Tests needing their own child CLI
manifests use the separate CLI-only fixture launcher, which preserves those
explicit fixtures.

Explicit live CLI checks remain separate: `PAL_CLINK_LIVE_TEST` selects an
installed CLI, and the live harness creates its own PAL manifests while retaining
native CLI authentication locations. See the [live clink checks](tools/clink.md#optional-live-conversation-check).
The in-process pytest fixture does not enable live calls, supply credentials, or
change dotenv handling; tests of dotenv behavior can still use synthetic settings.

### Integration launcher

`./run_integration_tests.sh` uses the activated environment's Python (including
virtualenvwrapper), falling back to the checkout's `.pal_venv`. Paths with spaces
and invocation from another directory are supported. Install both lock files into
the environment first. Default mode runs live integration tests and requires a
configured target; `--with-simulator` also runs the live simulator.

For offline validation, `./run_integration_tests.sh --stub` runs the strict local
simulator below with isolated credentials. It never selects the live tests.

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

Resolve the configured log directory instead of assuming repository-local logs.
The strict stub tier uses temporary state that it cleans up after each run.

To monitor logs during test execution:

```bash
# Start server and automatically follow logs
./run-server.sh -f

# Or manually monitor main server logs (includes all tool execution details)
pal_log_path=$(python -m utils.log_dir)
tail -f -n 500 "$pal_log_path/mcp_server.log"

# Monitor MCP activity logs (tool calls and completions)  
tail -f "$pal_log_path/mcp_activity.log"

# Check active and rotated files
ls -lh "$pal_log_path"/mcp_*.log*
```

Main logs rotate at 20 MiB with five backups (120 MiB including the active file);
activity logs rotate at 10 MiB with two backups (30 MiB including active). Logs
also reach stderr; stdout is reserved for MCP protocol traffic. See
[logging](logging.md) for paths, migration and diagnostic limits.

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
- **Memory validation**: In-process TTL conversation storage and retrieval

## Contributing

For detailed contribution guidelines, testing requirements, and code quality standards, please see our [Contributing Guide](./contributions.md).

### Quick Testing Reference

```bash
# Run quality checks
./code_quality_checks.sh

# Run unit tests
python -m pytest tests/ -v -m "not integration"

# Run simulator tests (for tool changes)
python communication_simulator_test.py
```

Remember: All tests must pass before submitting a PR. See the [Contributing Guide](./contributions.md) for complete requirements.

## Final reliability verification

Install both committed locks into fresh environments for Python 3.10, 3.11 and
3.12. Run the full unit suite and `python -m simulator_tests.stub_provider` in
each, and run `./code_quality_checks.sh` with an activated environment. Recompile
seeded temporary copies of both locks using the CI pip-compile flags and compare
them byte for byte. Package/image checks and independent code/spec reviews
complement this matrix; passing units alone does not close live-service gates.

[Task 42 evidence and release gates](feat/done/server-reliability-and-capabilities/final-verification.md)
record commands, counts, skip reasons and required follow-ups. Retain historical
cassettes until an explicitly configured recording run replaces them. A changed
request failing old replay is recorded as a compatibility gap, not edited into a
passing recording. Optional live model, CLI and Jules checks require their actual
credentials and targets; a deterministic stub cannot establish reasoning quality.

"""
PAL MCP Server - Main server implementation

This module implements the core MCP (Model Context Protocol) server that provides
AI-powered tools for code analysis, review, and assistance using multiple AI models.

The server follows the MCP specification to expose various AI tools as callable functions
that can be used by MCP clients (like Claude). Each tool provides specialized functionality
such as code review, debugging, deep thinking, and general chat capabilities.

Key Components:
- MCP Server: Handles protocol communication and tool discovery
- Tool Registry: Maps tool names to their implementations
- Request Handler: Processes incoming tool calls and returns formatted responses
- Configuration: Manages API keys and model settings

The server runs on stdio (standard input/output) and communicates using JSON-RPC messages
as defined by the MCP protocol.
"""

import asyncio
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from mcp.server import Server  # noqa: E402
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.shared.exceptions import McpError
from mcp.types import (  # noqa: E402
    ErrorData,
    GetPromptResult,
    JSONRPCRequest,
    Prompt,
    PromptMessage,
    PromptsCapability,
    Resource,
    ResourcesCapability,
    ResourceTemplate,
    ServerCapabilities,
    TextContent,
    Tool,
    ToolAnnotations,
    ToolsCapability,
)
from pydantic import AnyUrl

from clink import get_registry as get_cli_registry
from clink.availability import discover_cli_availability
from config import (  # noqa: E402
    DEFAULT_MODEL,
    __version__,
)
from providers.registry import ModelProviderRegistry
from tools import (  # noqa: E402
    AnalyzeTool,
    ChallengeTool,
    ChatTool,
    CLinkTool,
    CodeReviewTool,
    ConsensusTool,
    DebugIssueTool,
    DocgenTool,
    JulesTool,
    ListModelsTool,
    LookupTool,
    PlannerTool,
    PrecommitTool,
    RefactorTool,
    SecauditTool,
    TestGenTool,
    ThinkDeepTool,
    TracerTool,
    VersionTool,
)
from tools.models import ToolOutput  # noqa: E402
from tools.shared.exceptions import ToolExecutionError  # noqa: E402
from utils.env import env_override_enabled, get_env  # noqa: E402
from utils.log_dir import (
    ACTIVITY_LOG_BACKUPS,
    ACTIVITY_LOG_MAX_BYTES,
    ACTIVITY_LOG_NAME,
    MAIN_LOG_BACKUPS,
    MAIN_LOG_MAX_BYTES,
    MAIN_LOG_NAME,
    get_log_dir,
)
from utils.progress import request_progress
from utils.runtime_capabilities import (
    get_runtime_capabilities,
    initialize_runtime_capabilities,
    reset_runtime_capabilities,
)
from utils.state_dir import StateDirectoryError
from utils.transcript_reader import ResourceLimitError, ResourceNotFoundError, read_resource, resource_thread_id
from utils.transcripts import TranscriptError, prune_transcripts, transcripts_enabled

# Configure logging for server operations
# Can be controlled via LOG_LEVEL environment variable (DEBUG, INFO, WARNING, ERROR)
log_level = (get_env("LOG_LEVEL", "DEBUG") or "DEBUG").upper()

# Create timezone-aware formatter


class LocalTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        """Override to use local timezone instead of UTC"""
        ct = self.converter(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
            s = f"{t},{record.msecs:03.0f}"
        return s


class ActivityFileHandler(RotatingFileHandler):
    """Return emit failures to the caller, after releasing the handler lock."""

    def handleError(self, record):
        error = sys.exc_info()[1]
        if error is not None:
            raise error
        super().handleError(record)


# Configure both console and file logging
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Clear any existing handlers first
root_logger = logging.getLogger()
for old_handler in root_logger.handlers:
    if isinstance(old_handler, RotatingFileHandler):
        old_handler.close()
root_logger.handlers.clear()

# Create and configure stderr handler explicitly
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(getattr(logging, log_level, logging.INFO))
stderr_handler.setFormatter(LocalTimeFormatter(log_format))
root_logger.addHandler(stderr_handler)

# Note: MCP stdio_server interferes with stderr during tool execution
# File monitoring uses the shared operator-selected log directory.

# Set root logger level
root_logger.setLevel(getattr(logging, log_level, logging.INFO))

# Invalid/unwritable paths fail visibly; never silently write into the package.
log_dir = get_log_dir(create=True)
file_handler = RotatingFileHandler(
    log_dir / MAIN_LOG_NAME,
    maxBytes=MAIN_LOG_MAX_BYTES,
    backupCount=MAIN_LOG_BACKUPS,
    encoding="utf-8",
)
file_handler.setLevel(getattr(logging, log_level, logging.INFO))
file_handler.setFormatter(LocalTimeFormatter(log_format))
root_logger.addHandler(file_handler)

mcp_logger = logging.getLogger("mcp_activity")
for old_handler in list(mcp_logger.handlers):
    if isinstance(old_handler, RotatingFileHandler):
        old_handler.close()
        mcp_logger.removeHandler(old_handler)
mcp_file_handler = ActivityFileHandler(
    log_dir / ACTIVITY_LOG_NAME,
    maxBytes=ACTIVITY_LOG_MAX_BYTES,
    backupCount=ACTIVITY_LOG_BACKUPS,
    encoding="utf-8",
)
mcp_file_handler.setLevel(logging.INFO)
mcp_file_handler.setFormatter(LocalTimeFormatter("%(asctime)s - %(message)s"))
mcp_logger.addHandler(mcp_file_handler)
mcp_logger.setLevel(logging.INFO)
mcp_logger.propagate = True

logging.info("Logging to: %s", log_dir / MAIN_LOG_NAME)
logging.info("Process PID: %s", os.getpid())

logger = logging.getLogger(__name__)


def log_activity(event: str, message: str, *args: Any) -> None:
    """Diagnose expected logging I/O faults without hiding programming errors."""
    try:
        logging.getLogger("mcp_activity").info(f"{event}: {message}", *args)
    except (OSError, UnicodeError):
        logger.warning("Activity log write failed for %s", event, exc_info=True)


# Log PAL_MCP_FORCE_ENV_OVERRIDE configuration for transparency
if env_override_enabled():
    logger.info("PAL_MCP_FORCE_ENV_OVERRIDE enabled - .env file values will override system environment variables")
    logger.debug("Environment override prevents conflicts between different AI tools passing cached API keys")
else:
    logger.debug("PAL_MCP_FORCE_ENV_OVERRIDE disabled - system environment variables take precedence")


# Create the MCP server instance with a unique name identifier
# This name is used by MCP clients to identify and connect to this specific server
server: Server = Server("pal-server")


@server.list_resources()
async def handle_list_resources() -> list[Resource]:
    if not transcripts_enabled():
        return []
    return [
        Resource(
            uri="pal://threads",
            name="Conversation transcripts",
            mimeType="application/json",
            description="Private audit index; bounded to 100 threads, sorted by UUID.",
        )
    ]


@server.list_resource_templates()
async def handle_list_resource_templates() -> list[ResourceTemplate]:
    if not transcripts_enabled():
        return []
    return [
        ResourceTemplate(
            uriTemplate="pal://threads/{thread_id}",
            name="Conversation transcript",
            mimeType="application/json",
            description="Canonical UUID audit; bounded to 1 MiB/1000 records.",
        )
    ]


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
    try:
        result = await asyncio.to_thread(read_resource, str(uri))
    except ResourceNotFoundError as exc:
        raise McpError(ErrorData(code=-32002, message="Resource not found")) from exc
    except ResourceLimitError as exc:
        raise McpError(
            ErrorData(code=-32603, message="Transcript directory scan limit exceeded (10000 entries)")
        ) from exc
    except PermissionError as exc:
        logger.warning("Transcript resource permission denied")
        raise McpError(ErrorData(code=-32603, message="Transcript permission denied")) from exc
    except (OSError, TranscriptError, StateDirectoryError) as exc:
        logger.warning("Transcript resource unavailable (%s)", type(exc).__name__)
        raise McpError(ErrorData(code=-32603, message="Transcript storage unavailable")) from exc
    return [
        ReadResourceContents(
            content=json.dumps(result, ensure_ascii=False, allow_nan=False), mime_type="application/json"
        )
    ]


class _ResourceURIStream:
    """Validate resource URI spelling before MCP's AnyUrl normalization.

    Invalid spellings become a fixed unknown URI so the normal MCP lifecycle
    returns -32002 with its original request ID and cancellation/error handling.
    """

    def __init__(self, stream):
        self.stream = stream

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)

    async def __aiter__(self):
        async for message in self.stream:
            if not isinstance(message, Exception):
                request = message.message.root
                if isinstance(request, JSONRPCRequest) and request.method == "resources/read":
                    params = request.params
                    if isinstance(params, dict) and isinstance(params.get("uri"), str):
                        try:
                            resource_thread_id(params["uri"])
                        except ResourceNotFoundError:
                            request.params = {**params, "uri": "pal://invalid-resource"}
            yield message


# Constants for tool filtering
ESSENTIAL_TOOLS = {"version", "listmodels"}


def parse_disabled_tools_env() -> set[str]:
    """
    Parse the DISABLED_TOOLS environment variable into a set of tool names.

    Returns:
        Set of lowercase tool names to disable, empty set if none specified
    """
    disabled_tools_env = (get_env("DISABLED_TOOLS", "") or "").strip()
    if not disabled_tools_env:
        return set()
    return {t.strip().lower() for t in disabled_tools_env.split(",") if t.strip()}


def validate_disabled_tools(disabled_tools: set[str], all_tools: dict[str, Any]) -> None:
    """
    Validate the disabled tools list and log appropriate warnings.

    Args:
        disabled_tools: Set of tool names requested to be disabled
        all_tools: Dictionary of all available tool instances
    """
    essential_disabled = disabled_tools & ESSENTIAL_TOOLS
    if essential_disabled:
        logger.warning(f"Cannot disable essential tools: {sorted(essential_disabled)}")
    unknown_tools = disabled_tools - set(all_tools.keys())
    if unknown_tools:
        logger.warning(f"Unknown tools in DISABLED_TOOLS: {sorted(unknown_tools)}")


def apply_tool_filter(all_tools: dict[str, Any], disabled_tools: set[str]) -> dict[str, Any]:
    """
    Apply the disabled tools filter to create the final tools dictionary.

    Args:
        all_tools: Dictionary of all available tool instances
        disabled_tools: Set of tool names to disable

    Returns:
        Dictionary containing only enabled tools
    """
    enabled_tools = {}
    for tool_name, tool_instance in all_tools.items():
        if tool_name in ESSENTIAL_TOOLS or tool_name not in disabled_tools:
            enabled_tools[tool_name] = tool_instance
        else:
            logger.debug(f"Tool '{tool_name}' disabled via DISABLED_TOOLS")
    return enabled_tools


def log_tool_configuration(disabled_tools: set[str], enabled_tools: dict[str, Any]) -> None:
    """
    Log the final tool configuration for visibility.

    Args:
        disabled_tools: Set of tool names that were requested to be disabled
        enabled_tools: Dictionary of tools that remain enabled
    """
    if not disabled_tools:
        logger.info("All tools enabled (DISABLED_TOOLS not set)")
        return
    actual_disabled = disabled_tools - ESSENTIAL_TOOLS
    if actual_disabled:
        logger.debug(f"Disabled tools: {sorted(actual_disabled)}")
        logger.info(f"Active tools: {sorted(enabled_tools.keys())}")


def filter_disabled_tools(all_tools: dict[str, Any]) -> dict[str, Any]:
    """
    Filter tools based on DISABLED_TOOLS environment variable.

    Args:
        all_tools: Dictionary of all available tool instances

    Returns:
        dict: Filtered dictionary containing only enabled tools
    """
    disabled_tools = parse_disabled_tools_env()
    if not disabled_tools:
        log_tool_configuration(disabled_tools, all_tools)
        return all_tools
    validate_disabled_tools(disabled_tools, all_tools)
    enabled_tools = apply_tool_filter(all_tools, disabled_tools)
    log_tool_configuration(disabled_tools, enabled_tools)
    return enabled_tools


# Initialize the tool registry with all available AI-powered tools
# Each tool provides specialized functionality for different development tasks
# These instances are schema prototypes; execution gets a fresh instance per call.
ALL_TOOLS = {
    "jules": JulesTool(),  # Optional provider-free background coding actions
    "chat": ChatTool(),  # Interactive development chat and brainstorming
    "clink": CLinkTool(),  # Bridge requests to configured AI CLIs
    "thinkdeep": ThinkDeepTool(),  # Step-by-step deep thinking workflow with expert analysis
    "planner": PlannerTool(),  # Interactive sequential planner using workflow architecture
    "consensus": ConsensusTool(),  # Step-by-step consensus workflow with multi-model analysis
    "codereview": CodeReviewTool(),  # Comprehensive step-by-step code review workflow with expert analysis
    "precommit": PrecommitTool(),  # Step-by-step pre-commit validation workflow
    "debug": DebugIssueTool(),  # Root cause analysis and debugging assistance
    "secaudit": SecauditTool(),  # Comprehensive security audit with OWASP Top 10 and compliance coverage
    "docgen": DocgenTool(),  # Step-by-step documentation generation with complexity analysis
    "analyze": AnalyzeTool(),  # General-purpose file and code analysis
    "refactor": RefactorTool(),  # Step-by-step refactoring analysis workflow with expert validation
    "tracer": TracerTool(),  # Static call path prediction and control flow analysis
    "testgen": TestGenTool(),  # Step-by-step test generation workflow with expert validation
    "challenge": ChallengeTool(),  # Critical challenge prompt wrapper to avoid automatic agreement
    "apilookup": LookupTool(),  # Quick web/API lookup instructions
    "listmodels": ListModelsTool(),  # List all available AI models by provider
    "version": VersionTool(),  # Display server version and system information
}
TOOLS = filter_disabled_tools(ALL_TOOLS)

# Rich prompt templates for all tools
PROMPT_TEMPLATES = {
    "chat": {
        "name": "chat",
        "description": "Chat and brainstorm ideas",
        "template": "Chat with {model} about this",
    },
    "clink": {
        "name": "clink",
        "description": "Forward a request to a configured AI CLI (e.g., Gemini)",
        "template": "Use clink with cli_name=<cli> to run this prompt",
    },
    "thinkdeep": {
        "name": "thinkdeeper",
        "description": "Step-by-step deep thinking workflow with expert analysis",
        "template": "Start comprehensive deep thinking workflow with {model} using {thinking_mode} thinking mode",
    },
    "planner": {
        "name": "planner",
        "description": "Break down complex ideas, problems, or projects into multiple manageable steps",
        "template": "Create a detailed plan with {model}",
    },
    "consensus": {
        "name": "consensus",
        "description": "Step-by-step consensus workflow with multi-model analysis",
        "template": "Start comprehensive consensus workflow with {model}",
    },
    "codereview": {
        "name": "review",
        "description": "Perform a comprehensive code review",
        "template": "Perform a comprehensive code review with {model}",
    },
    "precommit": {
        "name": "precommit",
        "description": "Step-by-step pre-commit validation workflow",
        "template": "Start comprehensive pre-commit validation workflow with {model}",
    },
    "debug": {
        "name": "debug",
        "description": "Debug an issue or error",
        "template": "Help debug this issue with {model}",
    },
    "secaudit": {
        "name": "secaudit",
        "description": "Comprehensive security audit with OWASP Top 10 coverage",
        "template": "Perform comprehensive security audit with {model}",
    },
    "docgen": {
        "name": "docgen",
        "description": "Generate comprehensive code documentation with complexity analysis",
        "template": "Generate comprehensive documentation with {model}",
    },
    "analyze": {
        "name": "analyze",
        "description": "Analyze files and code structure",
        "template": "Analyze these files with {model}",
    },
    "refactor": {
        "name": "refactor",
        "description": "Refactor and improve code structure",
        "template": "Refactor this code with {model}",
    },
    "tracer": {
        "name": "tracer",
        "description": "Trace code execution paths",
        "template": "Generate tracer analysis with {model}",
    },
    "testgen": {
        "name": "testgen",
        "description": "Generate comprehensive tests",
        "template": "Generate comprehensive tests with {model}",
    },
    "challenge": {
        "name": "challenge",
        "description": "Challenge a statement critically without automatic agreement",
        "template": "Challenge this statement critically",
    },
    "apilookup": {
        "name": "apilookup",
        "description": "Look up the latest API or SDK information",
        "template": "Lookup latest API docs for {model}",
    },
    "listmodels": {
        "name": "listmodels",
        "description": "List available AI models",
        "template": "List all available models",
    },
    "version": {
        "name": "version",
        "description": "Show server version and system information",
        "template": "Show PAL MCP Server version",
    },
}


def configure_providers():
    """Register configured backends using their shared declarations."""
    from providers.configuration import provider_classes
    from providers.registry import ModelProviderRegistry
    from providers.shared import ProviderType
    from utils.model_restrictions import get_restriction_service

    configured = {}
    for factory in provider_classes():
        config = factory.CONFIGURATION
        # Validate explicit and shipped registries even without credentials.
        # Re-read on startup so an import-time cache cannot conceal a broken file.
        if hasattr(factory, "reload_registry"):
            factory.reload_registry()
            catalog = factory._registry
        else:
            catalog = factory.REGISTRY_CLASS()
            if config.provider_type != ProviderType.AZURE:
                factory._registry = catalog
        if not config.is_configured():
            ModelProviderRegistry.unregister_provider(config.provider_type)
            logger.debug("%s not configured; set %s", config.label, config.setup_hint())
            continue
        if config.provider_type == ProviderType.AZURE:
            if not catalog.list_models():
                logger.warning("Azure OpenAI models configuration is empty; configure AZURE_MODELS_CONFIG_PATH")
                ModelProviderRegistry.unregister_provider(config.provider_type)
                continue
        ModelProviderRegistry.register_provider(config.provider_type, factory)
        configured[config.provider_type] = ModelProviderRegistry.get_provider(config.provider_type)
        logger.info("%s configured", config.label)

    if not configured:
        hints = [
            f"- {factory.CONFIGURATION.setup_hint()} for {factory.CONFIGURATION.label}"
            for factory in provider_classes()
        ]
        logger.warning("No model providers configured. Model calls require:\n%s", "\n".join(hints))
        return configured
    get_restriction_service().validate_against_known_models(configured)

    from config import IS_AUTO_MODE

    if IS_AUTO_MODE and not ModelProviderRegistry.get_available_models(respect_restrictions=True):
        logger.warning("No models available for auto mode after restrictions; provider-free tools remain available.")
    return configured


def initialize_runtime():
    """Publish capabilities only after provider bootstrap and CLI discovery succeed."""
    from providers.configuration import provider_setup_diagnostics

    reset_runtime_capabilities()
    configured = configure_providers()
    return initialize_runtime_capabilities(
        configured_providers=configured,
        enabled_tools=TOOLS,
        provider_setup=provider_setup_diagnostics().values(),
        cli_statuses=discover_cli_availability(get_cli_registry()),
    )


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """
    List all available tools with their descriptions and input schemas.

    This handler is called by MCP clients during initialization to discover
    what tools are available. Each tool provides:
    - name: Unique identifier for the tool
    - description: Detailed explanation of what the tool does
    - inputSchema: JSON Schema defining the expected parameters

    Returns:
        List of Tool objects representing all available tools
    """
    logger.debug("MCP client requested tool list")

    # Try to log client info if available (this happens early in the handshake)
    try:
        from utils.client_info import format_client_info, get_client_info_from_context

        client_info = get_client_info_from_context(server)
        if client_info:
            formatted = format_client_info(client_info)
            logger.info(f"MCP Client Connected: {formatted}")

            # Log to activity file as well
            friendly_name = client_info.get("friendly_name", "CLI Agent")
            raw_name = client_info.get("name", "Unknown")
            version = client_info.get("version", "Unknown")
            log_activity("MCP_CLIENT_INFO", "%s (raw=%s v%s)", friendly_name, raw_name, version)
    except Exception as e:
        logger.debug(f"Could not log client info during list_tools: {e}")
    tools = []

    # Add all registered AI-powered tools from the TOOLS registry
    for tool in TOOLS.values():
        # Get optional annotations from the tool
        annotations = tool.get_annotations()
        tool_annotations = ToolAnnotations(**annotations) if annotations else None

        tools.append(
            Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.get_input_schema(),
                annotations=tool_annotations,
            )
        )

    # Log cache efficiency info
    openrouter_key_for_cache = get_env("OPENROUTER_API_KEY")
    if openrouter_key_for_cache and openrouter_key_for_cache != "your_openrouter_api_key_here":
        logger.debug("OpenRouter registry cache used efficiently across all tool schemas")

    logger.debug(f"Returning {len(tools)} tools to MCP client")
    return tools


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Coordinate continuations, letting clink own its non-waiting admission."""
    from utils.conversation_coordination import coordinate_conversation

    thread_id = arguments.get("continuation_id")
    try:
        context = server.request_context
    except LookupError:
        context = None  # Direct Python calls have no MCP request token.
    # Invalid input remains the request model's responsibility, not a lock key.
    try:
        async with request_progress(context) as progress:
            _check_call_availability(name)
            if name not in TOOLS:
                result = [TextContent(type="text", text=f"Unknown tool: {name}")]
            elif name == "clink":
                result = await _dispatch_tool(name, arguments)
            else:
                async with coordinate_conversation(thread_id if isinstance(thread_id, str) else None):
                    result = await _dispatch_tool(name, arguments)
    except ToolExecutionError as exc:
        raise ToolExecutionError(progress.annotate_error(exc.payload)) from exc
    return progress.annotate(result)


def _check_call_availability(name: str) -> None:
    """Reject disabled/provider-dependent calls before locks or conversation reads."""
    if name not in TOOLS:
        if name in ALL_TOOLS:
            raise ToolExecutionError(
                ToolOutput(
                    status="error",
                    content=f"Tool '{name}' is disabled via DISABLED_TOOLS.",
                    metadata={"tool_name": name, "error_type": "tool_disabled"},
                ).model_dump_json()
            )
        return
    runtime = get_runtime_capabilities()
    reason = runtime.unavailable_reason(name)
    if reason:
        raise ToolExecutionError(
            ToolOutput(
                status="error",
                content=reason,
                metadata={
                    "tool_name": name,
                    "error_type": "model_provider_required",
                    "available_tools": sorted(runtime.available_tools),
                },
            ).model_dump_json()
        )


async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Resolve MCP model/file policy and dispatch through the shared invocation owner.

    Tool execute wrappers validate and persist raw input once, build the receiving
    tool's context, and save its reply. Direct execute calls use the same lifecycle.
    Caller JSON cannot supply internal conversation or model-resolution state.
    """
    logger.info(f"MCP tool call: {name}")
    logger.debug(f"MCP tool arguments: {list(arguments.keys())}")

    # Log to activity file for monitoring
    log_activity("TOOL_CALL", "%s with %d arguments", name, len(arguments))

    # Admission and history belong to the same execute wrapper used by direct calls.
    # Resolve a continued model before MCP model validation, without changing explicit choices.
    arguments = {key: value for key, value in arguments.items() if not key.startswith("_")}
    if (
        arguments.get("continuation_id")
        and not arguments.get("model")
        and name in TOOLS
        and TOOLS[name].requires_model()
    ):
        from utils.conversation_memory import get_thread

        thread = get_thread(arguments["continuation_id"])
        if thread:
            for turn in reversed(thread.turns):
                if turn.role == "assistant" and turn.model_name:
                    arguments["model"] = turn.model_name
                    break

    # Route to AI-powered tools that require Gemini API calls
    if name in TOOLS:
        logger.info(f"Executing tool '{name}' with {len(arguments)} parameter(s)")
        tool = type(TOOLS[name])()

        # EARLY MODEL RESOLUTION AT MCP BOUNDARY
        # Resolve model before passing to tool - this ensures consistent model handling
        # NOTE: Consensus tool is exempt as it handles multiple models internally
        from providers.registry import ModelProviderRegistry
        from utils.model_context import ModelContext

        # Get model from arguments or use default
        model_name = arguments.get("model") or DEFAULT_MODEL
        logger.debug(f"Initial model for {name}: {model_name}")

        if not tool.requires_model():
            return await tool.execute(arguments)
        try:
            model_name, model_option = parse_model_option(model_name)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc

        # Handle auto mode at MCP boundary - resolve to specific model
        if model_name.lower() == "auto":
            # Get tool category to determine appropriate model
            tool_category = tool.get_model_category()
            try:
                resolved_model = ModelProviderRegistry.get_preferred_fallback_model(tool_category)
            except ValueError as exc:
                raise ToolExecutionError(str(exc)) from exc
            logger.info(f"Auto mode resolved to {resolved_model} for {name} (category: {tool_category.value})")
            model_name = resolved_model
            # Update arguments with resolved model
            arguments["model"] = model_name

        # Validate model availability at MCP boundary
        provider = ModelProviderRegistry.get_provider_for_model(model_name)
        if not provider:
            # Get list of available models for error message
            available_models = list(ModelProviderRegistry.get_available_models(respect_restrictions=True).keys())
            tool_category = tool.get_model_category()
            suggested_model = (
                ModelProviderRegistry.get_preferred_fallback_model(tool_category) if available_models else None
            )

            error_message = (
                f"Model '{model_name}' is not available with current API keys. "
                f"Available models: {', '.join(available_models)}. "
            )
            if suggested_model:
                error_message += f"Suggested model for {name}: '{suggested_model}' (category: {tool_category.value})"
            else:
                error_message += (
                    "Configure provider credentials and check model allowlists; use listmodels for setup hints."
                )
            error_output = ToolOutput(
                status="error",
                content=error_message,
                content_type="text",
                metadata={
                    "tool_name": name,
                    "requested_model": model_name,
                    "model_used": None,
                    "provider_used": None,
                },
            )
            raise ToolExecutionError(error_output.model_dump_json())

        # Create model context with resolved model and option
        model_context = ModelContext(model_name, model_option, provider=provider)
        arguments["model"] = model_name
        arguments["_model_context"] = model_context
        arguments["_resolved_model_name"] = model_name
        logger.debug(
            f"Model context created for {model_name} with {model_context.capabilities.context_window} token capacity"
        )
        if model_option:
            logger.debug(f"Model option stored in context: '{model_option}'")

        # File admission runs with rendering after history reconstruction, so both
        # use the same remaining allowance and rendered header/token accounting.

        # Execute tool with pre-resolved model context
        result = await tool.execute(arguments)
        logger.info(f"Tool '{name}' execution completed")

        # Log completion to activity file
        log_activity("TOOL_COMPLETED", "%s", name)
        return result

    # Handle unknown tool requests gracefully
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


def parse_model_option(model_string: str) -> tuple[str, Optional[str]]:
    """Preserve full registered IDs; accept only resolvable legacy stance options."""
    from providers.registry import ModelProviderRegistry

    requested = model_string.strip()
    if ":" not in requested:
        return requested, None
    if ModelProviderRegistry.resolve_model_identity(requested) is not None:
        return requested, None
    preceding, _, option = requested.rpartition(":")
    option = option.strip().lower()
    if option in {"for", "against", "neutral"}:
        preceding = preceding.strip()
        if ModelProviderRegistry.resolve_model_identity(preceding) is not None:
            return preceding, option
    raise ValueError(
        f"Unknown model '{requested}'. Register the exact tagged model or an explicit alias in its model registry; "
        "only :for, :against and :neutral are supported legacy options."
    )


def get_follow_up_instructions(current_turn_count: int, max_turns: int = None) -> str:
    """
    Generate dynamic follow-up instructions based on conversation turn count.

    Args:
        current_turn_count: Current number of turns in the conversation
        max_turns: Maximum allowed turns before conversation ends (defaults to MAX_CONVERSATION_TURNS)

    Returns:
        Follow-up instructions to append to the tool prompt
    """
    if max_turns is None:
        from utils.conversation_memory import MAX_CONVERSATION_TURNS

        max_turns = MAX_CONVERSATION_TURNS

    if max_turns - current_turn_count - 1 < 2:
        # We're at or approaching the turn limit - no more follow-ups
        return """
IMPORTANT: This is approaching the final exchange in this conversation thread.
Do NOT include any follow-up questions in your response. Provide your complete
final analysis and recommendations."""
    else:
        # Normal follow-up instructions
        remaining_turns = max(0, max_turns - current_turn_count - 1) // 2
        return f"""

CONVERSATION CONTINUATION: You can continue this discussion with the agent! ({remaining_turns} exchanges remaining)

Feel free to ask clarifying questions or suggest areas for deeper exploration naturally within your response.
If something needs clarification or you'd benefit from additional context, simply mention it conversationally.

IMPORTANT: When you suggest follow-ups or ask questions, you MUST explicitly instruct the agent to use the continuation_id
to respond. Use clear, direct language based on urgency:

For optional follow-ups: "Please continue this conversation using the continuation_id from this response if you'd "
"like to explore this further."

For needed responses: "Please respond using the continuation_id from this response - your input is needed to proceed."

For essential/critical responses: "RESPONSE REQUIRED: Please immediately continue using the continuation_id from "
"this response. Cannot proceed without your clarification/input."

This ensures the agent knows both HOW to maintain the conversation thread AND whether a response is optional, "
"needed, or essential.

The tool will automatically provide a continuation_id in the structured response that the agent can use in subsequent
tool calls to maintain full conversation context across multiple exchanges.

Remember: Only suggest follow-ups when they would genuinely add value to the discussion, and always instruct "
"The agent to use the continuation_id when you do."""


async def reconstruct_thread_context(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Reconstruct conversation context for stateless-to-stateful thread continuation.

    This is a critical function that transforms the inherently stateless MCP protocol into
    stateful multi-turn conversations. It loads persistent conversation state from in-memory
    storage and rebuilds complete conversation context using the sophisticated dual prioritization
    strategy implemented in the conversation memory system.

    CONTEXT RECONSTRUCTION PROCESS:

    1. THREAD RETRIEVAL: Loads complete ThreadContext from storage using continuation_id
       - Includes all conversation turns with tool attribution
       - Preserves file references and cross-tool context
       - Handles conversation chains across multiple linked threads

    2. CONVERSATION HISTORY BUILDING: Uses build_conversation_history() to create
       comprehensive context with intelligent prioritization:

       FILE PRIORITIZATION (Newest-First Throughout):
       - When same file appears in multiple turns, newest reference wins
       - File embedding prioritizes recent versions, excludes older duplicates
       - Token budget management ensures most relevant files are preserved

       CONVERSATION TURN PRIORITIZATION (Dual Strategy):
       - Collection Phase: Processes turns newest-to-oldest for token efficiency
       - Presentation Phase: Presents turns chronologically for LLM understanding
       - Ensures recent context is preserved when token budget is constrained

    3. CONTEXT INJECTION: Embeds reconstructed history into tool request arguments
       - Conversation history becomes part of the tool's prompt context
       - Files referenced in previous turns are accessible to current tool
       - Cross-tool knowledge transfer is seamless and comprehensive

    4. TOKEN BUDGET MANAGEMENT: Applies model-specific token allocation
       - Balances conversation history vs. file content vs. response space
       - Gracefully handles token limits with intelligent exclusion strategies
       - Preserves most contextually relevant information within constraints

    CROSS-TOOL CONTINUATION SUPPORT:
    This function enables seamless handoffs between different tools:
    - Analyze tool → Debug tool: Full file context and analysis preserved
    - Chat tool → CodeReview tool: Conversation context maintained
    - Any tool → Any tool: Complete cross-tool knowledge transfer

    ERROR HANDLING & RECOVERY:
    - Thread expiration: Provides clear instructions for conversation restart
    - Storage unavailability: Graceful degradation with error messaging
    - Invalid continuation_id: Security validation and user-friendly errors

    Args:
        arguments: Original request arguments dictionary containing:
                  - continuation_id (required): UUID of conversation thread to resume
                  - Other tool-specific arguments that will be preserved

    Returns:
        dict[str, Any]: Enhanced arguments dictionary with conversation context:
        - Original arguments preserved
        - Conversation history embedded in appropriate format for tool consumption
        - File context from previous turns made accessible
        - Cross-tool knowledge transfer enabled

    Raises:
        ValueError: When continuation_id is invalid, thread not found, or expired
                   Includes user-friendly recovery instructions

    Performance Characteristics:
        - O(1) thread lookup in memory
        - O(n) conversation history reconstruction where n = number of turns
        - Intelligent token budgeting prevents context window overflow
        - Optimized file deduplication minimizes redundant content

    Example Usage Flow:
        1. CLI: "Continue analyzing the security issues" + continuation_id
        2. reconstruct_thread_context() loads previous analyze conversation
        3. Debug tool receives full context including previous file analysis
        4. Debug tool can reference specific findings from analyze tool
        5. Natural cross-tool collaboration without context loss
    """
    from utils.conversation_memory import build_conversation_history, get_thread
    from utils.token_utils import estimate_tokens

    continuation_id = arguments["continuation_id"]

    # Get thread context from storage
    logger.debug(f"[CONVERSATION_DEBUG] Looking up thread {continuation_id} in storage")
    context = get_thread(continuation_id)
    if not context:
        logger.warning(f"Thread not found: {continuation_id}")
        logger.debug(f"[CONVERSATION_DEBUG] Thread {continuation_id} not found in storage or expired")

        # Log to activity file for monitoring
        log_activity("CONVERSATION_ERROR", "Thread %s not found or expired", continuation_id)

        # Return error asking CLI to restart conversation with full context
        raise ValueError(
            f"Conversation thread '{continuation_id}' was not found or has expired. "
            f"This may happen if the conversation was created more than 3 hours ago or if the "
            f"server was restarted. "
            f"Please restart the conversation by providing your full question/prompt without the "
            f"continuation_id parameter. "
            f"This will create a new conversation thread that can continue with follow-up exchanges."
        )

    # Create model context early to use for history building
    from utils.model_context import ModelContext

    tool = TOOLS.get(context.tool_name)
    requires_model = tool.requires_model() if tool else True

    # Check if we should use the model from the previous conversation turn
    model_from_args = arguments.get("model")
    if requires_model and not model_from_args and context.turns:
        # Find the last assistant turn to get the model used
        for turn in reversed(context.turns):
            if turn.role == "assistant" and turn.model_name:
                arguments["model"] = turn.model_name
                logger.debug(f"[CONVERSATION_DEBUG] Using model from previous turn: {turn.model_name}")
                break

    # Resolve an effective model for context reconstruction when DEFAULT_MODEL=auto
    model_context = arguments.get("_model_context")

    if requires_model:
        if model_context is None:
            try:
                model_context = ModelContext.from_arguments(arguments)
                arguments.setdefault("_resolved_model_name", model_context.model_name)
            except ValueError as exc:
                from providers.registry import ModelProviderRegistry

                fallback_model = None
                if tool is not None:
                    try:
                        fallback_model = ModelProviderRegistry.get_preferred_fallback_model(tool.get_model_category())
                    except Exception as fallback_exc:  # pragma: no cover - defensive log
                        logger.debug(
                            f"[CONVERSATION_DEBUG] Unable to resolve fallback model for {context.tool_name}: {fallback_exc}"
                        )

                if fallback_model is None:
                    available_models = ModelProviderRegistry.get_available_model_names()
                    if available_models:
                        fallback_model = available_models[0]

                if fallback_model is None:
                    raise

                logger.debug(
                    f"[CONVERSATION_DEBUG] Falling back to model '{fallback_model}' for context reconstruction after error: {exc}"
                )
                model_context = ModelContext(fallback_model)
                arguments["_model_context"] = model_context
                arguments["_resolved_model_name"] = fallback_model

        from providers.registry import ModelProviderRegistry

        provider = ModelProviderRegistry.get_provider_for_model(model_context.model_name)
        if provider is None:
            fallback_model = None
            if tool is not None:
                try:
                    fallback_model = ModelProviderRegistry.get_preferred_fallback_model(tool.get_model_category())
                except Exception as fallback_exc:  # pragma: no cover - defensive log
                    logger.debug(
                        f"[CONVERSATION_DEBUG] Unable to resolve fallback model for {context.tool_name}: {fallback_exc}"
                    )

            if fallback_model is None:
                available_models = ModelProviderRegistry.get_available_model_names()
                if available_models:
                    fallback_model = available_models[0]

            if fallback_model is None:
                raise ValueError(
                    f"Conversation continuation failed: model '{model_context.model_name}' is not available with current API keys."
                )

            logger.debug(
                f"[CONVERSATION_DEBUG] Model '{model_context.model_name}' unavailable; swapping to '{fallback_model}' for context reconstruction"
            )
            model_context = ModelContext(fallback_model)
            arguments["_model_context"] = model_context
            arguments["_resolved_model_name"] = fallback_model
    else:
        if model_context is None:
            from providers.registry import ModelProviderRegistry

            fallback_model = None
            if tool is not None:
                try:
                    fallback_model = ModelProviderRegistry.get_preferred_fallback_model(tool.get_model_category())
                except Exception as fallback_exc:  # pragma: no cover - defensive log
                    logger.debug(
                        f"[CONVERSATION_DEBUG] Unable to resolve fallback model for {context.tool_name}: {fallback_exc}"
                    )

            if fallback_model is None:
                available_models = ModelProviderRegistry.get_available_model_names()
                if available_models:
                    fallback_model = available_models[0]

            if fallback_model is None:
                raise ValueError(
                    "Conversation continuation failed: no available models detected for context reconstruction."
                )

            logger.debug(
                f"[CONVERSATION_DEBUG] Using fallback model '{fallback_model}' for context reconstruction of tool without model requirement"
            )
            model_context = ModelContext(fallback_model)
            arguments["_model_context"] = model_context
            arguments["_resolved_model_name"] = fallback_model

    # Build conversation history with model-specific limits
    logger.debug(f"[CONVERSATION_DEBUG] Building conversation history for thread {continuation_id}")
    logger.debug(f"[CONVERSATION_DEBUG] Thread has {len(context.turns)} turns, tool: {context.tool_name}")
    logger.debug(f"[CONVERSATION_DEBUG] Using model: {model_context.model_name}")
    conversation_history, conversation_tokens = build_conversation_history(context, model_context)
    logger.debug(f"[CONVERSATION_DEBUG] Conversation history built: {conversation_tokens:,} tokens")
    logger.debug(
        f"[CONVERSATION_DEBUG] Conversation history length: {len(conversation_history)} chars (~{conversation_tokens:,} tokens)"
    )

    # Add dynamic follow-up instructions based on turn count
    follow_up_instructions = get_follow_up_instructions(len(context.turns))
    logger.debug(f"[CONVERSATION_DEBUG] Follow-up instructions added for turn {len(context.turns)}")

    # All tools now use standardized 'prompt' field
    original_prompt = arguments.get("prompt", "")
    logger.debug("[CONVERSATION_DEBUG] Extracting user input from 'prompt' field")
    original_prompt_tokens = estimate_tokens(original_prompt) if original_prompt else 0
    logger.debug(
        f"[CONVERSATION_DEBUG] User input length: {len(original_prompt)} chars (~{original_prompt_tokens:,} tokens)"
    )

    # Merge original context with new prompt and follow-up instructions
    if conversation_history:
        enhanced_prompt = (
            f"{conversation_history}\n\n=== NEW USER INPUT ===\n{original_prompt}\n\n{follow_up_instructions}"
        )
    else:
        enhanced_prompt = f"{original_prompt}\n\n{follow_up_instructions}"

    # Update arguments with enhanced context and remaining token budget
    enhanced_arguments = arguments.copy()

    # Store the enhanced prompt in the prompt field
    enhanced_arguments["prompt"] = enhanced_prompt
    # Store the original user prompt separately for size validation
    enhanced_arguments["_original_user_prompt"] = original_prompt
    logger.debug("[CONVERSATION_DEBUG] Storing enhanced prompt in 'prompt' field")
    logger.debug("[CONVERSATION_DEBUG] Storing original user prompt in '_original_user_prompt' field")

    # Calculate remaining token budget based on current model
    # (model_context was already created above for history building)
    token_allocation = model_context.calculate_token_allocation()

    # Calculate remaining tokens for files/new content
    # History has already consumed some of the content budget
    remaining_tokens = token_allocation.content_tokens - conversation_tokens
    enhanced_arguments["_remaining_tokens"] = max(0, remaining_tokens)  # Ensure non-negative
    enhanced_arguments["_model_context"] = model_context  # Pass context for use in tools

    logger.debug("[CONVERSATION_DEBUG] Token budget calculation:")
    logger.debug(f"[CONVERSATION_DEBUG]   Model: {model_context.model_name}")
    logger.debug(f"[CONVERSATION_DEBUG]   Total capacity: {token_allocation.total_tokens:,}")
    logger.debug(f"[CONVERSATION_DEBUG]   Content allocation: {token_allocation.content_tokens:,}")
    logger.debug(f"[CONVERSATION_DEBUG]   Conversation tokens: {conversation_tokens:,}")
    logger.debug(f"[CONVERSATION_DEBUG]   Remaining tokens: {remaining_tokens:,}")

    # Merge original context parameters (files, etc.) with new request
    if context.initial_context:
        logger.debug(f"[CONVERSATION_DEBUG] Merging initial context with {len(context.initial_context)} parameters")
        for key, value in context.initial_context.items():
            if key not in enhanced_arguments and key not in ["temperature", "thinking_mode", "model"]:
                enhanced_arguments[key] = value
                logger.debug(f"[CONVERSATION_DEBUG] Merged initial context param: {key}")

    logger.info(f"Reconstructed context for thread {continuation_id} (turn {len(context.turns)})")
    logger.debug(f"[CONVERSATION_DEBUG] Final enhanced arguments keys: {list(enhanced_arguments.keys())}")

    if "absolute_file_paths" in enhanced_arguments:
        logger.debug(
            f"[CONVERSATION_DEBUG] Final files in enhanced arguments: {enhanced_arguments['absolute_file_paths']}"
        )

    # Log to activity file for monitoring
    log_activity(
        "CONVERSATION_CONTINUATION",
        "Thread %s turn %d - %d previous turns loaded",
        continuation_id,
        len(context.turns),
        len(context.turns),
    )

    return enhanced_arguments


@server.list_prompts()
async def handle_list_prompts() -> list[Prompt]:
    """
    List all available prompts for CLI Code shortcuts.

    This handler returns prompts that enable shortcuts like /pal:thinkdeeper.
    We automatically generate prompts from all tools (1:1 mapping) plus add
    a few marketing aliases with richer templates for commonly used tools.

    Returns:
        List of Prompt objects representing all available prompts
    """
    logger.debug("MCP client requested prompt list")
    prompts = []

    # Add a prompt for each tool with rich templates
    for tool_name, tool in TOOLS.items():
        if tool_name in PROMPT_TEMPLATES:
            # Use the rich template
            template_info = PROMPT_TEMPLATES[tool_name]
            prompts.append(
                Prompt(
                    name=template_info["name"],
                    description=template_info["description"],
                    arguments=[],  # MVP: no structured args
                )
            )
        else:
            # Fallback for any tools without templates (shouldn't happen)
            prompts.append(
                Prompt(
                    name=tool_name,
                    description=f"Use {tool.name} tool",
                    arguments=[],
                )
            )

    # Add special "continue" prompt
    prompts.append(
        Prompt(
            name="continue",
            description="Continue the previous conversation using the chat tool",
            arguments=[],
        )
    )

    logger.debug(f"Returning {len(prompts)} prompts to MCP client")
    return prompts


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, Any] = None) -> GetPromptResult:
    """
    Get prompt details and generate the actual prompt text.

    This handler is called when a user invokes a prompt (e.g., /pal:thinkdeeper or /pal:chat:gpt5).
    It generates the appropriate text that CLI will then use to call the
    underlying tool.

    Supports structured prompt names like "chat:gpt5" where:
    - "chat" is the tool name
    - "gpt5" is the model to use

    Args:
        name: The name of the prompt to execute (can include model like "chat:gpt5")
        arguments: Optional arguments for the prompt (e.g., model, thinking_mode)

    Returns:
        GetPromptResult with the prompt details and generated message

    Raises:
        ValueError: If the prompt name is unknown
    """
    logger.debug(f"MCP client requested prompt: {name} with args: {arguments}")

    # Handle special "continue" case
    if name.lower() == "continue":
        # This is "/pal:continue" - use chat tool as default for continuation
        tool_name = "chat"
        template_info = {
            "name": "continue",
            "description": "Continue the previous conversation",
            "template": "Continue the conversation",
        }
        logger.debug("Using /pal:continue - defaulting to chat tool")
    else:
        # Find the corresponding tool by checking prompt names
        tool_name = None
        template_info = None

        # Check if it's a known prompt name
        for t_name, t_info in PROMPT_TEMPLATES.items():
            if t_info["name"] == name:
                tool_name = t_name
                template_info = t_info
                break

        # If not found, check if it's a direct tool name
        if not tool_name and name in TOOLS:
            tool_name = name
            template_info = {
                "name": name,
                "description": f"Use {name} tool",
                "template": f"Use {name}",
            }

        if not tool_name:
            logger.error(f"Unknown prompt requested: {name}")
            raise ValueError(f"Unknown prompt: {name}")

    # Get the template
    template = template_info.get("template", f"Use {tool_name}")

    # Safe template expansion with defaults
    final_model = arguments.get("model", "auto") if arguments else "auto"

    prompt_args = {
        "model": final_model,
        "thinking_mode": arguments.get("thinking_mode", "medium") if arguments else "medium",
    }

    logger.debug(f"Using model '{final_model}' for prompt '{name}'")

    # Safely format the template
    try:
        prompt_text = template.format(**prompt_args)
    except KeyError as e:
        logger.warning(f"Missing template argument {e} for prompt {name}, using raw template")
        prompt_text = template  # Fallback to raw template

    # Generate tool call instruction
    if name.lower() == "continue":
        # "/pal:continue" case
        tool_instruction = (
            f"Continue the previous conversation using the {tool_name} tool. "
            "CRITICAL: You MUST provide the continuation_id from the previous response to maintain conversation context. "
            "Additionally, you should reuse the same model that was used in the previous exchange for consistency, unless "
            "the user specifically asks for a different model name to be used."
        )
    else:
        # Simple prompt case
        tool_instruction = prompt_text

    return GetPromptResult(
        prompt=Prompt(
            name=name,
            description=template_info["description"],
            arguments=[],
        ),
        messages=[
            PromptMessage(
                role="user",
                content={"type": "text", "text": tool_instruction},
            )
        ],
    )


async def _serve():
    """
    Main entry point for the MCP server.

    Initializes the Gemini API configuration and starts the server using
    stdio transport. The server will continue running until the client
    disconnects or an error occurs.

    The server communicates via standard input/output streams using the
    MCP protocol's JSON-RPC message format.
    """
    # Validate and configure providers based on available API keys
    initialize_runtime()

    # Audit retention is optional and cannot prevent startup or recover memory.
    await asyncio.to_thread(prune_transcripts)

    # Log startup message
    logger.info("PAL MCP Server starting up...")
    logger.info(f"Log level: {log_level}")

    # Note: MCP client info will be logged during the protocol handshake
    # (when handle_list_tools is called)

    # Log current model mode
    from config import IS_AUTO_MODE

    if IS_AUTO_MODE:
        logger.info("Model mode: AUTO (CLI will select the best model for each task)")
    else:
        logger.info(f"Model mode: Fixed model '{DEFAULT_MODEL}'")

    # Import here to avoid circular imports
    from config import DEFAULT_THINKING_MODE_THINKDEEP

    logger.info(f"Default thinking mode (ThinkDeep): {DEFAULT_THINKING_MODE_THINKDEEP}")

    logger.info(f"Available tools: {list(TOOLS.keys())}")
    logger.info("Server ready - waiting for tool requests...")

    # Prepare dynamic instructions for the MCP client based on model mode
    if IS_AUTO_MODE:
        handshake_instructions = (
            "When the user names a specific model (e.g. 'use chat with gpt5'), send that exact model in the tool call. "
            "When no model is mentioned, first use the `listmodels` tool from PAL to obtain available models to choose the best one from."
        )
    else:
        handshake_instructions = (
            "When the user names a specific model (e.g. 'use chat with gpt5'), send that exact model in the tool call. "
            f"When no model is mentioned, default to '{DEFAULT_MODEL}'."
        )

    # Run the server using stdio transport (standard input/output)
    # This allows the server to be launched by MCP clients as a subprocess
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            _ResourceURIStream(read_stream),
            write_stream,
            InitializationOptions(
                server_name="PAL",
                server_version=__version__,
                instructions=handshake_instructions,
                capabilities=ServerCapabilities(
                    tools=ToolsCapability(),  # Advertise tool support capability
                    prompts=PromptsCapability(),  # Advertise prompt support capability
                    resources=ResourcesCapability(),  # No subscriptions/list-change notifications.
                ),
            ),
        )


async def _close_providers() -> None:
    """Attempt every allocated provider once and retain the first cleanup failure."""
    first_error = None
    seen = set()
    for provider in list(ModelProviderRegistry()._initialized_providers.values()):
        if id(provider) in seen:
            continue
        seen.add(id(provider))
        try:
            await asyncio.wait_for(provider.aclose(), timeout=30.0)
        except (Exception, asyncio.CancelledError) as exc:
            logger.error("Provider cleanup failed: %s", type(provider).__name__, exc_info=True)
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


async def main():
    """Run stdio and join provider cleanup before the owning event loop exits."""
    try:
        await _serve()
    finally:
        primary_error = sys.exc_info()[1]
        cleanup = asyncio.create_task(_close_providers())
        cancelled = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                if not cleanup.cancelled():
                    cancelled = exc
            except Exception:
                break  # Observe the cleanup failure below, after all providers ran.
        try:
            cleanup.result()
        except (Exception, asyncio.CancelledError):
            if primary_error is None and cancelled is None:
                raise
            logger.error("Provider cleanup failed during server termination", exc_info=True)
        if cancelled is not None:
            raise cancelled


def run():
    """Console script entry point for pal-mcp-server."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle graceful shutdown
        pass


if __name__ == "__main__":
    run()

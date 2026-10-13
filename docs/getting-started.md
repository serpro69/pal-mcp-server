# Getting Started with PAL MCP Server

This guide walks you through setting up the PAL MCP Server from scratch, including installation, configuration, and first usage.

## Prerequisites

- **Python 3.10+** (3.12 recommended)
- **Git**
- **[uv installed](https://docs.astral.sh/uv/getting-started/installation/)** (for uvx method)
- **Windows users**: use native PowerShell setup or run Bash setup inside WSL; install and authenticate the selected CLI in the same environment as PAL. Claude Code supports both [native Windows and WSL](https://code.claude.com/docs/en/setup#set-up-on-windows).

## Step 1: Choose CLI Conversations or API-backed Tools

PAL can start without a model provider. Choose an installed, authenticated CLI for conversations through `clink`, or configure an API provider for tools such as `chat`, `consensus`, and `codereview`. Both paths can coexist; clink always invokes the selected CLI and never falls back to an API provider.

### CLI conversations without PAL provider keys

1. Install Codex, Claude Code, or Gemini CLI separately and sign in as the OS user who will run PAL. Follow the [vendor authentication guidance](tools/clink.md#authentication-and-version-assumptions); PAL does not install or log in to these CLIs.
2. Install PAL using either method in Step 2. For a clone, `./run-server.sh` sets up the Python environment and MCP integration. Your MCP client launches the server; the setup script does not run a CLI login check.
3. Leave PAL model-provider credentials and endpoints absent or empty in the launch environment, MCP configuration, and `.env`. Remove the API-key entries from the example MCP configurations below. A configured custom/local endpoint also enables a PAL provider, even if it needs no key. Shipped placeholders count as absent; do not add a dummy key to enable startup.
4. Make the selected executable available on the **MCP server's** `PATH` or set its explicit command path in a [CLI manifest](tools/clink.md#configuration). Preserve access to the same home/authentication location used when signing in. Use actual absolute directories in client `PATH` settings; literal `~` or `$HOME` values in JSON/TOML environment fields may not be expanded by the client.
5. Restart PAL and ask it to run `listmodels`. Provider-free diagnostics list available independent tools, missing/incomplete provider settings, and whether configured CLI executables were found at startup. Authentication remains unverified until use.
6. Make a first clink request with `cli_name`, then follow up using the returned PAL continuation ID. Use the [request examples](tools/clink.md#first-request-and-follow-up) below the setup guidance.

For example, after clone setup, a local MCP entry can launch the generated Python environment without provider keys (replace both paths):

```json
{
  "mcpServers": {
    "pal": {
      "command": "/absolute/path/to/pal-mcp-server/.pal_venv/bin/python",
      "args": ["/absolute/path/to/pal-mcp-server/server.py"]
    }
  }
}
```

This entry still inherits the client's environment, and PAL may read its `.env`; omitting keys here does not clear keys configured elsewhere. By default, inherited variables take precedence; `.env` can enable `PAL_MCP_FORCE_ENV_OVERRIDE` to make its settings authoritative for PAL. Ensure neither source configures a provider if you want provider-free mode. See the [configuration guide](configuration.md). CLI authentication and billing remain controlled by the vendor CLI and its own settings.

With no registered PAL model provider, the enabled subset of `clink`, `version`, `listmodels`, `planner`, `tracer`, `docgen`, `challenge`, `apilookup`, and `jules` remains available. Some are disabled in the default tool configuration; Jules needs its own service credentials. API-backed tools are hidden and direct calls return a provider-required error. Startup still succeeds without an installed CLI or with clink disabled. Existing bootstrap configuration errors remain errors, rather than triggering a fallback mode.

### API-backed tools

Configure at least one provider for API-backed tools. Cloud providers generally need a key; a custom/local endpoint may not. Choose based on your needs:

### Option A: OpenRouter (Recommended for beginners)
**One API for multiple models**
- Visit [OpenRouter](https://openrouter.ai/) and sign up
- Generate an API key
- Control spending limits in your dashboard
- Access GPT-4, Claude, Gemini, and more through one API

### Option B: Native Provider APIs

**Gemini (Google):**
- Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
- Generate an API key
- **Note**: For Gemini 3.0 / 2.5 Pro, use a paid API key (free tier has limited access)

**OpenAI:**
- Visit [OpenAI Platform](https://platform.openai.com/api-keys)
- Generate an API key for GPT-5.2, GPT-5.1-Codex, GPT-5, O3 access

**X.AI (Grok):**
- Visit [X.AI Console](https://console.x.ai/)
- Generate an API key for Grok models

**DIAL Platform:**
- Visit [DIAL Platform](https://dialx.ai/)
- Generate API key for vendor-agnostic model access

### Option C: Local Models (Free)

**Ollama:**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Start Ollama service
ollama serve

# Pull a model (e.g., Llama 3.2)
ollama pull llama3.2
```

**Other local options:**
- **vLLM**: Self-hosted inference server
- **LM Studio**: Local model hosting with OpenAI-compatible API
- **Text Generation WebUI**: Popular local interface

👉 **[Complete custom model setup guide](custom_models.md)**

## Step 2: Installation

Choose your preferred installation method:

### Method A: Instant Setup with uvx (Recommended)

**Prerequisites**: [Install uv first](https://docs.astral.sh/uv/getting-started/installation/)

Choose your AI coding assistant and add the corresponding configuration:

The examples below show API-backed setup. For CLI-only conversations, omit provider keys and endpoints while retaining the launcher and executable lookup settings described [above](#cli-conversations-without-pal-provider-keys).

**For Claude Desktop:**
1. Open Claude Desktop → Settings → Developer → Edit Config
2. Add this configuration:

```json
{
  "mcpServers": {
    "pal": {
      "command": "sh",
      "args": [
        "-c", 
        "for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x \"$p\" ] && exec \"$p\" --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1"
      ],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:~/.local/bin",
        "GEMINI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

**For Claude Code CLI:**
Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "pal": {
      "command": "sh", 
      "args": [
        "-c",
        "for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x \"$p\" ] && exec \"$p\" --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1"
      ],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:~/.local/bin",
        "GEMINI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

**For Gemini CLI:**
Edit `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "pal": {
      "command": "sh",
      "args": [
        "-c",
        "for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x \"$p\" ] && exec \"$p\" --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1"  
      ],
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:~/.local/bin",
        "GEMINI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

**For Codex CLI:**
Edit `~/.codex/config.toml`:

```toml
[mcp_servers.pal]
command = "bash"
args = ["-c", "for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x \\\"$p\\\" ] && exec \\\"$p\\\" --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1"]
tool_timeout_sec = 1200  # 20 minutes; added automatically by the setup script so upstream providers can respond

[mcp_servers.pal.env]
PATH = "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$HOME/.local/bin:$HOME/.cargo/bin:$HOME/bin"
GEMINI_API_KEY = "your_api_key_here"
```

Enable Codex's built-in web-search tool so PAL's `apilookup` instructions can execute successfully:

```toml
[tools]
web_search = true
```

Add the block above if `[tools]` is missing from the file; otherwise ensure `web_search = true` appears in that section.


**For Qwen Code CLI:**
Create or edit `~/.qwen/settings.json`:

```json
{
  "mcpServers": {
    "pal": {
      "command": "bash",
      "args": [
        "-c",
        "for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x \"$p\" ] && exec \"$p\" --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1"
      ],
      "cwd": "/path/to/pal-mcp-server",
      "env": {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:~/.local/bin",
        "GEMINI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

For API-backed tools, replace the placeholder API key with the providers you use (Gemini, OpenAI, OpenRouter, etc.). For provider-free clink, omit that key entry.

**For OpenCode CLI:**
Edit `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "pal": {
      "type": "local",
      "command": [
        "/path/to/pal-mcp-server/.pal_venv/bin/python",
        "/path/to/pal-mcp-server/server.py"
      ],
      "cwd": "/path/to/pal-mcp-server",
      "enabled": true,
      "environment": {
        "GEMINI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

For API-backed tools, add any other provider keys you use (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, etc.). For provider-free clink, omit the key entry.

#### IDE Clients (Cursor & VS Code)

PAL works in GUI IDEs that speak MCP. The configuration mirrors the CLI examples above—point the client at the `uvx` launcher and set any required environment variables.

**Cursor IDE**

1. Open Cursor → `Settings` (`Cmd+,`/`Ctrl+,`) → **Integrations › Model Context Protocol (MCP)**.
2. Click **Add MCP Server** and supply the following values:
   - Command: `sh`
   - Args: `-c` and `for p in $(which uvx 2>/dev/null) $HOME/.local/bin/uvx /opt/homebrew/bin/uvx /usr/local/bin/uvx uvx; do [ -x "$p" ] && exec "$p" --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server; done; echo 'uvx not found' >&2; exit 1`
   - Environment (example):
     - `PATH=/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:~/.local/bin`
     - `GEMINI_API_KEY=your_api_key_here`
3. Save the configuration—Cursor will launch the MCP server on demand. See the [Cursor MCP guide](https://cursor.com/docs) for screenshots of the UI.

**Visual Studio Code (Claude Dev extension)**

1. Install the [Claude Dev extension](https://marketplace.visualstudio.com/items?itemName=Anthropic.claude-vscode) v0.6.0 or later.
2. Open the Command Palette (`Cmd+Shift+P`/`Ctrl+Shift+P`) → **Claude: Configure MCP Servers** → **Add server**.
3. When prompted, use the same values as above:
   - Command: `sh`
   - Args: `-c` and the `uvx` bootstrap loop
   - Environment: add the API keys you need (e.g. `GEMINI_API_KEY`, `OPENAI_API_KEY`)
4. Save the JSON snippet the extension generates. VS Code will reload the server automatically the next time you interact with Claude.

👉 Pro tip: If you prefer a one-line command, replace the long loop with `uvx --from git+https://github.com/serpro69/pal-mcp-server.git pal-mcp-server`—just make sure `uvx` is on your PATH for every client.

**Benefits of uvx method:**
- ✅ Zero manual setup required
- ✅ Always pulls latest version
- ✅ No local dependencies to manage
- ✅ Works without Python environment setup

### Method B: Clone and Setup

```bash
# Clone the repository
git clone https://github.com/serpro69/pal-mcp-server.git
cd pal-mcp-server

# One-command setup (handles everything)
./run-server.sh

# Or for Windows PowerShell:
./run-server.ps1

# View configuration for Claude Desktop
./run-server.sh -c

# See all options
./run-server.sh --help
```

**What the setup script does:**
- ✅ Creates Python virtual environment
- ✅ Installs all dependencies  
- ✅ Creates .env for optional provider settings and server configuration
- ✅ Configures Claude integrations
- ✅ Provides copy-paste configuration

**After updates:** Always run `./run-server.sh` again after `git pull`.

### Windows CLI setup

For local Windows CLIs, run `.\run-server.ps1` from the checkout in PowerShell (the script requires PowerShell 5.1 or later). Use its native Python workflow, then restart your MCP client. Install and authenticate the chosen vendor CLI under the Windows account that launches PAL, and make its executable available on that process's `PATH` or through an explicit CLI manifest command. Use Windows absolute paths for request files and `working_dir`; the generated Python launcher is `.pal_venv\Scripts\python.exe`.

Both PowerShell setup branches create a new `.env` with provider credentials and endpoints commented out. Leave them unset for provider-free clink, or uncomment only the API settings you intend to use. Existing files are preserved: older generated files may still contain `CUSTOM_API_URL=your_custom_api_url_here`, which PAL treats as absent. Remove or comment unused placeholder keys in those files too, especially `ANTHROPIC_API_KEY`, because child CLIs can inherit them. A real custom endpoint remains configured, and inherited provider settings still count; commenting a file entry does not clear a process environment variable. The PowerShell launcher imports active `.env` assignments into its process, so those assignments replace same-named inherited values.

For WSL, install/run PAL and the selected CLI inside the same distribution and use `./run-server.sh` with Linux paths. Do not assume a Windows CLI login or `PATH` is available inside WSL. See the [WSL Setup Guide](wsl-setup.md) for that setup path and the vendor's [current Windows requirements](https://code.claude.com/docs/en/setup#set-up-on-windows).

The `-Docker` path needs the selected CLI executable and its authentication **inside the container**. It does not automatically reuse the host's installation or login. Native Python setup is the local CLI path described here. Neither setup branch installs vendor CLIs or probes their login state; `listmodels` reports executable observations with authentication unverified.

## Step 3: Configure the Selected Setup Path

For provider-free clink, keep PAL model-provider keys/endpoints unconfigured as described in [Step 1](#cli-conversations-without-pal-provider-keys), and skip the API examples in this step. Login to the selected CLI is a separate prerequisite; an empty PAL provider configuration does not choose the child's authentication method.

For API-backed tools, configure the providers you intend to use:

### For uvx installation:
Add your API keys directly to the MCP configuration shown above.

### For clone installation:
Edit the `.env` file:

```bash
nano .env
```

Add the settings for your chosen provider only; do not copy every example endpoint/key into a CLI-only setup:
```env
# Cloud provider examples (choose one or more for API-backed tools)
GEMINI_API_KEY=your-gemini-api-key-here      # For Gemini models  
OPENAI_API_KEY=your-openai-api-key-here      # For GPT-5.2, GPT-5.1-Codex, O3
XAI_API_KEY=your-xai-api-key-here            # For Grok models
OPENROUTER_API_KEY=your-openrouter-key       # For multiple models

# DIAL Platform (optional)
DIAL_API_KEY=your-dial-api-key-here
DIAL_API_HOST=https://core.dialx.ai          # Default host (optional)
DIAL_API_VERSION=2024-12-01-preview          # API version (optional) 
DIAL_ALLOWED_MODELS=o3,gemini-2.5-pro       # Restrict models (optional)

# Custom/Local models (Ollama, vLLM, etc.)
CUSTOM_API_URL=http://localhost:11434/v1     # Ollama example
CUSTOM_API_KEY=                              # Empty for Ollama
CUSTOM_MODEL_NAME=llama3.2                   # Default model name
```

## Prevent Client Timeouts

Some MCP clients default to short timeouts and can disconnect from PAL during long tool runs. Configure each client with a generous ceiling (we recommend at least five minutes); the PAL setup script now writes a 20-minute tool timeout for Codex so upstream providers contacted by the server have time to respond.

### Claude Code & Claude Desktop

Claude reads MCP-related environment variables either from your shell or from `~/.claude/settings.json`. Add (or update) the `env` block so both startup and tool execution use a 5-minute limit:

```json
{
  "env": {
    "MCP_TIMEOUT": "300000",
    "MCP_TOOL_TIMEOUT": "300000"
  }
}
```

You can scope this block at the top level of `settings.json` (applies to every session) or under a specific `mcpServers.<name>.env` entry if you only want it for PAL (the server name may still be `pal` while configurations catch up). The values are in milliseconds. Note: Claude’s SSE transport still enforces an internal ceiling of roughly five minutes; long-running HTTP/SSE servers may need retries until Anthropic ships their fix.

### Codex CLI

Codex exposes per-server timeouts in `~/.codex/config.toml`. Add (or bump) these keys under `[[mcp_servers.<name>]]`:

```toml
[mcp_servers.pal]
command = "..."
args = ["..."]
startup_timeout_sec = 300    # default is 10 seconds
tool_timeout_sec = 1200      # default is 60 seconds; setup script pre-populates 20 minutes so upstream providers can respond
```

`startup_timeout_sec` covers the initial handshake/list tools step, while `tool_timeout_sec` governs each tool call. Raise the latter if the providers your MCP server invokes routinely need more than 20 minutes.

### Gemini CLI

Gemini uses a single `timeout` field per server inside `~/.gemini/settings.json`. Set it to at least five minutes (values are milliseconds):

```json
{
  "mcpServers": {
    "pal": {
      "command": "uvx",
      "args": ["pal-mcp-server"],
      "timeout": 300000
    }
  }
}
```

Versions 0.2.1 and newer currently ignore values above ~60 seconds for some transports due to a known regression; if you still see premature disconnects we recommend breaking work into smaller calls or watching the Gemini CLI release notes for the fix.

**Important notes:**
- ⭐ **Restart PAL after configuration changes** - provider availability and CLI configuration are loaded at startup; process-local conversation IDs are lost on restart
- ⭐ If multiple APIs configured, native APIs take priority over OpenRouter
- ⭐ Configure model aliases in [`conf/custom_models.json`](../conf/custom_models.json)

## Step 4: Test the Installation

For either path, start with `"Use pal to list available models"`. In provider-free mode this reports setup and CLI executable diagnostics instead of API models. Then ask `"Use clink with cli_name=codex to remember the codeword ORCHID-42"`, substituting your installed CLI. Ask a dependent follow-up through clink with the returned `continuation_offer.continuation_id`; see the [exact tool arguments](tools/clink.md#first-request-and-follow-up). Executable discovery alone does not prove authentication or a successful conversation.

### For Claude Desktop:
1. Restart Claude Desktop
2. Open a new conversation
3. Try: `"Use pal to list available models"`

### For Claude Code CLI:
1. Exit any existing Claude session
2. Run `claude` from your project directory  
3. Try: `"Use pal to list available models"`, then the clink example above or an API-backed chat if a provider is configured

### For Gemini CLI:
**Note**: While PAL MCP connects to Gemini CLI, tool invocation isn't working correctly yet. See [Gemini CLI Setup](gemini-setup.md) for updates.

### For Qwen Code CLI:
1. Restart the Qwen Code CLI if it's running (`qwen exit`).
2. Run `qwen mcp list --scope user` and confirm `pal` shows `CONNECTED`.
3. Try: `"/mcp"` to inspect available tools or `"Use pal to analyze this repo"`.

### For OpenCode CLI:
1. Restart OpenCode (or run `OpenCode: Reload Config`).
2. Open **Settings › Tools › MCP** and confirm `pal` is enabled.
3. Start a new chat and try: `"Use pal to list available models"`.

### For Codex CLI:
1. Restart Codex CLI if running
2. Open a new conversation
3. Try: `"Use pal to list available models"`

### API-backed Test Commands:
```
"Use pal to list available models"
"Chat with pal about the best approach for API design"
"Use pal thinkdeep with gemini pro about scaling strategies"  
"Debug this error with o3: [paste error]"
```

**Note**: Codex CLI provides excellent MCP integration with automatic environment variable configuration when using the setup script.

## Step 5: Start Using PAL

For CLI conversations, use [clink's first-request and follow-up examples](tools/clink.md#first-request-and-follow-up). Edit permission must be supplied on each request; it is never inherited. History is bounded, expires by default after three hours without a successful recorded exchange, and is lost on restart. See [permissions](tools/clink.md#edit-permissions), [limits](tools/clink.md#conversation-limits), and [failure handling](tools/clink.md#diagnostics-and-failures).

The model-selection and multi-model examples below require configured PAL model providers. Provider-independent workflows remain available according to the enabled tools reported by `listmodels`.

### Basic Usage Patterns:

**Let Claude pick the model:**
```
"Use pal to analyze this code for security issues"
"Debug this race condition with pal"
"Plan the database migration with pal"
```

**Specify the model:**
```  
"Use pal with gemini pro to review this complex algorithm"
"Debug with o3 using pal for logical analysis"
"Get flash to quickly format this code via pal"
```

**Multi-model workflows:**
```
"Use pal to get consensus from pro and o3 on this architecture"
"Code review with gemini, then precommit validation with o3"  
"Analyze with flash, then deep dive with pro if issues found"
```

### Quick Tool Reference:

**🤝 Collaboration**: `chat`, `thinkdeep`, `planner`, `consensus`
**🔍 Code Analysis**: `analyze`, `codereview`, `debug`, `precommit`  
**⚒️ Development**: `refactor`, `testgen`, `secaudit`, `docgen`
**🔧 Utilities**: `challenge`, `tracer`, `listmodels`, `version`

👉 **[Complete Tools Reference](tools/)** with detailed examples and parameters

## Common Issues and Solutions

### "pal not found" or "command not found"

**For uvx installations:**
- Ensure `uv` is installed and in PATH
- Try: `which uvx` to verify uvx is available
- Check PATH includes `/usr/local/bin` and `~/.local/bin`

**For clone installations:**
- Run `./run-server.sh` again to verify setup
- Check virtual environment: `which python` should show `.pal_venv/bin/python`

### API Key Issues

**"Invalid API key" errors:**
- Verify API keys in `.env` file or MCP configuration
- Test API keys directly with provider's API
- Check for extra spaces or quotes around keys

**"Model not available":**
- Run `"Use pal to list available models"` to see what's configured
- Check model restrictions in environment variables
- Verify API key has access to requested models

### CLI Setup and Continuation Issues

- **Provider-free `listmodels`:** this is expected without registered providers. Its missing/incomplete setting hints explain how to enable API tools; they do not prevent independent tools from working.
- **Executable found, request fails:** discovery does not verify authentication, quota, CLI version, or arguments. Check the selected CLI's diagnostic and [vendor guidance](tools/clink.md#authentication-and-version-assumptions) under the same OS account and launch environment.
- **Busy continuation:** retry the same ID after the active request finishes. Failed CLI requests do not append history or refresh its TTL.
- **Expired/full/oversized history:** start a new conversation or follow the [history budget guidance](configuration.md#cli-conversation-history-budgets). If an answer has `metadata.history_saved=false`, keep the answer; it has no new continuation offer.

### Performance Issues

**Slow responses:**
- Use faster models: `flash` instead of `pro`  
- Lower thinking modes: `minimal` or `low` instead of `high`
- Restrict model access to prevent expensive model selection

**Token limit errors:**
- Use models with larger context windows
- Break large requests into smaller chunks
- See [Working with Large Prompts](advanced-usage.md#working-with-large-prompts)

### More Help

👉 **[Complete Troubleshooting Guide](troubleshooting.md)** with detailed solutions

👉 **[Advanced Usage Guide](advanced-usage.md)** for power-user features

👉 **[Configuration Reference](configuration.md)** for all options

## What's Next?

🎯 **Try the example workflows in the main README**

📚 **Explore the [Tools Reference](tools/)** to understand what each tool can do

⚡ **Read the [Advanced Usage Guide](advanced-usage.md)** for complex workflows

🔧 **Check out [Configuration Options](configuration.md)** to customize behavior

💡 **Join discussions and get help** in the project issues or discussions

## Quick Configuration Templates

### Development Setup (Balanced)
```env
DEFAULT_MODEL=auto
GEMINI_API_KEY=your-key
OPENAI_API_KEY=your-key
GOOGLE_ALLOWED_MODELS=flash,pro
OPENAI_ALLOWED_MODELS=gpt-5.1-codex-mini,gpt-5-mini,o4-mini
```

### Cost-Optimized Setup
```env  
DEFAULT_MODEL=flash
GEMINI_API_KEY=your-key
GOOGLE_ALLOWED_MODELS=flash
```

### High-Performance Setup  
```env
DEFAULT_MODEL=auto
GEMINI_API_KEY=your-key
OPENAI_API_KEY=your-key
GOOGLE_ALLOWED_MODELS=pro
OPENAI_ALLOWED_MODELS=gpt-5.1-codex,gpt-5.2
```

### Local-First Setup
```env
DEFAULT_MODEL=auto
CUSTOM_API_URL=http://localhost:11434/v1
CUSTOM_MODEL_NAME=llama3.2
# Add cloud APIs as backup
GEMINI_API_KEY=your-key
```

Happy coding with your AI development team! 🤖✨

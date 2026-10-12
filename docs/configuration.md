# Configuration Guide

This guide covers all configuration options for the PAL MCP Server. The server is configured through environment variables defined in your `.env` file.

State/log placement uses `PAL_STATE_DIR`, then `XDG_STATE_HOME/pal`, then the
per-user fallback. `PAL_LOG_DIR` overrides logs only. All explicit paths must be
absolute after home expansion. See [logging and migration](logging.md) for
platform defaults, rotation sizes, container volumes and legacy logs.

## Quick Start Configuration

**Auto Mode (Recommended):** Set `DEFAULT_MODEL=auto` and let Claude intelligently select the best model for each task:

```env
# Basic configuration
DEFAULT_MODEL=auto
GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
```

## Complete Configuration Reference

### Required Configuration

**Workspace Root:**
```env

### API Keys (required for model calls)

With no provider credentials, PAL still starts and exposes its tools. Version,
model listing and provider-free planner operations remain available; clink uses
its own configured CLI authentication. Model calls return MCP tool errors with
setup guidance. There is no invented fallback model when none is eligible.

Invalid model configuration is a separate startup fault. Explicit paths must
exist and be readable; invalid UTF-8/JSON, malformed model entries, conflicting
aliases and invalid Azure deployments raise errors naming the registry file.
Shipped required registries are also validated. Default Custom/Azure catalogs
may be absent or contain `{"models": []}`; an explicitly selected missing file
is always an error. A malformed file never silently becomes an empty catalog.

Providers can coexist. Complete registered names and aliases resolve in this
priority order: Gemini, OpenAI, Azure, X.AI, DIAL, Custom, OpenRouter. Policy is
applied to that identity; a disallowed match does not fall through to another provider.

**Option 1: Native APIs (Recommended for direct access)**
```env
# Google Gemini API
GEMINI_API_KEY=your_gemini_api_key_here
# Get from: https://makersuite.google.com/app/apikey

# OpenAI API  
OPENAI_API_KEY=your_openai_api_key_here
# Get from: https://platform.openai.com/api-keys

# X.AI GROK API
XAI_API_KEY=your_xai_api_key_here
# Get from: https://console.x.ai/
```

**Option 2: OpenRouter (Access multiple models through one API)**
```env
# OpenRouter for unified model access
OPENROUTER_API_KEY=your_openrouter_api_key_here
# Get from: https://openrouter.ai/
# If using OpenRouter, comment out native API keys above
```

**Option 3: Custom API Endpoints (Local models)**
```env
# For Ollama, vLLM, LM Studio, etc.
CUSTOM_API_URL=http://localhost:11434/v1  # Ollama example
CUSTOM_API_KEY=                                      # Empty for Ollama
CUSTOM_MODEL_NAME=llama3.2                          # Default model
```

**Local Model Connection:**
- Use standard localhost URLs since the server runs natively
- Example: `http://localhost:11434/v1` for Ollama

### Model Configuration

File allowances reuse the effective response reservation and remaining input
capacity. The admission check and renderer use the same allowance and count the
rendered file headers/line numbers, with the conservative request-estimation margin
and a 1,000-token allowance for surrounding prompt text. This is not an additional
output reservation. A zero remaining allowance stays zero; there is no fallback
to a larger file budget. Oversized selections report the binding limit, overage
and largest contributors. Historical files may be omitted with explicit diagnostics.

Workflow `file_context` and chat metadata distinguish `files_embedded` in the
current file block from `files_in_history` actually present in rendered history.
Missing, denied, unreadable and oversized files do not count as covered. Stored
path references alone never suppress an embedding. Consensus participants use
their own model/proposal/stance/attachment budget and never deduct hidden audit
history or deduplicate attachments against it. File descriptions remain separate
planned work; existing successful file headers are preserved.

Model names are matched as complete identifiers, ignoring outer whitespace and
case. Colons in registered names are preserved, including custom tags, DIAL `:0`
deployments and OpenRouter suffixes. To migrate an invocation that previously
relied on automatic tag stripping, register its exact tagged name in the provider
catalog, or add an explicit alias to the intended model. Unknown tags now fail
before a provider call.

Only an unresolved full name ending in `:for`, `:against` or `:neutral` can be
treated as a legacy option, and the preceding complete model name must resolve.
A registered name ending in `:for` wins even when restrictions disallow it.
Options remain separate metadata; they do not add stance behavior to other tools.
Provider prefixes are not an additional invocation syntax.

Allowlists use each provider's canonical names or aliases: `GOOGLE_ALLOWED_MODELS`,
`OPENAI_ALLOWED_MODELS`, `AZURE_ALLOWED_MODELS`, `XAI_ALLOWED_MODELS`,
`DIAL_ALLOWED_MODELS`, `CUSTOM_ALLOWED_MODELS`, and `OPENROUTER_ALLOWED_MODELS`.
Custom endpoints still require `CUSTOM_API_URL` and may omit `CUSTOM_API_KEY`.
Azure requires both `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT`, plus a
configured deployment. Placeholder keys do not activate providers. Setup diagnostics
show setting names, not credential values or endpoint URLs.
`AZURE_OPENAI_ALLOWED_MODELS` remains a compatibility alias with precedence over
`AZURE_ALLOWED_MODELS`. Azure rejects a deployment ID that collides with another
model's canonical name, alias or deployment; choose unambiguous deployment names.

**Default Model Selection:**
```env
# Options: 'auto', 'pro', 'flash', 'gpt5.2', 'gpt5.1-codex', 'gpt5.1-codex-mini', 'o3', 'o3-mini', 'o4-mini', etc.
DEFAULT_MODEL=auto  # Claude picks best model for each task (recommended)
```

- **Available Models:** The canonical capability data for native providers lives in JSON manifests under `conf/`:
  - `conf/openai_models.json` – OpenAI catalogue (can be overridden with `OPENAI_MODELS_CONFIG_PATH`)
  - `conf/gemini_models.json` – Gemini catalogue (`GEMINI_MODELS_CONFIG_PATH`)
  - `conf/xai_models.json` – X.AI / GROK catalogue (`XAI_MODELS_CONFIG_PATH`)
  - `conf/openrouter_models.json` – OpenRouter catalogue (`OPENROUTER_MODELS_CONFIG_PATH`)
  - `conf/dial_models.json` – DIAL aggregation catalogue (`DIAL_MODELS_CONFIG_PATH`)
  - `conf/custom_models.json` – Custom/OpenAI-compatible endpoints (`CUSTOM_MODELS_CONFIG_PATH`)

  Each JSON file documents the allowed fields via its `_README` block and controls model aliases, capability limits, and feature flags (including `allow_code_generation`). Edit these files (or point the matching `*_MODELS_CONFIG_PATH` variable to your own copy) when you want to adjust context windows, enable JSON mode, enable structured code generation, or expose additional aliases without touching Python code.

  The shipped defaults cover:

  | Provider | Canonical Models | Notable Aliases |
  |----------|-----------------|-----------------|
  | OpenAI | `gpt-5.2`, `gpt-5.1-codex`, `gpt-5.1-codex-mini`, `gpt-5`, `gpt-5.2-pro`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5-codex`, `gpt-4.1`, `o3`, `o3-mini`, `o3-pro`, `o4-mini` | `gpt5.2`, `gpt-5.2`, `5.2`, `gpt5.1-codex`, `codex-5.1`, `codex-mini`, `gpt5`, `gpt5pro`, `mini`, `nano`, `codex`, `o3mini`, `o3pro`, `o4mini` |
  | Gemini | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-2.0-flash-lite` | `pro`, `gemini-pro`, `flash`, `flash-2.0`, `flashlite` |
  | X.AI | `grok-4`, `grok-4.1-fast` | `grok`, `grok4`, `grok-4.1-fast-reasoning` |
  | OpenRouter | See `conf/openrouter_models.json` for the continually evolving catalogue | e.g., `opus`, `sonnet`, `flash`, `pro`, `mistral` |
  | Custom | User-managed entries such as `llama3.2` | Define your own aliases per entry |

  Latest OpenAI entries (`gpt-5.2`, `gpt-5.1-codex`, `gpt-5.1-codex-mini`, `gpt-5.2-pro`) expose 400K-token contexts with large outputs, reasoning-token support, and multimodal inputs. `gpt-5.1-codex` and `gpt-5.2-pro` are Responses-only with streaming disabled, while the base `gpt-5.2` and Codex mini support streaming along with full code-generation flags. Update your manifests if you run custom deployments so these capability bits stay accurate.

  > **Tip:** Copy the JSON file you need, customise it, and point the corresponding `*_MODELS_CONFIG_PATH` environment variable to your version. This lets you enable or disable capabilities (JSON mode, function calling, temperature support, code generation) without editing Python.

### Code Generation Capability

**`allow_code_generation` Flag:**

The `allow_code_generation` capability enables models to generate complete, production-ready implementations in a structured format. When enabled, the `chat` tool will inject special instructions for substantial code generation tasks.

```json
{
  "model_name": "gpt-5",
  "allow_code_generation": true,
  ...
}
```

**When to Enable:**

- **Enable for**: Models MORE capable than your primary CLI's model (e.g., GPT-5.1 Codex, GPT-5.2 Pro, GPT-5.2 when using Claude Code with Sonnet 4.5)
- **Purpose**: Get complete implementations from a more powerful reasoning model that your primary CLI can then review and apply
- **Use case**: Large-scale implementations, major refactoring, complete module creation

**Important Guidelines:**

1. Only enable for models significantly more capable than your primary CLI to ensure high-quality generated code
2. The capability triggers structured code output (`<GENERATED-CODE>` blocks) for substantial implementation requests
3. Minor code changes still use inline code blocks regardless of this setting
4. Generated code is saved to `.pal_artifacts/pal_generated_<unique-id>.code` in the user's working directory
5. Your CLI receives instructions to review and apply the generated code systematically

**Example Configuration:**

```json
// OpenAI models configuration (conf/openai_models.json)
{
  "models": [
    {
      "model_name": "gpt-5",
      "allow_code_generation": true,
      "intelligence_score": 18,
      ...
    },
    {
      "model_name": "gpt-5.2-pro",
      "allow_code_generation": true,
      "intelligence_score": 19,
      ...
    }
  ]
}
```

**Typical Workflow:**
1. You ask your AI agent to implement a complex new feature using `chat` with a higher-reasoning model such as **gpt-5.2-pro**
2. GPT-5.2-Pro generates structured implementation and shares the complete implementation with PAL
3. PAL saves the code to `.pal_artifacts/pal_generated_<unique-id>.code` and asks AI agent to implement the plan
4. AI agent continues from the previous context, reads the file, applies the implementation

### Thinking Mode Configuration

**Default Thinking Mode for ThinkDeep:**
```env
# Only applies to models supporting extended thinking (e.g., Gemini 3.0 Pro)
# Starting with Gemini 3.0 Pro, `thinking level` should stick to `high`

DEFAULT_THINKING_MODE_THINKDEEP=high

# Available modes and token consumption:
#   minimal: 128 tokens   - Quick analysis, fastest response
#   low:     2,048 tokens - Light reasoning tasks  
#   medium:  8,192 tokens - Balanced reasoning
#   high:    16,384 tokens - Complex analysis (recommended for thinkdeep)
#   max:     32,768 tokens - Maximum reasoning depth
```

### Model Usage Restrictions

Model catalog entries can set `"enabled_by_default": false` to hide their names
and aliases from discovery, tool model suggestions and category fallback. Omitted
or `true` preserves existing behavior; other values are configuration errors.
This is a discovery setting: explicit canonical calls and aliases still resolve
against the full catalog and must pass the same allow/deny policy.

`PREFERRED_MODELS` is an ordered, comma-separated list for auto selection and
fallback. Entries are trimmed, matched case-insensitively against configured
catalogs and deduplicated by provider/canonical identity. The first eligible
preference takes priority over category ranking. Unknown, unavailable, denied or
still-hidden entries are skipped; if none qualify, category selection runs.
Explicit request/per-tool choices and fixed defaults keep their existing behavior.

```env
PREFERRED_MODELS=llama3.2:latest,gemini-2.5-flash
```

To expose a hidden entry, list its **canonical name** in its provider allowlist or
in `PREFERRED_MODELS`. An alias alone does not opt a hidden entry into discovery,
even after using that alias in a call. Preferences may use aliases for already
visible entries. Preference identities follow normal provider routing priority;
there is no provider-scope prefix or invocation-only option parsing in this setting.
Tagged IDs, including complete names ending in `:for`, stay intact. Opt-ins never
override allowlists or `DISABLED_MODELS`. Unfiltered internal catalog access remains
available with `list_models(respect_restrictions=False)` for identity validation.
When a preferred alias selects a provider whose canonical model name also exists
at a higher-priority provider, selection retains the alias for routing. The selected
provider still serializes its canonical model name on the wire.

`DISABLED_MODELS` adds a deny override after provider allowlists. It applies to
explicit calls, aliases, model listings and auto fallback. Empty or unset means
no global denies. Restart the MCP server after changing policy or catalogs.

```env
# Deny this canonical model (and all its aliases) wherever it resolves
DISABLED_MODELS=gemini-2.5-pro
# Or scope each deny to a provider; tags and other remaining colons stay intact
DISABLED_MODELS=custom:llama3.2:latest,dial:deployment:0,openrouter:opus
```

Recognized scopes are `google`, `openai`, `azure`, `xai`, `dial`, `custom` and
`openrouter`. Only the first recognized prefix is removed. Unscoped names are
resolved independently in every configured provider's full catalog; scoped names
affect only that provider. Canonical names and aliases deny the same identity.
These settings never interpret invocation-only `:for`/`:against`/`:neutral`
options. Unknown entries produce a startup warning by entry position, without
echoing potentially sensitive input. A deny cannot be overridden by an allowlist,
and a denied higher-priority identity does not reroute to another provider.

Control which models can be used from each provider for cost control, compliance, or standardization:

```env
# Format: Comma-separated list (case-insensitive, whitespace tolerant)
# Empty or unset = all models allowed (default)

# OpenAI model restrictions
OPENAI_ALLOWED_MODELS=gpt-5.1-codex-mini,gpt-5-mini,o3-mini,o4-mini,mini

# Gemini model restrictions  
GOOGLE_ALLOWED_MODELS=flash,pro

# X.AI GROK model restrictions
XAI_ALLOWED_MODELS=grok-4,grok-4.1-fast-reasoning

# OpenRouter model restrictions (affects models via custom provider)
OPENROUTER_ALLOWED_MODELS=opus,sonnet,mistral
```

**Supported Model Names:** The names/aliases listed in the JSON manifests above are the authoritative source. Keep in mind:

- Aliases are case-insensitive and defined per entry (for example, `mini` maps to `gpt-5-mini` by default, while `flash` maps to `gemini-2.5-flash`).
- When you override the manifest files you can add or remove aliases as needed; restriction policies (`*_ALLOWED_MODELS`) automatically pick up those changes.
- Models omitted from a manifest fall back to generic capability detection (where supported) and may have limited feature metadata.

**Example Configurations:**
```env
# Cost control - only cheap models
OPENAI_ALLOWED_MODELS=o4-mini
GOOGLE_ALLOWED_MODELS=flash

# High-performance setup
OPENAI_ALLOWED_MODELS=gpt-5.1-codex,gpt-5.2
GOOGLE_ALLOWED_MODELS=pro

# Single model standardization
OPENAI_ALLOWED_MODELS=o4-mini
GOOGLE_ALLOWED_MODELS=pro

# Balanced selection
GOOGLE_ALLOWED_MODELS=flash,pro
OPENAI_ALLOWED_MODELS=gpt-5.1-codex-mini,gpt-5-mini,o4-mini
XAI_ALLOWED_MODELS=grok,grok-4.1-fast-reasoning
```

### Advanced Configuration

**Custom Model Configuration & Manifest Overrides:**
```env
# Override default location of built-in catalogues
OPENAI_MODELS_CONFIG_PATH=/path/to/openai_models.json
GEMINI_MODELS_CONFIG_PATH=/path/to/gemini_models.json
XAI_MODELS_CONFIG_PATH=/path/to/xai_models.json
OPENROUTER_MODELS_CONFIG_PATH=/path/to/openrouter_models.json
DIAL_MODELS_CONFIG_PATH=/path/to/dial_models.json
CUSTOM_MODELS_CONFIG_PATH=/path/to/custom_models.json
```

**Conversation Settings:**
```env
# How long AI-to-AI conversation threads persist in memory (hours)
# Conversations are auto-purged when claude closes its MCP connection or 
# when a session is quit / re-launched 
CONVERSATION_TIMEOUT_HOURS=5

# Maximum conversation turns (each exchange = 2 turns)
MAX_CONVERSATION_TURNS=20
```

**Logging Configuration:**
```env
# Logging level: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=DEBUG  # Default: shows detailed operational messages
```

## Configuration Examples

### Development Setup
```env
# Development with multiple providers
DEFAULT_MODEL=auto
GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
GOOGLE_ALLOWED_MODELS=flash,pro
OPENAI_ALLOWED_MODELS=gpt-5.1-codex-mini,gpt-5-mini,o4-mini
XAI_API_KEY=your-xai-key
LOG_LEVEL=DEBUG
CONVERSATION_TIMEOUT_HOURS=1
```

### Production Setup
```env
# Production with cost controls
DEFAULT_MODEL=auto
GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
GOOGLE_ALLOWED_MODELS=flash
OPENAI_ALLOWED_MODELS=gpt-5.1-codex-mini,o4-mini
LOG_LEVEL=INFO
CONVERSATION_TIMEOUT_HOURS=3
```

### Local Development
```env
# Local models only
DEFAULT_MODEL=llama3.2
CUSTOM_API_URL=http://localhost:11434/v1
CUSTOM_API_KEY=
CUSTOM_MODEL_NAME=llama3.2
LOG_LEVEL=DEBUG
```

### OpenRouter Only
```env
# Single API for multiple models
DEFAULT_MODEL=auto
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_ALLOWED_MODELS=opus,sonnet,gpt-4
LOG_LEVEL=INFO
```

## Important Notes

**Local Networking:**
- Use standard localhost URLs for local models
- The server runs as a native Python process

**API Key Priority:**
- Native APIs take priority over OpenRouter when both are configured
- Avoid configuring both native and OpenRouter for the same models

**Model Restrictions:**
- Apply to all usage including auto mode
- Empty/unset = all models allowed
- Invalid model names are warned about at startup

**Configuration Changes:**
- Restart the server with `./run-server.sh` after changing `.env`
- Configuration is loaded once at startup

## Related Documentation

- **[Advanced Usage Guide](advanced-usage.md)** - Advanced model usage patterns, thinking modes, and power user workflows
- **[Context Revival Guide](context-revival.md)** - Conversation persistence and context revival across sessions
- **[AI-to-AI Collaboration Guide](ai-collaboration.md)** - Multi-model coordination and conversation threading

### Chat artifact write root

`PAL_WORKSPACE_ROOT` is an absolute existing directory supplied by the operator in
the server launch environment. It defaults to startup cwd, which may be the PAL
installation rather than your project. A code-generation request's working
directory must resolve within that root. Artifacts have exclusive unique names in
`.pal_artifacts`; path/write failures are explicit errors. See the
[chat workspace contract](tools/chat.md#artifact-workspace-and-failures) for
symlink checks, platform requirements and cleanup. Context-file reads retain their
independent deny policy.

### Context-file read policy

Context-file readers reject system directories, including `/proc`, `/sys`, `/dev`
and `/run`, as well as existing sensitive home subdirectories. Symlinks to denied
locations remain denied. Ordinary project files under home or temporary directories
remain readable; chat's artifact write root does not restrict context-file reads.

Readers enforce their byte limit on the actual binary read before UTF-8 decoding,
using one extra byte to detect overflow even when filesystem metadata is stale or
reports zero bytes. Oversized prompt files produce an explicit `FILE TOO LARGE`
marker; the optional safe-reader API returns `None`. Ordinary successful formatting
and each reader's invalid-UTF-8 behavior are unchanged. Token estimation validates
the path before reading size metadata and returns zero for denied paths.


### Provider request budgets

PAL reserves and sends one output limit per request: the minimum of the model's
`max_output_tokens`, 40% of context below 300,000 tokens (20% otherwise), and any
lower positive limit supplied by a direct provider caller. MCP tools add no new
public output parameter. Remaining input capacity is context minus that limit.
Chat, empty-response recovery, expert analysis and each blinded consensus
participant check their assembled prompt, system instructions and images before
I/O. Requests that exceed the estimate fail explicitly; reduce input/attachments.

Text estimation uses UTF-8 bytes/3 rounded up, plus a 10% margin, 64 protocol tokens
and 8,192 tokens per image. These conservative estimates do not promise tokenizer
precision. History/file rendering retains its existing omission policy; final
admission counts only the payload that will actually reach that participant.

Configured models/deployments must declare positive integer `context_window` and
`max_output_tokens`. Generic OpenRouter fallback capacities are unverified and
cannot be used for generation. Unknown Azure deployments need explicit capacity
metadata. `output_token_parameter` can declare the verified endpoint field:
`max_tokens` or `max_completion_tokens` for Chat Completions, `max_output_tokens`
for Responses/Gemini. The default is `max_tokens` for OpenAI-compatible Chat
Completions and `max_output_tokens` for Responses/Gemini; native OpenAI registry
entries (inherited by Azure) and DIAL O3/O4-mini explicitly select their field.
Invalid or endpoint-incompatible declarations fail before I/O. Custom endpoints
must actually support their configured field; PAL cannot discover arbitrary server
capabilities. Usage includes only valid counts reported by the endpoint; absent
counts remain unknown, while a reported zero is retained.


### Gemini thinking controls

Native Gemini model metadata selects `thinking_parameter`: `thinking_budget`
for verified Gemini 2.5 entries and `thinking_level` for verified Gemini 3 entries.
Level-based models also declare a nonempty `supported_thinking_levels` list using
`minimal`, `low`, `medium`, and/or `high`. The list is model-specific; SDK enum
membership alone does not establish model support.

PAL maps its requested mode to the nearest accepted level at or below it. If none
exists, it chooses the lowest accepted level above it. `max` maps to the highest
accepted level. For example, Gemini 3 Pro Preview's `medium` now maps to `low`,
and Gemini 3.8 Flash's `minimal` maps to `low`. Response metadata reports the
selected level. Gemini 2.5 retains the existing percentage-of-budget mapping.
Models without thinking support omit the control.

Invalid modes, empty/malformed level lists and unverified controls fail before
provider I/O. The retained experimental Gemini 2.0 Flash entry has no verified
current budget/level contract and now fails explicitly; its retirement and the
broader catalog audit remain Task 28. No model entries or aliases are removed by
this change. Capability validation and fake-transport tests do not establish live
availability of legacy models.

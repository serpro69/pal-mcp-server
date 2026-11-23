# Custom Models & API Setup

This guide covers setting up multiple AI model providers including OpenRouter, custom API endpoints, and local model servers. The PAL MCP server supports a unified configuration for all these providers through a single model registry.

## Supported Providers

- **OpenRouter** - Unified access to multiple commercial models (GPT-4, Claude, Mistral, etc.)
- **Custom API endpoints** - Local models (Ollama, vLLM, LM Studio, text-generation-webui)
- **Self-hosted APIs** - Any OpenAI-compatible endpoint

## When to Use What

**Use OpenRouter when you want:**
- Access to models not available through native APIs (GPT-4, Claude, Mistral, etc.)
- Simplified billing across multiple model providers
- Experimentation with various models without separate API keys

**Use Custom URLs for:**
- **Local models** like Ollama (Llama, Mistral, etc.)
- **Self-hosted inference** with vLLM, LM Studio, text-generation-webui
- **Private/enterprise APIs** that use OpenAI-compatible format
- **Cost control** with local hardware

**Use native APIs (Gemini/OpenAI) when you want:**
- Direct access to specific providers without intermediary
- Potentially lower latency and costs
- Access to the latest model features immediately upon release

**Mix & Match:** You can use multiple providers simultaneously! For example:
- OpenRouter for expensive commercial models (GPT-4, Claude)
- Custom URLs for local models (Ollama Llama)
- Native APIs for specific providers (Gemini Pro with extended thinking)

**Note:** When multiple providers offer the same model name, native APIs take priority over OpenRouter.

## Model Aliases

PAL ships multiple registries:

- `conf/openai_models.json` – native OpenAI catalogue (override with `OPENAI_MODELS_CONFIG_PATH`)
- `conf/gemini_models.json` – native Google Gemini catalogue (`GEMINI_MODELS_CONFIG_PATH`)
- `conf/xai_models.json` – native X.AI / GROK catalogue (`XAI_MODELS_CONFIG_PATH`)
- `conf/openrouter_models.json` – OpenRouter catalogue (`OPENROUTER_MODELS_CONFIG_PATH`)
- `conf/dial_models.json` – DIAL aggregation catalogue (`DIAL_MODELS_CONFIG_PATH`)
- `conf/custom_models.json` – local/self-hosted OpenAI-compatible catalogue (`CUSTOM_MODELS_CONFIG_PATH`)

Copy whichever file you need into your project (or point the corresponding `*_MODELS_CONFIG_PATH` env var at your own copy) and edit it to advertise the models you want.

### OpenRouter Models (Cloud)

The curated defaults in `conf/openrouter_models.json` include popular entries such as:

| Alias | Canonical Model | Highlights |
|-------|-----------------|------------|
| `opus`, `claude-opus` | `anthropic/claude-opus-4.1` | Flagship Claude reasoning model with vision |
| `sonnet`, `sonnet4.5` | `anthropic/claude-sonnet-4.5` | Balanced Claude with high context window |
| `haiku` | `anthropic/claude-3.5-haiku` | Fast Claude option with vision |
| `pro`, `gemini` | `google/gemini-2.5-pro` | Frontier Gemini with extended thinking |
| `flash` | `google/gemini-2.5-flash` | Ultra-fast Gemini with vision |
| `mistral` | `mistralai/mistral-large-2411` | Frontier Mistral (text only) |
| `llama3` | `meta-llama/llama-3-70b` | Large open-weight text model |
| `deepseek-r1` | `deepseek/deepseek-r1-0528` | DeepSeek reasoning model |
| `perplexity` | `perplexity/llama-3-sonar-large-32k-online` | Search-augmented model |
| `gpt5.2`, `gpt-5.2`, `5.2` | `openai/gpt-5.2` | Flagship GPT-5.2 with reasoning and vision |
| `gpt5.1-codex`, `codex-5.1` | `openai/gpt-5.1-codex` | Agentic coding specialization (Responses API) |
| `codex-mini`, `gpt5.1-codex-mini` | `openai/gpt-5.1-codex-mini` | Cost-efficient Codex variant with streaming |

Consult the JSON file for the full list, aliases, and capability flags. Add new entries as OpenRouter releases additional models.

### Custom/Local Models

| Alias | Maps to Local Model | Note |
|-------|-------------------|------|
| `local-llama`, `ollama-llama` | `llama3.2` | Requires `CUSTOM_API_URL` configured |

View the [OpenRouter catalog](../conf/openrouter_models.json) or edit the [custom endpoint catalog](../conf/custom_models.json) to advertise local models.

Native catalogues (`conf/openai_models.json`, `conf/gemini_models.json`, `conf/xai_models.json`, `conf/dial_models.json`) follow the same schema. Updating those files lets you:

> For a step-by-step walkthrough of adding a model to one of these native catalogues (including the alias-migration policy and which tests need updating), see [Adding Models](adding_models.md).

- Expose new aliases (e.g., map `enterprise-pro` to `gpt-5.2-pro`)
- Advertise support for JSON mode or vision if the upstream provider adds it
- Adjust token limits when providers increase context windows

### Latest OpenAI releases

OpenAI's November 13, 2025 drop introduced `gpt-5.1-codex` and `gpt-5.1-codex-mini`, while the flagship base model is now `gpt-5.2`. All of these ship in `conf/openai_models.json`:

| Model | Highlights | Notes |
|-------|------------|-------|
| `gpt-5.2` | 400K context, 128K output, multimodal IO, configurable reasoning effort | Streaming enabled; use for balanced agent/coding flows |
| `gpt-5.1-codex` | Responses-only agentic coding version of GPT-5.1 | Streaming disabled; `use_openai_response_api=true`; `allow_code_generation=true` |
| `gpt-5.1-codex-mini` | Cost-efficient Codex variant | Streaming enabled, retains 400K context and code-generation flag |

These entries include pricing-friendly aliases (`gpt5.2`, `codex-5.1`, `codex-mini`) plus updated capability flags (`supports_extended_thinking`, `allow_code_generation`). Copy the manifest if you operate custom deployment names so downstream providers inherit the same metadata.

Because providers load the manifests on import, you can tweak capabilities without touching Python. Restart the server after editing the JSON files so changes are picked up.

To control ordering in auto mode or the `listmodels` summary, adjust the
[`intelligence_score`](model_ranking.md) for each entry (or rely on the automatic
heuristic described there).

Requests need verified token limits. Add unlisted OpenRouter models to the OpenRouter catalog and models served by your own endpoint to the custom catalog.

## Quick Start

### Option 1: OpenRouter Setup

#### 1. Get API Key
1. Sign up at [openrouter.ai](https://openrouter.ai/)
2. Create an API key from your dashboard
3. Add credits to your account

#### 2. Set Environment Variable
```bash
# Add to your .env file
OPENROUTER_API_KEY=your-openrouter-api-key
```

> **Note:** Control which models can be used directly in your OpenRouter dashboard at [openrouter.ai](https://openrouter.ai/). 
> This gives you centralized control over model access and spending limits.

That's it! The setup script handles all necessary configuration automatically.

### Option 2: Custom API Setup (Ollama, vLLM, etc.)

For local models like Ollama, vLLM, LM Studio, or any OpenAI-compatible API:

#### 1. Start Your Local Model Server
```bash
# Example: Ollama
ollama serve
ollama pull llama3.2

# Example: vLLM
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-2-7b-chat-hf

# Example: LM Studio (enable OpenAI compatibility in settings)
# Server runs on localhost:1234
```

#### 2. Configure Environment Variables
```bash
# Add to your .env file
CUSTOM_API_URL=http://localhost:11434/v1  # Ollama example
CUSTOM_API_KEY=                          # Empty for Ollama
DEFAULT_MODEL=local-llama                # Optional, shipped alias for llama3.2
```

**Local Model Connection**

The PAL MCP server runs natively, so you can use standard localhost URLs to connect to local models:

```bash
# For Ollama, vLLM, LM Studio, etc. running on your machine
CUSTOM_API_URL=http://localhost:11434/v1  # Ollama default port
```

#### 3. Register the models your endpoint serves

PAL reads model IDs, aliases and token limits from `conf/custom_models.json`. To keep a separate catalog, set `CUSTOM_MODELS_CONFIG_PATH` to the absolute path of your JSON file. This file replaces the shipped custom catalog.

Use the exact model ID accepted by the endpoint, including any Ollama tag. Restart PAL and call `listmodels` to confirm the entry. `DEFAULT_MODEL` selects a registered ID or alias for requests that omit a model. With `DEFAULT_MODEL=auto`, PAL chooses from eligible catalog entries across configured providers. Discovery does not query the endpoint for installed models. [Tagged Ollama example](#adding-a-customlocal-model)

For Docker Compose, place `ollama-models.json` beside the Compose file and add a `docker-compose.override.yml` that mounts it and sets its container path together. Set `CUSTOM_API_URL` to an endpoint reachable from the container.

```yaml
services:
  pal-mcp:
    volumes:
      - ./ollama-models.json:/etc/pal/ollama-models.json:ro
    environment:
      CUSTOM_MODELS_CONFIG_PATH: /etc/pal/ollama-models.json
```

#### 4. Examples for Different Platforms

**Ollama:**
```bash
CUSTOM_API_URL=http://localhost:11434/v1
CUSTOM_API_KEY=
DEFAULT_MODEL=llama3.2
```

**vLLM:**
```bash
CUSTOM_API_URL=http://localhost:8000/v1
CUSTOM_API_KEY=
DEFAULT_MODEL=meta-llama/Llama-2-7b-chat-hf  # Register this ID in the custom catalog first
```

**LM Studio:**
```bash
CUSTOM_API_URL=http://localhost:1234/v1
CUSTOM_API_KEY=lm-studio  # Or any value, LM Studio often requires some key
DEFAULT_MODEL=local-model  # Use the ID or alias you registered for the loaded model
```

**text-generation-webui (with OpenAI extension):**
```bash
CUSTOM_API_URL=http://localhost:5001/v1
CUSTOM_API_KEY=
DEFAULT_MODEL=your-loaded-model  # Use the ID or alias from your catalog
```

## Using Models

**Using model aliases (from the registry files):**
```
# OpenRouter models:
"Use opus for deep analysis"         # → anthropic/claude-opus-4
"Use sonnet to review this code"     # → anthropic/claude-sonnet-4
"Use pro via pal to analyze this"    # → google/gemini-2.5-pro
"Use gpt4o via pal to analyze this"  # → openai/gpt-4o
"Use mistral via pal to optimize"    # → mistral/mistral-large

# Local models (with custom URL configured):
"Use local-llama to analyze this code"     # → llama3.2 (local)
"Use ollama-llama to debug this function"  # → llama3.2 (local)
```

**Using full model names:**
```
# OpenRouter models:
"Use anthropic/claude-opus-4 via pal for deep analysis"
"Use openai/gpt-4o via pal to debug this"
"Use deepseek/deepseek-coder via pal to generate code"

# Local/custom models:
"Use llama3.2 via pal to review this"
"Use meta-llama/Llama-2-7b-chat-hf via pal to analyze"
```

**For OpenRouter:** Check current model pricing at [openrouter.ai/models](https://openrouter.ai/models).  
**For Local models:** Context window and capabilities are defined in `conf/custom_models.json`.

## Model Provider Selection

The system automatically routes models to the appropriate provider:

1. Entries in `conf/custom_models.json` → Available through the Custom API when `CUSTOM_API_URL` is configured
2. Entries in `conf/openrouter_models.json` → Routed through OpenRouter (requires `OPENROUTER_API_KEY`)
3. **Undeclared custom models** → Register the exact ID or an alias before requesting the model

**Provider Priority Order:**
1. Native APIs (Google, OpenAI) - if API keys are available
2. Custom endpoints - for models declared in `conf/custom_models.json`  
3. OpenRouter - catch-all for cloud models

This ensures clean separation between local and cloud models while maintaining flexibility for unknown models.

## Model Configuration

These JSON files define model aliases and capabilities. You can:

1. **Use the default configuration** - Includes popular models with convenient aliases
2. **Customize the configuration** - Add your own models and aliases
3. **Override the config path** - Set `CUSTOM_MODELS_CONFIG_PATH` environment variable to an absolute path on disk

### Adding Custom Models

Edit `conf/openrouter_models.json` to tweak OpenRouter behaviour or `conf/custom_models.json` to add local models. Each entry maps directly onto [`ModelCapabilities`](../providers/shared/model_capabilities.py).

#### Adding an OpenRouter Model

```json
{
  "model_name": "vendor/model-name",
  "aliases": ["short-name", "nickname"],
  "context_window": 128000,
  "supports_extended_thinking": false,
  "supports_json_mode": true,
  "supports_function_calling": true,
  "description": "Model description"
}
```

#### Adding a Custom/Local Model

This complete override file registers `qwen3:latest` and the alias `local-qwen`. The example assumes an endpoint configured for an 8,192-token context and up to 2,048 output tokens. Use the limits supported by your deployment.

```json
{
  "models": [
    {
      "model_name": "qwen3:latest",
      "aliases": ["local-qwen"],
      "context_window": 8192,
      "max_output_tokens": 2048
    }
  ]
}
```

For example, set `CUSTOM_MODELS_CONFIG_PATH=/absolute/path/to/ollama-models.json` and `DEFAULT_MODEL=local-qwen`. PAL sends `qwen3:latest` to the endpoint.

**Field explanations:**
- `model_name`: The model identifier (OpenRouter format like `vendor/model` or local name like `llama3.2`)
- `aliases`: Array of short names users can type instead of the full model name
- `context_window`: Total tokens the model can process (input + output combined)
- `max_output_tokens`: Maximum output tokens your deployment supports per request
- `supports_extended_thinking`: Whether the model has extended reasoning capabilities
- `supports_json_mode`: Whether the model can guarantee valid JSON output
- `supports_function_calling`: Whether the model supports function/tool calling
- `description`: Human-readable description of the model

**Important:** Keep OpenRouter and Custom models in their respective files so that requests are routed correctly.

## Available Models

Popular models available through OpenRouter:
- **GPT-4** - OpenAI's most capable model
- **Claude 4** - Anthropic's models (Opus, Sonnet, Haiku)
- **Mistral** - Including Mistral Large
- **Llama 3** - Meta's open models
- Many more at [openrouter.ai/models](https://openrouter.ai/models)

## Troubleshooting

- **Custom model missing from `listmodels`**: Check the active custom catalog and model policy, restart PAL, and request the exact registered tag or alias.
- **Endpoint reports "model not found"**: Confirm that the endpoint actually serves the catalog's `model_name`. For Ollama, check the tag with `ollama list`.
- **"Insufficient credits"**: Add credits to your OpenRouter account
- **"Model not available"**: Check your OpenRouter dashboard for model access permissions

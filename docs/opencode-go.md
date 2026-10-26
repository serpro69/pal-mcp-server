# OpenCode Go

PAL includes an optional OpenCode Go provider for text generation through Chat
Completions and Responses. It remains inactive without `OPENCODE_GO_API_KEY`.
The initial entries are **offline-tested candidates**: both stay hidden from
default discovery until a separately configured live gateway check is completed.
No live request was made for this implementation, at the user's direction.

Set `OPENCODE_GO_API_KEY` to your Go subscription key. PAL deliberately uses its
own variable rather than activating from OpenCode CLI's general key variable.
`OPENCODE_GO_ALLOWED_MODELS` restricts the provider and
`OPENCODE_GO_MODELS_CONFIG_PATH` selects an explicit catalog. Normal global
`DISABLED_MODELS` and `PREFERRED_MODELS` policies apply. A canonical allowlist or
preference can opt a hidden entry into discovery; hidden entries are also callable
by exact model ID. Those are explicit operator opt-ins, not completed live validation.

| Candidate | Wire endpoint | PAL controls |
| --- | --- | --- |
| `glm-5.2` | `https://opencode.ai/zen/go/v1/chat/completions` | `max_tokens`; temperature fixed at 1; 65,536 output cap |
| `gpt-6-luna` | `https://opencode.ai/zen/go/v1/responses` | `max_output_tokens`; no temperature; medium reasoning; 128,000 output cap |

Use the complete model ID in a normal PAL tool call. Native providers and Custom
retain precedence if another configured provider exposes the same canonical ID;
OpenCode Go is considered before OpenRouter. There are no new request prefixes or
invented model aliases. Both entries currently accept text only through PAL.

Each request sends `User-Agent: pal-mcp-server/<version>` and
`x-opencode-session` with the actual saved conversation UUID, including the first
turn. Continuations retain it; independent concurrent calls have distinct IDs.
Headers are passed to the SDK per request and never stored on its shared client.
The existing invocation context resets on success, failure and cancellation.
Calling this provider directly outside a PAL conversation fails before I/O;
use a PAL tool to establish proper identity. PAL owns conversation history locally
and does not request Responses storage or use remote response IDs for continuation.

Custom catalog entries must declare `api_protocol` as `chat_completions` or
`responses`. Contradictory endpoint/output-field settings and unsupported protocols
fail registry validation. New entries default hidden; maintain verified capability
evidence when adding them. The offline catalog proposal tool accepts this protocol
field in provider evidence and preserves it in generated candidates.

## Evidence and release gates

Checked 2026-10-12:

- [OpenCode Go documentation](https://opencode.ai/docs/go/) defines the endpoint
  families and client/session headers. Its [public catalog](https://opencode.ai/zen/go/v1/models)
  lists both candidate IDs. The documented OpenAI-compatible SDK families use
  bearer-key authentication; offline tests inspect that actual wire header.
- [GLM-5.2 native documentation](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)
  declares a 1M context and 128K output, and shows temperature 1 with a 65,536
  output request. PAL uses that lower output cap; it is not a gateway ceiling claim.
- [GPT-6 Luna native documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
  declares 1,050,000 context, 128,000 output and medium reasoning. The upstream
  [model metadata](https://raw.githubusercontent.com/anomalyco/models.dev/dev/models/openai/gpt-6-luna.toml)
  marks temperature unsupported; PAL omits it. Native limits and community metadata
  are provisional for this gateway until live validation.
- OpenAI Python **2.41.0** is retained. Its installed signatures and tagged
  [Chat Completions](https://github.com/openai/openai-python/blob/v2.41.0/src/openai/resources/chat/completions/completions.py)
  and [Responses](https://github.com/openai/openai-python/blob/v2.41.0/src/openai/resources/responses/responses.py)
  implementations support per-call `extra_headers`. Offline HTTP transports test
  routing, bearer auth, output limits, usage and session isolation through the real SDK.

**Open release gate:** explicitly configure a Go test key and authorize a minimal
coding request per candidate/endpoint family. Verify authentication, temperature,
output limits, response shape and continuity before promoting catalog entries.
There is no claim of live compatibility, service availability or pricing.

**Deferred Messages adapter:** current Go docs assign `claude-haiku-5-5`,
`minimax-m3`, `minimax-m2.7`, `qwen3.8-max`, `qwen3.8-flash` and `qwen3.7-plus`
to `/messages`. They are excluded. Next step: separately design and wire-test
Messages authentication, payload/content blocks, usage, cancellation and output
controls, then perform configured live validation. Other catalog IDs are also
unpromoted until their protocol/capabilities and a useful scope are verified.
Both the live gate and the adapter are tracked in
[#24](https://github.com/serpro69/pal-mcp-server/issues/24).

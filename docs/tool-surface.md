# Tool schema compatibility and measurement

PAL exposes one name per capability. Tool descriptions share concise guidance for
continuations, workflow confidence, expert validation and model selection. Explicit
model choices remain exact; validation errors never authorize silent substitution.
The latest continuation ID still carries context, files and findings across tools.

Planner and tracer run locally. They no longer require `model`, even with
`DEFAULT_MODEL=auto` or an unavailable default. Their `model` and
`use_assistant_model` inputs are deprecated, optional compatibility fields. They
remain accepted with the original string/boolean types throughout PAL 1.x and
cannot trigger a provider call. The advertised expert default is false, matching
their existing request models. Removal requires a separately announced PAL major
release; this is unrelated to the retained MCP 1.x protocol version.

No advertised parameter was removed in this slice: MCP 1.30 validates requests
against the advertised schema before invoking tool code, so removing a field would
break callers even if the Pydantic request model still accepts it. Keeping the small
deprecated fields preserves that validation and the compatibility window. Clink's
local parameters, edit opt-in, allowed-path controls and safety wording remain.

## Measure a tool surface

Run from the repository root in the existing environment:

```bash
python -m scripts.tool_surface --output /tmp/pal-tool-surface.json
```

This snapshots all exported capabilities, including tools hidden by
`DISABLED_TOOLS`, under the current model and CLI configuration. It does not start
the MCP server or call a provider. It records names, complete schema constraints
and defaults without descriptions, annotations, per-tool serialized description
bytes, total serialized tool bytes, clink's full schema, and SHA-256 hashes for
every Python/text file under `systemprompts/`. Byte counts use compact UTF-8 JSON;
they are not token counts or provider-cost estimates. Use identical model/provider
and CLI configuration when comparing snapshots because discovery text is dynamic.

Task 33's auto-mode baseline with no registered providers and shipped CLI configs:

| Measure | Before | After |
| --- | ---: | ---: |
| Tool names | 18 | 18 |
| Serialized description bytes | 34,289 | 29,387 |
| Serialized tool bytes | 54,421 | 49,577 |

Descriptions are 14.3% smaller. The only schema contract changes are planner/tracer
model requiredness and annotations/defaults for their unused compatibility fields.
The regression baseline is [tool_surface_baseline.json](../tests/fixtures/tool_surface_baseline.json);
the feature keeps a [post-change snapshot](feat/wip/server-reliability-and-capabilities/tool-surface-current.json).
Tests compare semantic constraints, exercise MCP decorator validation and local
execution with/without legacy fields, guard clink controls and hash every prompt
module rather than a hand-maintained subset. A future intentional tool/prompt
change must update the regression expectations with its own contract evidence;
do not regenerate a fixture solely to conceal an unexpected difference.

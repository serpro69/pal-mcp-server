# Catalog reconciliation — 2026-10-12

This bounded reconciliation preserves every existing canonical ID and alias.
The snapshots in [tests/fixtures/catalog_evidence](../../../../tests/fixtures/catalog_evidence/) are normalized observations
from public provider documentation and catalog endpoints, captured on 2026-10-12.
They contain no credentials or model responses. No live generation was requested.

## Applied decisions

| Provider / canonical model | Decision and evidence |
| --- | --- |
| OpenAI `gpt-6.1-sol` | Add a hidden candidate without aliases or rank. The [model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) documents 1,050,000 context, 128,000 output, vision, streaming and structured output. Use Responses and medium reasoning; omit temperature per the [GPT-6 parameter guide](https://developers.openai.com/api/docs/guides/latest-model). A configured live smoke and deliberate curation are required before promotion. |
| Google `gemini-3-pro-preview`, `gemini-2.0-flash`, `gemini-2.0-flash-lite` | Hide from discovery/auto selection, retaining canonical IDs and aliases. Google's [shutdown schedule](https://ai.google.dev/gemini-api/docs/deprecations) records March 9 and June 1, 2026 respectively; the [release notes](https://ai.google.dev/gemini-api/docs/changelog) confirm the 2.0 shutdown. Hidden is not a deny: explicit requests still reach the provider and can fail there. Use `gemini-3.1-pro-preview`, `gemini-3.8-flash` or `gemini-3.1-flash-lite` as appropriate. |
| OpenRouter `anthropic/claude-sonnet-4.5` | Context 200,000 → 1,000,000. |
| OpenRouter `anthropic/claude-opus-4.1` | Output ceiling 64,000 → 32,000. |
| OpenRouter `google/gemini-2.5-flash` | Output ceiling 65,536 → 65,535. |
| OpenRouter `deepseek/deepseek-r1-0528` | Context 65,536 → 163,840. |
| OpenRouter `openai/gpt-5.2-pro` | Output ceiling 272,000 → 128,000. |

All five OpenRouter changes come from its own [model endpoint](https://openrouter.ai/api/v1/models),
specifically `context_length` and `top_provider.max_completion_tokens`.
The response contained 458 entries, including 26 of our 35 canonical names.
The retained snapshot covers those 26 matched names and their capacities; it is
not a copy of the entire response. Aggregator capacities apply only to OpenRouter,
not native providers. Output reservation still uses Task 20's minimum policy.
No category fallback names, ranking scores or short aliases change.

## Comparison and remaining review work

The four `*-audit.json` files are fresh Task 27 reports over the shipped catalogs.
`preserved-identities.json` records the exact pre-change canonical/alias surface.
`models-dev.json` retains only matching local names plus the Sol candidate from
the public community catalog. It deliberately does not represent a complete
candidate inventory. Provider `complete` means the selected source observation
finished successfully, not that every vendor model or capability was verified.
The OpenAI snapshot covers only the new candidate; Google covers lifecycle only;
xAI covers the confirmed redirect; OpenRouter covers matching capacities.

Unresolved items are intentionally visible in the reports:

- **Native OpenAI:** older Codex names absent from the community snapshot remain
  intact. Native limits differing from community data (including GPT-5.2 Pro and
  O3) remain review work. Next: verify each native model page and endpoint controls
  before adjusting capacities. No native retirement is inferred from omission.
- **Google:** retain current Gemini/Flash aliases and 2.5 entries. Google documents
  restricted new-account access to 2.5, not retirement. Snapshot capacities are
  missing because the lifecycle page does not establish them. Next: verify the
  model-specific pages before further capability edits. `gemini-3.5-flash-lite`
  remains an unadded candidate until its GenerateContent controls are verified.
- **OpenRouter:** the nine `absent_from_provider_snapshot` IDs remain intact;
  endpoint omission does not establish retirement or justify alias migration.
  Next: inspect the provider's per-model routes/retirement notices for those exact
  IDs, particularly `google/gemini-3-pro-preview`, before hiding/removing them.
  Existing Claude Fable/Opus/Sonnet additions remain unchanged.
- **xAI:** [the retirement/redirect notice](https://docs.x.ai/developers/migration/may-15-retirement)
  confirms `grok-4-1-fast-reasoning` redirects to `grok-4.3` with low reasoning.
  Its old 2M limit is not re-certified here. No matching local xAI models remain
  in the secondary snapshot, so the audit correctly returns **incomplete**, exit 2.
  Next: verify redirect-target output limits and Chat Completions controls before
  changing legacy metadata or adding current Grok candidates. Preserve local IDs
  and aliases until that evidence is available; operators can deny them now with
  `DISABLED_MODELS` if unsuitable for their workload.

The first three audits return **review_needed**, exit 1; none claims a clean
full-provider certification. OpenRouter's observed capacity drift is resolved.
Reproduce using `python -m scripts.audit_model_registry` with the corresponding
registry/provider/evidence files, `--models-dev tests/fixtures/catalog_evidence/models-dev.json`
and `--as-of 2026-10-12` (paths relative to the repository root). Regression tests compare
reports exactly and exercise the changed request limits through real SDK serializers
with fake transports. Promotion and live routing remain separate release gates.

Independent review exposed an existing Responses image conversion defect through
the new candidate's vision declaration. The shared converter now emits separate
`input_text` and `input_image` parts, preserving image URLs/detail and rejecting
unknown part types. Request-log sanitization redacts image data. Tests exercise
native Sol and OpenRouter Responses with/without an actual validated data URL.
The shape was verified against the locked OpenAI 2.41.0 SDK and the official
[image-input guide](https://developers.openai.com/api/docs/guides/images).

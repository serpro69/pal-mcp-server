# Cache usage and request measurements

PAL preserves reported cache counts without enabling new provider cache controls.
Missing, null, negative, boolean and noninteger counts remain
unknown; a reported zero remains zero. These measurements do not establish costs
or model quality.

## Endpoint fields

Verified 2026-10-12 using official endpoint documentation and the installed
OpenAI 2.41.0 / google-genai 2.8.0 SDKs. No dependencies were upgraded.

| Endpoint response | Normalized usage |
| --- | --- |
| Chat Completions `usage.prompt_tokens_details.cached_tokens` | `cached_input_tokens` |
| Chat Completions `usage.prompt_tokens_details.cache_write_tokens` | `cache_write_tokens` |
| Responses `usage.input_tokens_details.cached_tokens` | `cached_input_tokens` |
| Responses `usage.input_tokens_details.cache_write_tokens` | `cache_write_tokens` |
| Gemini `usage_metadata.cached_content_token_count` | `cached_input_tokens` |

The OpenAI SDK retains newer response fields as extras, so cache-write counts can
be read without an SDK upgrade. The shared compatible parser also covers Azure,
DIAL, Custom, xAI, OpenRouter and OpenCode Go where their responses provide the
same fields. This does not assert those services always report cache usage.
Gemini has no verified per-generation cache-write field here. Responses counts
come only from its `usage` object; unsupported top-level fields no longer invent
input or total counts.

Sources: [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching),
[Chat usage fields](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/retrieve),
[OpenRouter usage](https://openrouter.ai/docs/guides/best-practices/prompt-caching),
[Gemini UsageMetadata](https://ai.google.dev/api/generate-content#UsageMetadata).

## Transcript accounting

With `PAL_TRANSCRIPTS=true`, each saved assistant turn carries
`model_metadata.provider_calls`: ordered records with a per-invocation
`call_index`, `provider`, `model` and known `usage` counts. Recovery calls are
separate records. Local workflow/synthesis steps carry an empty list; restored
consensus responses are never counted again. Existing simple-tool `usage` metadata
remains compatible, but the report reads only the new per-call list.

Run `python -m scripts.cache_report /absolute/path/to/thread.jsonl` with explicit
transcript paths. The JSON report groups by provider/model, gives each field's
known/missing call counts, and reports null sums when all counts are unknown.
Cache-read ratios use only calls with both input and cached-input counts and a
positive combined denominator. Inconsistent read counts above input are flagged
and excluded from that ratio. Duplicate thread/turn records are counted once;
malformed records, invalid call entries and legacy turns are reported separately.
Records over 4 MiB fail explicitly. `--output` writes the report to a file.

This is a diagnostic report over persisted replies, not a billing ledger: missing
transcripts, failed final persistence, cancelled/failed invocations without a saved
assistant reply, and provider attempts that returned no usage cannot supply complete
accounting. It does not infer token counts from workflow aggregates or old turns.
Retention/privacy/platform limitations follow [transcript configuration](logging.md#private-conversation-transcripts).

## Reproduce the offline baseline

From the repository with its dev environment active:

The full capture harness requires private POSIX transcript support and fails if
persisted usage does not cover every captured call. Report readers can process
previously exported fixtures independently of recording support.

```sh
PAL_SKIP_DOTENV=true PYTHONHASHSEED=1 python -m tests.cache_capture --output /tmp/cache-seed1.json
PAL_SKIP_DOTENV=true PYTHONHASHSEED=7 python -m tests.cache_capture --output /tmp/cache-seed7.json
python -m scripts.cache_prefix /tmp/cache-seed1.json --compare /tmp/cache-seed7.json --output /tmp/cache-prefix.json
```

The harness invokes the real server dispatcher and actual OpenAI SDK serializers,
with only HTTP replaced by a fake transport. It uses synthetic files, prompts,
replies and usage. Nineteen captured requests cover 14 chat invocations (13
continuations), a model switch, unchanged/added/touched/edited files, workflow
restoration and cross-tool history, blinded consensus participants, Responses and
codereview. It checks exact-once required sentinels, forbidden sentinels, full
applicable system instructions and independent audit history. Consensus instructions
are stance-specific; generic history and previous verdicts are intentionally excluded.

Saved requests contain no headers or credentials. Fixture directory/UUID values
are normalized and fixture mtimes fixed for reproducibility. This normalization
affects evidence only. Prefix lengths count bytes of canonical serialized JSON,
not tokens or the provider's internal cache key. Comparisons stay within the same
scenario/provider/model. A failed completeness contract suppresses prefix scores
and exits nonzero; cross-run comparisons require the same ordered scenarios.

The retained [baseline capture](feat/wip/server-reliability-and-capabilities/cache-capture-baseline.json)
passes all declared contracts. Its 12 comparable chat pairs share **4,078–4,155
bytes** (median **4,154.5**), approximately **41–68%** of the previous request.
Seeds 1/7/42 each pass completeness; the workflow expert request differs across
processes while the other 18 requests match. These are observations, not savings.
This is the pre-Task-40 baseline, retained for comparison.

## Deterministic continuation rendering

Task 40 moves the conversation turn total to the history footer, preserving its
budget reservation, and sorts set-derived workflow files and symbols at rendering
boundaries. Caller-ordered file/image lists and chronological findings retain their
order. File errors, omission markers, modification times and model/tool attribution
remain available. Consensus retains its complete stance text and shared safety and
response instructions, with the stance following the shared system instructions.

The [Task 40 comparison](feat/wip/server-reliability-and-capabilities/cache-stability-task40.json)
passes all content contracts and matches all **19/19 requests** across seeds
1/7/42. Comparable chat prefixes have median **5,220.5 bytes**, up from **4,154.5**;
the range is **4,078–7,800 bytes**. Two same-model consensus calls share **6,170
bytes**, up from **1,190**, with earlier verdicts still excluded. Reproduce the
same-model case with `python -m tests.cache_capture --consensus-model gpt-4.1
--output /tmp/cache-same-model.json` (on one shell line).

These are synthetic serialized-request measurements. Live cache benefit and answer
quality remain unverified. Rollback restores rendering only; no memory/schema
migration is needed.

## History layout evaluation

Task 41 retains the current runtime layout. Fourteen calls each with fixed, added,
touched and edited files, plus a 1,000-token history-selection case, compare the
current layout to three test-only candidates. First-seen order can displace a
new attachment under a file limit; moving modification times helps touch-only
changes but conflicts with the annotation header contract; the tested compaction
hysteresis discards more context without reducing compaction events.

The [decision and follow-ups](adr/0001-retain-conversation-history-layout.md)
link measured results, compatibility constraints and rollback. Reproduce all
280 synthetic dispatcher/SDK requests with:

```sh
PAL_SKIP_DOTENV=true PYTHONHASHSEED=1 python -m tests.history_layout_capture --output /tmp/layout-captures.json --summary /tmp/layout-summary.json
```

The full output retains requests, content contracts and audit/coverage results;
the committed summary retains prefixes and coverage per call. File bytes are
reread on every request, and compaction checks a contiguous newest suffix,
exact-once current input and accurate omission counts. This experiment makes no
live cost/quality claim and introduces no runtime cache, layout option or snapshots.

Separate [reproduced prompt issues](feat/wip/server-reliability-and-capabilities/capture-followups.md)
remain open: absent chat search guidance, unused thinkdeep customization, and
codereview literal newline escapes. The green core-content gate does not certify
those optional guidance paths or erase the follow-ups.

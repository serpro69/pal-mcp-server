# Consensus replay request migration

On 2026-10-18, Task 6 of `clink-project-directories` reproduced the existing
consensus replay failures and updated these stale request bodies:

- `openai_cassettes/consensus_step1_gpt5_for.json`
- `openai_cassettes/consensus_step1_gpt52_for.json`
- `gemini_cassettes/consensus/step2_gemini25_flash_against/mldev.json`

The current tool appends the stance block after the main system prompt instead
of inserting it near the beginning, and supplies an explicit response cap:
80,000 tokens for the recorded OpenAI requests and 65,536 for Gemini. Before
editing, assertions verified that the main prompt, stance block, user question
and all other generation parameters were unchanged. Only placement of that
existing block and the explicit cap changed.

This was an **offline fixture migration, not a new vendor recording**. Original
request headers and recorded responses remain unchanged. Exact request matching
and every consensus assertion remain enabled. The OpenAI files carry migration
metadata; Gemini's original request-text SHA-256 was
`c0c2baafb027cdfe6483faed1711303e9100acb6bf875b05eebbc549cee7e7b7`.
The original responses are replay evidence, not live acceptance of current models.

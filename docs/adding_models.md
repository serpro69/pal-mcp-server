# Adding a New Model to a Native Catalogue

This guide covers adding a new model to one of the native provider catalogues
(`conf/gemini_models.json`, `conf/openai_models.json`, `conf/xai_models.json`,
etc.). For adding a whole new *provider*, see [adding_providers.md](adding_providers.md).
For the registry overview, see [custom_models.md](custom_models.md).

## When to add an entry

Add a model when:
- A provider ships a new model you want exposed via PAL.
- A preview model goes GA and the canonical name changes (preview → stable).
- An existing model gains capabilities (e.g. thinking support added in a stable release).

Aliases are case-insensitive and the model name itself is always usable — you
don't need to list it as an alias.

## Step 1 — Add the JSON entry

Edit the relevant `conf/<provider>_models.json`. Insert near other models in the
same family (top of family for newest). Schema fields are documented in the
`_README` block at the top of each file; the most consequential ones:

| Field | Notes |
|-------|-------|
| `model_name` | Canonical API identifier the provider expects |
| `aliases` | Short names users type. Don't list `model_name` itself |
| `intelligence_score` | 1–20 human rating; primary signal for auto-mode ordering ([model_ranking.md](model_ranking.md)) |
| `supports_extended_thinking` | Set true *only if* the API actually accepts a thinking budget for this model |
| `max_thinking_tokens` | Required when `supports_extended_thinking` is true |
| `allow_code_generation` | True only for models *more capable* than the calling AI tool |
| `context_window` / `max_output_tokens` | From the provider's model docs |

Validate the JSON parses:

```bash
python3 -c "import json; json.load(open('conf/gemini_models.json'))"
```

## Step 2 — Decide whether short aliases migrate

Short aliases like `flash` or `pro` should point at the **current flagship** of
that family. Precedent set by 881d6ca (Gemini 3.1 Pro Preview) and the 3.5-flash
addition:

- **`pro`** migrates to the newest "pro" tier model — even if it's still in preview.
- **`flash`** migrates to the newest **stable** flash model. Preview flash models
  keep their version-suffixed aliases (`flash3`, etc.) but don't claim `flash`.
- The previous holder keeps a version-pinned alias (`flash2.5`, `gemini-3-pro`)
  so existing users can opt back in.

If you decide to migrate: remove the alias from the old entry, add it to the
new entry, and expect test updates (see Step 4).

## Step 3 — Restart and smoke-test

```bash
workon pal
./code_quality_checks.sh
```

If you renamed a preview to stable, also grep for the old canonical name:

```bash
grep -rn "gemini-3.1-flash-lite-preview" .
```

Update any stragglers — references in test fixtures and other catalogues
(e.g. `conf/openrouter_models.json` may mirror the same model).

## Step 4 — Update tests when selection changes

The Gemini provider's `get_preferred_model` (`providers/gemini.py`) uses a
**reverse-alphabetical** sort when choosing between candidates of the same
class. So adding `gemini-3.5-flash` makes it the new winner for the
`FAST_RESPONSE` and `BALANCED` categories (it sorts above
`gemini-3.1-flash-lite` and `gemini-3-flash-preview`).

For `EXTENDED_REASONING`, the filter `"pro" in model_name && supports thinking`
runs first, so non-pro models don't win that category regardless of
intelligence_score.

When the new model wins a category or claims a short alias, these tests
typically need updates:

| Test | What to update |
|------|----------------|
| `tests/test_supported_models_aliases.py` | Alias → canonical resolution assertions |
| `tests/test_listmodels.py` | `listmodels` output snippets |
| `tests/test_auto_mode_provider_selection.py` | Expected models per category |
| `tests/test_auto_mode_comprehensive.py` | Parametrized provider-config matrices |
| `tests/test_per_tool_model_defaults.py` | Allowed-models lists |
| `tests/test_intelligent_fallback.py` | Fallback selection when only one provider is registered |
| `tests/test_model_restrictions.py` | `validate_model_name` assertions when the migrated alias is restricted |
| `tests/test_alias_target_restrictions.py` | Same — alias/target restriction matrix |
| `tests/test_providers.py` | `get_capabilities(alias).model_name` assertions |

Run them iteratively:

```bash
python -m pytest tests/ -m 'not integration'
```

The failures themselves point at what to update — read the assertion, decide
whether the *new* behavior is correct, and adjust.

## Reference commits

- `881d6ca` (feat: Update gemini models) — added 3.x family, migrated `pro` alias.
- `e4201af` (fixup! feat: Update gemini models) — the corresponding test fixup; the diff is a useful template.
- `033bdcf` (feat: add gemini-3.5-flash, promote 3.1-flash-lite to stable) — most recent, the worked example behind this guide.

## Common gotchas

- **Don't list `model_name` in `aliases`.** It's redundant; the resolver checks
  both canonical and aliases.
- **Don't enable `supports_extended_thinking` without verifying the API
  accepts a thinking budget** for that model. Some Flash models advertise
  "Thinking: Supported" in docs but reject specific budget values — check the
  provider override block in `providers/gemini.py` for known exceptions.
- **Test-isolation failures.** A small set of fallback tests fail in suite
  order but pass in isolation; this is pre-existing and unrelated to
  catalogue edits. Confirm by running the suspect tests alone before
  attributing them to your change.
- **`find_best` is reverse-alphabetical, not score-sorted.** If you need a
  lower-versioned model to win a category, you'd need to change the selection
  logic in `providers/gemini.py`, not the catalogue.

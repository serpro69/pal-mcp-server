# Retain the current conversation-history layout

Date: 2026-10-12. Status: accepted for Task 41; reopen with new evidence.

Keep fresh files before chronological, budget-selected history. Retain newest-first
file admission, existing modification-time headers and the current compaction
policy. Task 40's deterministic ordering and footer counter remain in place.

The [Task 41 experiment](../feat/done/server-reliability-and-capabilities/history-layout-task41.json)
captures **280 real dispatcher/SDK requests** with fake HTTP: 14 calls for each
combination of five scenarios and four layouts. All required current files and
retained turns appear exactly once; full system instructions survive; superseded file sentinels
are absent, omitted turns have accurate markers, and all 28 audit turns survive
each run. Candidates are scoped test patches, not runtime settings.

| Candidate | Observed benefit | Reason not to ship |
| --- | --- | --- |
| First-seen file order | Sum of 13 comparable prefixes for added files rises from 109,306 to 115,106 bytes | Still four early rewrites; changing admission order can evict the current attachment in favor of an older file |
| Modification time after file content | Touched-file median prefix rises from 4,426 to 4,932 bytes | Edited-file median falls to 4,416; repeated provenance adds bytes, and the annotation header contract would change |
| Drop 25% of retained turns when compaction starts | No reduction in compaction events or median prefix churn in this small-window case | Both policies compact 11 times; summed visible prior turns fall from 61 to 50 |

These are serialized UTF-8 prefix measurements, not cache tokens or savings.
The first request has no continuation history; the first continuation changes
the request envelope. Those transitions remain in the 13-pair measurements.
The constrained run uses a 1,000-token turn-selection allowance; the existing
fixed history envelope/instructions bring the measured maximum to 1,127 estimated
tokens (candidate maximum 1,147). Full-request admission remains enforced by the
runtime. Fixture paths are identical during comparisons and normalized only after
rendering, so path length cannot bias admission between candidates.

The separate [file-description design](../feat/wip/per-file-descriptions/design.md)
requires unchanged headers when descriptions are absent, annotations adjacent to
content, fixed descriptions after first embedding, and no new cross-continuation
annotation persistence. Runtime headers remain unchanged. The test-only provenance
footer explores the cost of a coordinated format change; it is not an approved
annotation implementation. Immutable file snapshots/version logs were not added:
they would need an explicit authoritative-current-version rule, bounded storage,
and coordinated annotation semantics before a meaningful safe prototype.

Follow-ups are deliberately separate:

1. Separate newest-first **admission** from first-seen **presentation** after path
   expansion/alias deduplication; then repeat all five file-feed paths, constrained
   file budgets and the current-attachment regression before considering rollout.
2. If representative traffic shows touch-only churn matters, agree on a versioned
   header/provenance format with the annotation feature, then measure real cache
   usage and response quality. Preserve explicit file errors and fresh bytes.
3. Reevaluate compaction with windows/headroom measured in complete exchanges,
   varied turn sizes and long real workloads. Require fewer rewrites without
   unjustified loss of usable context; this experiment tests one policy/allowance.
4. Strict-prefix instruction relocation and provider-explicit caches need separate
   provider API evidence and live quality/cost validation.

Rollback: no runtime or stored format changes landed in Task 41. Remove the
experiment harness/report if needed; Task 40 can be reverted independently without
migrating conversations. This decision is limited to the tested candidates and
does not establish that every append-oriented layout or hysteresis policy fails.

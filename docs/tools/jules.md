# Jules background coding sessions

The `jules` tool manages remote coding sessions using `JULES_API_KEY` from
[Jules settings](https://jules.google.com/settings). It needs no PAL model provider.
Connect the intended repository in Jules first. With no key, discovery still lists
the tool and calls return an actionable configuration error. Disable discovery
with `DISABLED_TOOLS=jules` if desired.

## Actions

| Action | Inputs | Result |
| --- | --- | --- |
| `list_sources` | Optional `page_size` (1–100, default 30), `page_token` | Connected sources and `next_page_token` |
| `create` | `prompt`, exact `source` from discovery, `starting_branch`; optional `title`, `require_plan_approval`, `auto_create_pr` | Created session and polling guidance |
| `status` | `session_id`; optional paging inputs | Session state/outputs, one activity page, `next_page_token` |

Use an explicit source such as `sources/github/owner/repository` and an explicit
branch. PAL sets `require_plan_approval=true` and `auto_create_pr=false` by default.
Only deliberately supplied `auto_create_pr=true` requests `AUTO_CREATE_PR`.
Setting `require_plan_approval=false` permits coding without a separate approval.
Neither installing PAL nor setting a key creates a session.

Copy the opaque ID from `session.name` without its `sessions/` prefix into
`session_id`. IDs cannot contain path separators, URL query/fragment syntax,
percent escapes, whitespace or dot traversal. Source names can contain multiple
safe components. Creation requires repository context even though newer Jules
versions may support repository-free sessions.

Pass each returned `next_page_token` as `page_token` with the same action/session
to retrieve more entries. Empty or absent tokens mean the end of the page list.
Status never waits for coding to finish: poll through separate calls. Unknown
future states and remote outputs are preserved for inspection.

## Failures and lifecycle

Errors distinguish invalid input, missing configuration, authentication,
permissions, missing resources, invalid state, rate limits, transport/timeouts,
and malformed responses. Remote error bodies and credentials are not copied into
diagnostics. Requests use HTTPS, no redirects, a 10-second connect timeout and a
30-second deadline per HTTP request. Clients close after each action, including
cancellation; MCP progress is request-scoped when requested by the client.

No action is automatically retried. A failed creation with `outcome_unknown=true`
may already have created a session. Inspect Jules to reconcile that outcome before
repeating it. Cancellation can also leave remote work running. The single tool's
MCP annotations conservatively declare mutations, non-idempotence and external
access because annotations apply to the whole tool, including create.

## Verified API and release gate

Contract checked 2026-10-12 against the official
[session resource](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions),
[session endpoints](https://jules.google/docs/api/reference/sessions/),
[source pagination](https://developers.google.com/jules/api/reference/rest/v1alpha/sources/list),
[activities](https://jules.google/docs/api/reference/activities/) and
[authentication](https://jules.google/docs/api/reference/overview/).
Uses `https://jules.googleapis.com/v1alpha` with `X-Goog-Api-Key` authentication.
HTTPX 0.28.1 is directly declared and pinned through pip-tools.

Offline HTTP/MCP tests validate payloads and failure behavior. **Live validation
remains open:** no test repository or live creation was authorized for this work.
Next step: designate a disposable repository/branch and explicitly run discovery,
creation with plan approval on and PR automation off, then inspect status. Do not
claim live service validation from the fake transport tests. Steering and archive
support are tracked by Task 38.

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
| `message` | `session_id`, `prompt` | Message acknowledgment and refreshed session |
| `approve` | `session_id` | Approval acknowledgment and refreshed session |
| `archive` / `unarchive` | `session_id` | Updated session, including the reported `archived` flag |

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

Inspect the plan through status before explicitly invoking approve. PAL verifies
the observed state is `AWAITING_PLAN_APPROVAL`; Jules remains authoritative if the
state changes before the POST. Message forwards feedback without inventing a
state transition. Successful message/approve calls fetch status once, without
polling. If that read fails, `accepted=true` and a warning preserve the successful
mutation; call status again instead of repeating the action.

Archive/unarchive use their dedicated endpoints with empty JSON requests and
return the service's session object. Do not treat archiving as cancellation or
infer that coding stopped. State and archival visibility are separate fields.
The deletion API is outside this tool's scope.

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
claim live service validation from the fake transport tests. Message/approve and
archive/unarchive need explicit live exercise on that same designated session.
Tracked in [#25](https://github.com/serpro69/pal-mcp-server/issues/25).

## Lifecycle evidence (Task 38)

The official [discovery document](https://jules.googleapis.com/$discovery/rest?version=v1alpha),
retrieved 2026-10-12, reports revision **20261008**. Its session methods include
`archive` and `unarchive`, although the older developer REST index omits them.
Both are POSTs to `v1alpha/{+name}:archive` or `:unarchive`, accept empty request
objects, and return `Session`. The output-only boolean `Session.archived` is
separate from `Session.state`. This closes the missing-API-evidence gate;
offline tests cover both directions and unchanged execution state.

`sendMessage` accepts a `prompt`; `approvePlan` accepts an empty request. Both
return empty response objects; PAL also accepts an empty HTTP response body as
documented by the [message](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions/sendMessage)
and [approval](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions/approvePlan)
references. Unknown states remain inspectable, but cannot pass PAL's approval
precondition. No credentials were used to retrieve discovery data.

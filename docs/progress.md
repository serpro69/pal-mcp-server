# Request progress

PAL emits MCP 1.x progress notifications when a tool request supplies
`_meta.progressToken`, including numeric `0`. No token means no notifications.
The reporter belongs to that request and resets when it exits, so concurrent
requests cannot borrow one another's token, counter or usage.

Notifications carry a strictly increasing counter with no percentage/total.
The message is JSON containing a fixed stage name, elapsed seconds, and known
input/output/total token counts when an operation has returned them. Stages cover
request start/completion/failure and provider, recovery, expert, consensus and CLI waits.
During an awaited provider/CLI operation a heartbeat is sent every 10 seconds.
Messages contain no prompt, file path, model answer, command or credential.
Completion means the invocation ended; inspect the tool's status for success.

Each notification has a one-second transport deadline. If delivery raises or
times out, PAL logs a diagnostic and disables further notifications for that
request. The answer is preserved and includes a progress warning. Cancellation
propagates after the operation's heartbeat is cancelled and joined; CLI child
cleanup retains its existing kill/reap behavior. Providers continue using their
existing request timeouts and retry policy. Notification transport cancellation
must cooperate with the SDK's async cancellation contract.

Client handling varies. These notifications do not guarantee that a particular
MCP client extends or resets its tool timeout.

## Result metadata

MCP-dispatched PAL JSON responses, including `isError` tool errors, add
`metadata.execution` independently of whether progress was requested. Plain
`ToolExecutionError` messages become JSON errors with their original text in
`content`. Cancellation still propagates without a fabricated result. Metadata contains:

- `duration_seconds`: elapsed time for this invocation, including coordination.
- `operations`: an ordered list with `stage`, `status`, `duration_seconds` and
  `usage` for each provider or CLI operation performed during this invocation.
- `progress_warnings`: delivery diagnostics, with exception type only.

Operation `usage` is null when unknown; valid zero counts remain zero. Only
nonnegative integer `input_tokens`, `output_tokens` and `total_tokens` fields are
copied. Recovery calls are separate operations. Resumed workflow/consensus calls
report only the current invocation, without re-counting earlier participants or
synthesizing a cumulative total. CLI parsers with canonical `usage` fields (Claude
and Codex) contribute available counts. Gemini CLI's vendor-specific `token_usage`
remains in its existing result metadata; no unverified normalization is attempted.

Plain-text results keep their text and put this record in the MCP content block's
`_meta.pal_execution`. Direct Python tool calls outside MCP have no reporter;
internal code can explicitly use `request_progress` when needed. These fields
are additive and are not a persisted transcript or conversation recovery format.

The implementation uses MCP **1.30.0**
[`request_context`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/lowlevel/server.py)
and [`send_progress_notification`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/session.py),
verified against the installed locked SDK and an actual SDK session round trip.

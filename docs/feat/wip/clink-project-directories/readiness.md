# Fresh implementation-readiness review

> Date: 2026-10-15
> Reviewed checkout: `7cd82ee`
> Scope: reread all five feature documents, relevant execution/validation code,
> and the original nine findings; no new skill or reviewer agent used
> Verdict: architecture is sound; one narrow contract correction remains

## Overall assessment

The design, implementation plan and task list agree on project selection,
scratch-to-first-project promotion, immutable established projects, separate
launch/identity paths, filesystem equivalence, request-local configuration and
atomic persistence. The proposed changes fit the existing factory, runner and
conversation ownership. The six tasks have concrete checks, defined dependencies
and useful parallel subtasks. No architectural redesign or further user decision
is needed for these mechanisms.

The historical verification record is clearly labeled as historical. The native
configuration-activation limitation and possible operator directory policy are
explicit; the feature makes no claim to confine restricted callers. Native CLI
acceptance and the existing dependency-related test failure remain verification
work, not unresolved product decisions.

I would correct the error-boundary promise below before calling the documents
fully ready for implementation. The previous statement that all nine findings
were completely resolved was too strong: R9's domain error shapes are specified,
but their relationship to SDK/request-model validation was missed.

## P2: schema rejection cannot return the promised clink error envelope

**Confidence: high. Related original finding: R9.**

[The rejection contract](design.md#rejection-contract) promises
`InvalidWorkingDirectoryError` and clink metadata for an invalid request
**path/type**, including non-string input. However, the proposed MCP schema
accepts only a string or null. PAL registers its handler with
[`@server.call_tool()`](../../../../server.py), which leaves SDK input validation
enabled. The SDK validates against the tool schema and returns its own error
before invoking PAL. This behavior is present in both the installed baseline
SDK version and the committed dependency version.

Primary sources:
[MCP SDK 1.27.0](https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/src/mcp/server/lowlevel/server.py#L450),
[MCP SDK 1.30.0](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/lowlevel/server.py#L454).

A local in-memory SDK probe registered a string-or-null directory field and
submitted the integer `42`. Observed: handler not reached, `isError=True`, no
structured content, and text reporting an input-validation error. No child CLI,
model inference, network call or repository source modification was involved.

Direct Python calls have a related earlier boundary: `prepare_request()` creates
the Pydantic request before loading the conversation or calling a directory
helper. Its type error currently reaches the generic `ValueError` handler in
`_execute_coordinated()`. Task 3.1 only describes mapping typed directory-helper
errors, which does not cover these model-level failures.
Sources: [request preparation](../../../../clink/conversation.py),
[tool execution](../../../../tools/clink.py).

**Required correction:** distinguish schema/model rejection from directory-domain
rejection in design.md and Tasks 2.1, 3.1 and 5.1. Recommended resolution:

- Keep the strict schema and existing SDK validation enabled. Wrong JSON types
  retain the SDK's ordinary error result; no clink-specific metadata is promised.
- Direct-call request-model failures retain their existing validation envelope.
  Do not load thread state just to decorate a malformed request with bound paths.
- Reserve the specified directory error envelope for accepted request types that
  reach directory resolution: invalid path strings, missing directories,
  conflicting projects and malformed/unavailable saved bindings.
- Test both boundaries. Invoke the SDK's registered request handler or real
  stdio for a wrong-type case, and exercise domain failures through direct and
  MCP paths. Calling `server.handle_call_tool()` directly bypasses SDK validation
  and cannot establish the transport contract.

This is a small specification/test-plan correction, not a reason to disable
validation globally or broaden the implementation into SDK error rewriting.
The finding is recorded rather than silently left to the implementer. This turn
assesses readiness; it does not change the design contract or implement the fix.

## Original findings coverage

| Finding | Readiness result |
| --- | --- |
| R1: symlink-resolved launch | Addressed: separate launch/canonical paths, preserved manifest spelling and Claude argument tests. Native spelling guarantees are appropriately limited. |
| R2: scratch immutability | Addressed with the user's choice: explicit first-project promotion retains history and commits atomically; established projects remain immutable. |
| R3: chat API consistency | Addressed: shared public name and home expansion, intentional artifact/execution distinction, no inheritance of raw chat state. |
| R4: native configuration activation | Addressed in the design and operator-doc task, with an explicit possible policy follow-up and restricted-client limitation. |
| R5: Codex non-Git behavior | Addressed: native rejection and intentional operator override are distinct from automatic scratch behavior. |
| R6: filesystem equivalence | Addressed: named `samefile()` operation, path revalidation, case/symlink tests, and explicit filesystem-race limits. |
| R7: duplicate fake CLI | Addressed: extend the shared fixture and preserve its existing scenarios. |
| R8: task size/parallelism | Addressed: six bounded tasks, defined early-start subtasks and an unambiguous dependency list. |
| R9: pre-spawn payload | Partially addressed: complete domain payloads exist; schema/model rejection needs the correction above. |

## Checks and limits

The pre-report comprehensive check passed lint/format and 1,302 tests before the
existing TLS hostname assertion failed; 23 integration cases were deselected.
This reproduces the documented baseline and does not invalidate the design's
core architecture. The read-only SDK probe independently establishes the new
finding. No proposed runtime feature was executed or claimed as tested.

The post-report comprehensive check produced the same result: lint/format passed,
1,302 tests passed, the TLS hostname test failed, and 23 cases were deselected.
Local links and whitespace passed across all six documents (78 local links).
Temporary check outputs are `/tmp/clink-project-directories-readiness-before.log`
and `/tmp/clink-project-directories-readiness-after.log`; this record retains the
outcomes independently of those temporary files.

The design package itself is unchanged by this review; only this readiness record
is added. No new skill was loaded, no subagent was spawned, and no native CLI
configuration or authentication state was changed.

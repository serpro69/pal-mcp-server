# Context Revival: AI Memory Beyond Context Limits

## Turn ownership and capacity

Each conversation call records one raw user input before generation and one raw
reply after success. Chat artifacts and CLI output shortening affect presentation,
not the stored reply. Workflow steps are user turns; steps handled locally store
their actual tool result with null model/provider fields. A failed provider call
leaves its saved input without an invented assistant turn.

`MAX_CONVERSATION_TURNS` counts individual stored messages. A call needs two free
slots before its input is saved or provider/CLI work begins. `remaining_turns` is
the number of free message slots; the number of complete exchanges is half that
value, rounded down. A cap of one admits no conversation calls. Below two free
slots, results direct callers to start a new conversation rather than offering
another exchange. Provider-error metadata reports the saved thread ID and the
remaining slots/exchanges.

MCP and direct `execute` calls use the same internal invocation context. Caller
JSON and history-looking text cannot assert that admission or input saving already
happened. Continuations re-read the saved input and current attachments when
constructing context. Consensus participants receive the original proposal, their
own stance and the current call's explicit attachments; audit history retains all
turns separately. CLI continuations preserve earlier file/image references and
use a provider-independent limit of 20,000 estimated history tokens. The current
input is kept outside history truncation, so older files or turns cannot displace it.

MCP calls use fresh tool instances and serialize updates to the same continuation
ID through reply persistence. Different conversations remain concurrent, and
cancellation releases the coordinator. Workflow configuration is restored from
metadata scoped to the owning tool; older metadata uses defaults. Direct Python
calls do not acquire this lock automatically: concurrent callers must use
`coordinate_conversation` explicitly and a separate tool instance per invocation.
See [request ownership](architecture.md#request-and-conversation-ownership).

## Memory failures

Conversation memory lasts only for the current server process and expires after
its configured timeout. An absent or expired continuation is an explicit error.
Corrupt stored data and storage read/write failures are reported separately; a
required continuation read or input save failure stops the request before model
execution.

If saving fails after a response has been generated, the answer is still returned.
Its metadata includes `memory_persistence_warning` (a code and message) and
`conversation_ready: false`; no continuation offer claims that saving succeeded.
Start a new conversation with full context after such a warning. In-process callers
can catch the exceptions in `utils.conversation_errors`; storage/decode exceptions
retain their original cause. `add_turn(required=True)` also distinguishes a missing
thread from a full message cap, while the default retains `False` for those cases.

## How Context Revival Works

The client can resume a PAL thread after its own context is compacted, provided
the same server process is still running and the thread has not expired.

### Reconstructing a request

1. Memory-backed tools create a UUID thread in the server process.
2. Compatible tools reuse its `continuation_id` to load stored turns and file references.
3. PAL reconstructs history within the receiving model's budget and tool contract.
4. Files are re-read, older turns may be omitted with markers, and consensus
   participants remain blinded to prior verdicts.
5. The receiving model can use the supplied context to help the client resume work.

### The Dual Prioritization Strategy

The system employs a sophisticated **"newest-first"** approach that ensures optimal context preservation:

**File Prioritization**:
- Walks backwards through conversation turns (newest to oldest)
- When the same file appears multiple times, only the **newest reference** is kept
- Ensures most recent file context is preserved when token limits require exclusions

**Conversation Turn Prioritization**:
- **Collection Phase**: Processes turns newest-to-oldest to prioritize recent context
- **Presentation Phase**: Reverses to chronological order for natural LLM flow
- When token budget is tight, **older turns are excluded first**

**Show Case**:

The following video demonstartes `continuation` via a casual `continue with gemini...` prompt and the slash command `/continue`.

* We ask Claude code to pick one, then `chat` with `gemini` to make a final decision
* Gemini responds, confirming choice. We use `continuation` to ask another question using the same conversation thread
* Gemini responds with explanation. We use continuation again, using `/pal:continue (MCP)` command the second time

<div style="center">
  
[Chat With Gemini_web.webm](https://github.com/user-attachments/assets/37bd57ca-e8a6-42f7-b5fb-11de271e95db)

</div>

## Real-World Context Revival Example

Here's how this works in practice with a modern AI/ML workflow:

**Session 1 - Claude's Initial Context (before reset):**
You: "Help me design a RAG system for our customer support chatbot. I want to integrate vector embeddings with real-time retrieval. think deeply with pal using 03 after you've come up with a detailed plan."

Claude: "I'll analyze your requirements and design a comprehensive RAG architecture..."
→ Uses [`thinkdeep`](../README.md#1-chat---general-development-chat--collaborative-thinking) to brainstorm the overall approach
→ PAL creates a new thread: abc123-def456-ghi789
→ PAL responds, Claude finalizes the plan and presents it to you

*[Claude's context gets reset/compacted after extensive analysis]*

**Session 2 - After Context Reset:**
You: "Continue our RAG system discussion with O3 - I want to focus on the real-time inference optimization we talked about"

→ Claude re-uses the last continuation identifier it received, _only_ poses the new prompt (since PAL is supposed to know what was being talked about) thus saving on tokens trying to re-prompt Claude
→ PAL supplies the retained history and files that fit O3's request budget
→ O3 can see the included context: "Claude was designing a RAG system, comparing vector databases, and analyzing embedding strategies for customer support..."
→ O3 continues: "Building on our previous vector database analysis, for real-time inference optimization, I recommend implementing semantic caching with embedding similarity thresholds..."
→ O3's response helps Claude resume the discussion

Claude: "Ah yes, excellent plan! Based on O3's optimization insights and our earlier vector database comparison, let me implement the semantic caching layer..."

This example assumes the PAL process and thread survived the client's reset.
Inspect omission markers and provide missing details when context was truncated.

Use durable project documents for decisions that must survive a restart or TTL
expiry. Optional transcripts provide an audit view, but cannot be used as a
continuation recovery backend.

## Configuration

The system is highly configurable:

```env
# Maximum stored messages (default: 50; each successful exchange uses two)
MAX_CONVERSATION_TURNS=50

# Thread expiration in hours (default: 3) 
CONVERSATION_TIMEOUT_HOURS=3
```

See [architecture](architecture.md) for ownership and cleanup, and
[logging/transcripts](logging.md) for persistent audit limits.

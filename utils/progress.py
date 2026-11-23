"""Request-local MCP progress and bounded, owned operation heartbeats."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from mcp.server import ServerRequestContext
from mcp.types import TextContent

from utils.usage import known_usage

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 10.0
NOTIFICATION_TIMEOUT_SECONDS = 1.0
_current: ContextVar["RequestProgress | None"] = ContextVar("pal_progress", default=None)


@dataclass
class Operation:
    stage: str
    usage: dict[str, int] | None = None
    duration_seconds: float = 0.0
    status: str = "running"


@dataclass
class RequestProgress:
    context: ServerRequestContext | None
    started: float = field(default_factory=time.monotonic)
    operations: list[Operation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counter: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def enabled(self) -> bool:
        return (
            self.context is not None
            and self.context.meta is not None
            and self.context.meta.get("progress_token") is not None
            and not self.warnings
        )

    async def report(self, stage: str, usage: Any = None) -> None:
        if not self.enabled:
            return
        async with self._lock:
            if not self.enabled:
                return
            self.counter += 1
            message = {"stage": stage, "elapsed_seconds": round(time.monotonic() - self.started, 3)}
            counts = known_usage(usage)
            if counts:
                message["usage"] = counts
            try:
                await asyncio.wait_for(
                    self.context.session.send_progress_notification(
                        progress_token=self.context.meta["progress_token"],
                        progress=self.counter,
                        message=json.dumps(message),
                    ),
                    timeout=NOTIFICATION_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                # Notification is an optional side channel. Do not log exception
                # text: transports can include credentials or request content.
                warning = f"MCP progress notification failed ({type(exc).__name__}); further notifications disabled."
                self.warnings.append(warning)
                logger.warning(warning)

    def metadata(self) -> dict[str, Any]:
        return {
            "duration_seconds": round(time.monotonic() - self.started, 3),
            "operations": [
                {
                    "stage": op.stage,
                    "status": op.status,
                    "duration_seconds": op.duration_seconds,
                    "usage": known_usage(op.usage) or None,
                }
                for op in self.operations
            ],
            "progress_warnings": list(self.warnings),
        }

    def annotate(self, result: list[TextContent]) -> list[TextContent]:
        """Add execution metadata to PAL JSON; preserve non-JSON result content."""
        execution = self.metadata()
        for index, block in enumerate(result):
            if not isinstance(block, TextContent):
                continue
            try:
                payload = json.loads(block.text)
            except (ValueError, RecursionError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("metadata", {}), (dict, type(None))):
                payload["metadata"] = {**(payload.get("metadata") or {}), "execution": execution}
                return [*result[:index], block.model_copy(update={"text": json.dumps(payload)}), *result[index + 1 :]]
        # MCP content metadata covers plain-text tools without changing the answer.
        if result:
            block = result[0]
            return [block.model_copy(update={"meta": {**(block.meta or {}), "pal_execution": execution}}), *result[1:]]
        return result

    def annotate_error(self, payload: str) -> str:
        """Keep structured tool errors and wrap plain errors for MCP's adapter."""
        try:
            parsed = json.loads(payload)
        except (ValueError, RecursionError):
            parsed = None
        if not isinstance(parsed, dict):
            payload = json.dumps({"status": "error", "content": payload})
        return self.annotate([TextContent(type="text", text=payload)])[0].text


@asynccontextmanager
async def request_progress(context: ServerRequestContext | None) -> AsyncIterator[RequestProgress]:
    reporter = RequestProgress(context)
    token = _current.set(reporter)
    try:
        await reporter.report("started")
        try:
            yield reporter
        except Exception:
            await reporter.report("failed")
            raise
        else:
            await reporter.report("completed")
    finally:
        _current.reset(token)


async def _stop_heartbeat(task: asyncio.Task) -> None:
    async def stop() -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # This helper owns the heartbeat's cancellation.

    cleanup = asyncio.create_task(stop())
    cancelled = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancelled = exc
    cleanup.result()
    if cancelled is not None:
        raise cancelled


@asynccontextmanager
async def progress_operation(stage: str) -> AsyncIterator[Operation]:
    """Hooks use fixed stage names only, never prompts, arguments or CLI commands."""
    reporter = _current.get()
    operation = Operation(stage)
    if reporter is None:
        yield operation
        return
    reporter.operations.append(operation)
    started = time.monotonic()
    task = None

    async def heartbeat() -> None:
        while reporter.enabled:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await reporter.report(stage)

    try:
        await reporter.report(stage)
        if reporter.enabled:
            task = asyncio.create_task(heartbeat(), name="pal-progress-heartbeat")
        yield operation
        operation.status = "completed"
    except asyncio.CancelledError:
        operation.status = "cancelled"
        raise
    except BaseException:
        operation.status = "failed"
        raise
    finally:
        operation.duration_seconds = round(time.monotonic() - started, 3)
        if task is not None:
            await _stop_heartbeat(task)
    await reporter.report(f"{stage}_completed", operation.usage)

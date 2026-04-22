"""Dispatch commands from the server to handler coroutines.

Handlers register by CommandType and receive the command payload; they return
(success, result_dict, error). The dispatcher is intentionally thin — probes (TCP/DNS/
HTTP) and iperf3 register in later steps.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pulse_shared.contracts import CommandResult
from pulse_shared.enums import CommandType

log = logging.getLogger(__name__)


HandlerOutcome = tuple[bool, dict[str, Any] | None, str | None]
Handler = Callable[[dict[str, Any]], Awaitable[HandlerOutcome]]


class Dispatcher:
    def __init__(self) -> None:
        self._handlers: dict[CommandType, Handler] = {}

    def register(self, cmd_type: CommandType, handler: Handler) -> None:
        self._handlers[cmd_type] = handler

    async def handle(self, command_id: int, cmd_type: CommandType, payload: dict[str, Any]) -> CommandResult:
        handler = self._handlers.get(cmd_type)
        if handler is None:
            log.info("dispatcher.no_handler", extra={"command_id": command_id, "type": cmd_type.value})
            return CommandResult(
                command_id=command_id,
                success=False,
                result=None,
                error=f"no handler registered for {cmd_type.value}",
            )
        try:
            success, result, error = await handler(payload)
        except Exception as e:  # noqa: BLE001
            log.exception("dispatcher.handler_crashed", extra={"command_id": command_id})
            return CommandResult(command_id=command_id, success=False, result=None, error=repr(e))
        return CommandResult(
            command_id=command_id, success=success, result=result, error=error
        )

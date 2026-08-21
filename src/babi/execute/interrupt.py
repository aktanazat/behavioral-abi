"""Generic transport-level interruption, using nothing but the MCP SDK's own
courtesy-cancel mechanism.

`Client.call_tool(..., read_timeout_seconds=budget)` bounds how long the
client waits for a response; when the budget elapses the SDK's dispatcher
sends the peer `notifications/cancelled` -- but *only* if the request had
already been written (see `mcp.shared.jsonrpc_dispatcher`: the write happens
unconditionally before the timeout is armed, and a write interrupted by
cancellation still counts as issued). That is genuinely "interrupted after
send, before response": never synthesized, never simulated -- either the
SDK's own abandon-tracking fires, confirming a real courtesy cancel went
out, or it does not, and this module says so honestly rather than assuming
the interrupt "must have" taken hold.

Because the outcome depends on whether the server answers within `budget`,
whether the mechanism actually fires is itself something to observe, not
assume -- `attempt_interruptible_call`'s `interrupted` flag is exactly that
observation, consumed by `babi.classify`'s "interrupted unsupported ->
inconclusive" gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from babi.canon import Json

from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, CallToolResult

DEFAULT_INTERRUPT_BUDGET_SECONDS = 0.001


@dataclass(frozen=True, slots=True)
class InterruptAttempt:
    """`interrupted=True`: the SDK's own timeout/courtesy-cancel fired --
    the request was sent and the wait for its response was abandoned
    before one arrived; `result`/`error` are both `None`.
    `interrupted=False, error=None`: the call completed normally within
    `budget_seconds` (the interruption attempt did not win the race);
    `result` holds the real response.
    `interrupted=False, error=<exc>`: some other transport/protocol failure,
    unrelated to the interrupt budget.
    """

    interrupted: bool
    result: CallToolResult | None
    error: Exception | None


async def attempt_interruptible_call(
    client: Client,
    name: str,
    arguments: dict[str, Json],
    *,
    budget_seconds: float = DEFAULT_INTERRUPT_BUDGET_SECONDS,
) -> InterruptAttempt:
    try:
        result = await client.call_tool(name, arguments, read_timeout_seconds=budget_seconds)
    except MCPError as exc:
        if exc.code == REQUEST_TIMEOUT:
            return InterruptAttempt(interrupted=True, result=None, error=None)
        return InterruptAttempt(interrupted=False, result=None, error=exc)
    except Exception as exc:  # noqa: BLE001 -- any other transport failure is still a valid outcome to record
        return InterruptAttempt(interrupted=False, result=None, error=exc)
    return InterruptAttempt(interrupted=False, result=result, error=None)


__all__ = ["DEFAULT_INTERRUPT_BUDGET_SECONDS", "InterruptAttempt", "attempt_interruptible_call"]

"""irl-gateway over MCP stdio: the agent's only path to the market.

The gateway runs as a child process configured by the IRL_* / GATEWAY_*
environment variables (see the irl-gateway README); this module only speaks
MCP to it.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

DEFAULT_COMMAND = "irl-gateway"


class GatewayError(RuntimeError):
    pass


class McpTradingTools:
    def __init__(self, session: Any):
        self._session = session

    async def _call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self._session.call_tool(name, arguments or {})
        if getattr(result, "is_error", False):
            text = " ".join(getattr(c, "text", "") for c in getattr(result, "content", []))
            raise GatewayError(f"{name} failed: {text or 'no detail'}")
        data = getattr(result, "structured_content", None)
        if not isinstance(data, dict):
            raise GatewayError(f"{name} returned no structured result")
        if "error" in data and len(data) == 1:
            raise GatewayError(f"{name}: {data['error']}")
        return data

    async def get_balances(self) -> dict[str, float]:
        data = await self._call("get_balances")
        return {str(k): float(v) for k, v in data.get("balances", {}).items()}

    async def get_quote(self, symbol: str) -> float:
        return float((await self._call("get_quote", {"symbol": symbol}))["price"])

    async def get_policy(self) -> dict[str, Any]:
        return await self._call("get_policy")

    async def execute_trade(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._call("execute_trade", arguments)


@asynccontextmanager
async def gateway_tools(command: str | None = None) -> AsyncIterator[McpTradingTools]:
    """Start irl-gateway as an MCP stdio server and yield its tools."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    raw = command or os.environ.get("IRL_GATEWAY_COMMAND", DEFAULT_COMMAND)
    argv = shlex.split(raw, posix=os.name != "nt")  # keep Windows backslash paths intact
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=dict(os.environ))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield McpTradingTools(session)

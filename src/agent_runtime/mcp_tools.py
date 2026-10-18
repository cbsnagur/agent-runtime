"""Connect to an MCP server and expose its tools in the loop's terms: ToolSpecs plus a call_tool coroutine."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import Client

from agent_runtime.loop import CallTool
from agent_runtime.providers.base import ToolSpec


class ToolError(Exception):
    """The server reported a failed tool call (unknown tool, invalid arguments, or an exception)."""


@asynccontextmanager
async def connect(server: Any) -> AsyncIterator[tuple[list[ToolSpec], CallTool]]:
    """`server` is anything mcp.Client accepts: StdioServerParameters, a URL, or an in-process server."""
    async with Client(server) as client:
        listing = await client.list_tools()
        specs = [ToolSpec(t.name, t.description or "", t.input_schema) for t in listing.tools]

        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            res = await client.call_tool(name, arguments)
            text = "".join(c.text for c in res.content if c.type == "text")
            if res.is_error:
                raise ToolError(text)
            if res.structured_content is not None:
                return res.structured_content
            try:
                return json.loads(text)
            except ValueError:
                return text

        yield specs, call_tool

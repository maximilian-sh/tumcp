"""The MCP layer: annotations, input schemas and error reporting (no network)."""
import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tumcp import server
from tumcp.client.auth import AuthError
from tumcp.client.session import TuError

WRITES_LOCAL_FILES = {"tuwel_download", "tuwel_sync_course"}


def _tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_every_tool_is_annotated():
    for name, tool in _tools().items():
        a = tool.annotations
        assert a is not None and a.title, name
        assert a.destructive_hint is False, name
        assert a.read_only_hint is (name not in WRITES_LOCAL_FILES), name


def test_schemas_carry_limits_and_hide_the_context():
    tools = _tools()
    days = tools["tuwel_upcoming"].input_schema["properties"]["days"]
    assert days["minimum"] == 1 and days["maximum"] == 365 and days["description"]
    assert "ctx" not in tools["tuwel_sync_course"].input_schema["properties"]
    assert "pattern" in tools["tiss_course"].input_schema["properties"]["semester"]


@pytest.mark.parametrize("exc, hint", [
    (TuError("no TU Wien credentials configured — run `tumcp login`"), "in a terminal"),
    (AuthError("the IdP rejected the login"), "tumcp login"),
    (ValueError("semester must look like 2026W"), "2026W"),
])
def test_errors_become_tool_errors_with_a_next_step(exc, hint):
    async def boom():
        raise exc
    with pytest.raises(ToolError, match=hint):
        asyncio.run(server._safe(boom()))

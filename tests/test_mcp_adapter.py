import pytest

from support_agent_lab.bootstrap import create_container
from support_agent_lab.config import get_settings
from support_agent_lab.mcp.adapter import MCPToolAdapter
from support_agent_lab.mcp.server import build_adapter


def test_mcp_adapter_lists_governed_tool_metadata():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=True)

    tools = {tool["name"]: tool for tool in adapter.list_tools()}

    assert "ticket.create" in tools
    assert tools["ticket.create"]["requiredScopes"] == ["ticket:write"]
    assert tools["ticket.create"]["idempotent"] is False
    assert tools["ticket.create"]["timeoutMs"] > 0
    assert "inputSchema" in tools["ticket.create"]
    assert "outputSchema" in tools["ticket.create"]


@pytest.mark.asyncio
async def test_mcp_adapter_applies_stable_idempotency_for_write_tools():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=True)
    payload = {
        "customer_id": "SELF",
        "title": "MCP ticket",
        "description": "Created through MCP adapter",
    }

    first = await adapter.call_tool("ticket.create", payload, user_id="user_demo", scopes=["ticket:write"])
    second = await adapter.call_tool("ticket.create", payload, user_id="user_demo", scopes=["ticket:write"])

    first_result = first["content"][0]["json"]
    second_result = second["content"][0]["json"]
    assert first["isError"] is False
    assert first_result["data"]["ticket_id"] == second_result["data"]["ticket_id"]


@pytest.mark.asyncio
async def test_mcp_adapter_respects_resource_ownership():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant")

    result = await adapter.call_tool(
        "order.get",
        {"order_id": "A1001"},
        tenant_id="demo_tenant",
        user_id="user_guest",
        scopes=["order:read"],
    )

    payload = result["content"][0]["json"]
    assert result["isError"] is True
    assert payload["error_code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_mcp_adapter_requires_explicit_actor_when_default_actor_disabled():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)

    with pytest.raises(RuntimeError, match="authenticated user_id"):
        await adapter.call_tool("crm.get_customer", {"user_id": "user_demo"})


@pytest.mark.asyncio
async def test_mcp_adapter_requires_explicit_scopes_in_gateway_mode():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)

    with pytest.raises(RuntimeError, match="authenticated scopes"):
        await adapter.call_tool(
            "crm.get_customer",
            {"user_id": "user_demo"},
            tenant_id="demo_tenant",
            user_id="user_demo",
        )


@pytest.mark.asyncio
async def test_mcp_adapter_requires_explicit_tenant_in_gateway_mode():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)

    with pytest.raises(RuntimeError, match="authenticated tenant_id"):
        await adapter.call_tool(
            "crm.get_customer",
            {"user_id": "user_demo"},
            user_id="user_demo",
            scopes=["crm:read"],
        )


@pytest.mark.asyncio
async def test_mcp_adapter_rejects_tenant_mismatch():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)

    with pytest.raises(RuntimeError, match="tenant_id does not match"):
        await adapter.call_tool(
            "crm.get_customer",
            {"user_id": "user_demo"},
            tenant_id="other_tenant",
            user_id="user_demo",
            scopes=["crm:read"],
        )


@pytest.mark.asyncio
async def test_mcp_gateway_mode_does_not_auto_create_idempotency_key():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)

    result = await adapter.call_tool(
        "ticket.create",
        {
            "customer_id": "SELF",
            "title": "Missing explicit idempotency",
            "description": "Gateway mode must pass its own idempotency key.",
        },
        tenant_id="demo_tenant",
        user_id="user_demo",
        scopes=["ticket:write"],
    )

    payload = result["content"][0]["json"]
    assert result["isError"] is True
    assert payload["error_code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_mcp_gateway_mode_replays_explicit_idempotency_key():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)
    payload = {
        "customer_id": "SELF",
        "title": "Explicit idempotency",
        "description": "Gateway supplies the replay key.",
    }

    first = await adapter.call_tool(
        "ticket.create",
        payload,
        tenant_id="demo_tenant",
        user_id="user_demo",
        scopes=["ticket:write"],
        request_id="req_mcp_1",
        trace_id="trace_mcp_1",
        idempotency_key="mcp-ticket-explicit-1",
    )
    second = await adapter.call_tool(
        "ticket.create",
        payload,
        tenant_id="demo_tenant",
        user_id="user_demo",
        scopes=["ticket:write"],
        request_id="req_mcp_2",
        trace_id="trace_mcp_1",
        idempotency_key="mcp-ticket-explicit-1",
    )

    assert first["isError"] is False
    assert second["isError"] is False
    first_result = first["content"][0]["json"]
    second_result = second["content"][0]["json"]
    assert first_result["data"]["ticket_id"] == second_result["data"]["ticket_id"]


@pytest.mark.asyncio
async def test_mcp_gateway_mode_reports_idempotency_conflict():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=False)
    first_payload = {
        "customer_id": "SELF",
        "title": "First payload",
        "description": "Original operation payload.",
    }
    changed_payload = {
        "customer_id": "SELF",
        "title": "Changed payload",
        "description": "Same operation key with changed payload.",
    }

    first = await adapter.call_tool(
        "ticket.create",
        first_payload,
        tenant_id="demo_tenant",
        user_id="user_demo",
        scopes=["ticket:write"],
        idempotency_key="mcp-ticket-conflict-1",
    )
    conflict = await adapter.call_tool(
        "ticket.create",
        changed_payload,
        tenant_id="demo_tenant",
        user_id="user_demo",
        scopes=["ticket:write"],
        idempotency_key="mcp-ticket-conflict-1",
    )

    assert first["isError"] is False
    payload = conflict["content"][0]["json"]
    assert conflict["isError"] is True
    assert payload["error_code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_empty_mcp_scopes_do_not_fall_back_to_local_defaults():
    container = create_container()
    adapter = MCPToolAdapter(container.tools, tenant_id="demo_tenant", allow_default_actor=True)

    result = await adapter.call_tool(
        "crm.get_customer",
        {"user_id": "user_demo"},
        user_id="user_demo",
        scopes=[],
    )

    payload = result["content"][0]["json"]
    assert result["isError"] is True
    assert payload["error_code"] == "FORBIDDEN"


def test_bundled_mcp_server_is_local_only_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="local-only"):
            build_adapter()
    finally:
        get_settings.cache_clear()

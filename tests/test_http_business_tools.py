import httpx
import pytest

from support_agent_lab.tools.errors import RATE_LIMITED, UPSTREAM_ERROR, ToolError
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient
from support_agent_lab.tools.registry import Actor, ToolContext


def _ctx() -> ToolContext:
    return ToolContext(
        actor=Actor(
            user_id="user_123",
            tenant_id="tenant_live",
            scopes=["crm:read"],
        ),
        request_id="req_123",
        trace_id="run_123",
        tenant_id="tenant_live",
        idempotency_key="idem_123",
    )


@pytest.mark.asyncio
async def test_http_business_client_sends_gateway_context_headers():
    seen_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"customer_id": "C123"})

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        api_key="business-token",
        transport=httpx.MockTransport(handler),
    )

    payload = await client.get("/customers/user_123", ctx=_ctx())

    assert payload == {"customer_id": "C123"}
    assert seen_headers["authorization"] == "Bearer business-token"
    assert seen_headers["x-tenant-id"] == "tenant_live"
    assert seen_headers["x-actor-user-id"] == "user_123"
    assert seen_headers["x-actor-roles"] == "agent"
    assert seen_headers["x-actor-scopes"] == "crm:read"
    assert seen_headers["x-request-id"] == "req_123"
    assert seen_headers["x-trace-id"] == "run_123"
    assert seen_headers["idempotency-key"] == "idem_123"


@pytest.mark.asyncio
async def test_http_business_client_maps_rate_limit_to_tool_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ToolError) as exc_info:
        await client.get("/customers/user_123", ctx=_ctx())

    assert exc_info.value.code == RATE_LIMITED
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_http_business_client_maps_invalid_json_to_upstream_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ToolError) as exc_info:
        await client.get("/customers/user_123", ctx=_ctx())

    assert exc_info.value.code == UPSTREAM_ERROR
    assert exc_info.value.retryable is True

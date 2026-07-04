import json

import httpx
import pytest

from support_agent_lab.models import ToolStatus
from support_agent_lab.tools.errors import RATE_LIMITED, UPSTREAM_ERROR, UPSTREAM_UNAVAILABLE, ToolError
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient, create_http_registry
from support_agent_lab.tools.registry import Actor, ToolBroker, ToolContext


def _ctx(scopes: list[str] | None = None) -> ToolContext:
    return ToolContext(
        actor=Actor(
            user_id="user_123",
            tenant_id="tenant_live",
            scopes=scopes or ["crm:read"],
        ),
        request_id="req_123",
        trace_id="run_123",
        tenant_id="tenant_live",
        idempotency_key="idem_123",
    )


def test_http_registry_tool_timeouts_cover_retry_budget():
    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        timeout_ms=1000,
        retry_attempts=3,
        retry_backoff_ms=10,
    )

    timeouts = {tool["timeout_ms"] for tool in create_http_registry(client).list_tools()}

    assert timeouts == {3030}


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


@pytest.mark.asyncio
async def test_http_business_client_retries_safe_get_before_returning_success():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"customer_id": "C123"})

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        retry_attempts=2,
        retry_backoff_ms=0,
        transport=httpx.MockTransport(handler),
    )

    payload = await client.get("/customers/user_123", ctx=_ctx())

    assert payload == {"customer_id": "C123"}
    assert calls == ["/customers/user_123", "/customers/user_123"]
    assert client.circuit_status()["state"] == "closed"


@pytest.mark.asyncio
async def test_http_business_client_opens_circuit_after_retryable_failures():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": "temporary"})

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        retry_attempts=1,
        circuit_failure_threshold=1,
        circuit_reset_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ToolError) as first_error:
        await client.get("/customers/user_123", ctx=_ctx())
    with pytest.raises(ToolError) as second_error:
        await client.get("/customers/user_123", ctx=_ctx())

    assert first_error.value.code == UPSTREAM_ERROR
    assert second_error.value.code == UPSTREAM_UNAVAILABLE
    assert second_error.value.retryable is True
    assert "circuit is open" in second_error.value.message
    assert calls == ["/customers/user_123"]
    assert client.circuit_status()["state"] == "open"


@pytest.mark.asyncio
async def test_http_business_client_half_open_success_closes_circuit():
    now = 0.0
    calls = []

    def clock() -> float:
        return now

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"customer_id": "C123"})

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        retry_attempts=1,
        circuit_failure_threshold=1,
        circuit_reset_seconds=10,
        transport=httpx.MockTransport(handler),
        clock=clock,
    )

    with pytest.raises(ToolError):
        await client.get("/customers/user_123", ctx=_ctx())
    assert client.circuit_status()["state"] == "open"

    now = 11.0
    payload = await client.get("/customers/user_123", ctx=_ctx())

    assert payload == {"customer_id": "C123"}
    assert calls == ["/customers/user_123", "/customers/user_123"]
    assert client.circuit_status()["state"] == "closed"
    assert client.circuit_status()["failure_count"] == 0


@pytest.mark.asyncio
async def test_http_business_client_retries_post_only_when_idempotency_key_is_present():
    seen_idempotency_headers = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_idempotency_headers.append(request.headers.get("idempotency-key"))
        if len(seen_idempotency_headers) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={
                "ticket_id": "T9001",
                "status": "open",
                "created_at": "2026-07-02T00:00:00+00:00",
            },
        )

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        retry_attempts=2,
        retry_backoff_ms=0,
        transport=httpx.MockTransport(handler),
    )

    payload = await client.post("/tickets", json={"title": "Need help"}, ctx=_ctx())

    assert payload["ticket_id"] == "T9001"
    assert seen_idempotency_headers == ["idem_123", "idem_123"]


@pytest.mark.asyncio
async def test_http_business_client_does_not_retry_post_without_idempotency_key():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": "temporary"})

    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        retry_attempts=3,
        retry_backoff_ms=0,
        transport=httpx.MockTransport(handler),
    )
    ctx = _ctx().model_copy(update={"idempotency_key": None})

    with pytest.raises(ToolError) as exc_info:
        await client.post("/tickets", json={"title": "Need help"}, ctx=ctx)

    assert exc_info.value.code == UPSTREAM_ERROR
    assert calls == ["/tickets"]


@pytest.mark.asyncio
async def test_http_registry_resolves_self_for_order_search_before_upstream_call():
    seen_customer_ids = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/customers/user_123":
            return httpx.Response(
                200,
                json={
                    "customer_id": "C123",
                    "name": "Lin",
                    "tier": "gold",
                    "verified": True,
                },
            )
        if request.url.path == "/orders":
            seen_customer_ids.append(request.url.params["customer_id"])
            return httpx.Response(
                200,
                json={
                    "orders": [
                        {
                            "order_id": "A1001",
                            "customer_id": "C123",
                            "status": "paid",
                            "product": "Headphones",
                            "amount": 19900,
                            "currency": "CNY",
                            "returnable": True,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    broker = _http_broker(handler)

    result = await broker.call(
        "order.search",
        {"customer_id": "SELF"},
        _ctx(scopes=["crm:read", "order:read"]),
    )

    assert result.status == ToolStatus.success
    assert seen_customer_ids == ["C123"]


@pytest.mark.asyncio
async def test_http_registry_resolves_self_for_ticket_create_before_upstream_call():
    seen_ticket_bodies = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/customers/user_123":
            return httpx.Response(
                200,
                json={
                    "customer_id": "C123",
                    "name": "Lin",
                    "tier": "gold",
                    "verified": True,
                },
            )
        if request.url.path == "/tickets":
            seen_ticket_bodies.append(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "ticket_id": "T9001",
                    "status": "open",
                    "created_at": "2026-07-02T00:00:00+00:00",
                },
            )
        return httpx.Response(404)

    broker = _http_broker(handler)

    result = await broker.call(
        "ticket.create",
        {
            "customer_id": "SELF",
            "title": "Need help",
            "description": "Create this for my own customer record.",
        },
        _ctx(scopes=["crm:read", "ticket:write"]),
    )

    assert result.status == ToolStatus.success
    assert seen_ticket_bodies[0]["customer_id"] == "C123"


@pytest.mark.asyncio
async def test_http_registry_rejects_cross_user_customer_lookup_before_upstream_call():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    broker = _http_broker(handler)

    result = await broker.call(
        "crm.get_customer",
        {"user_id": "other_user"},
        _ctx(scopes=["crm:read"]),
    )

    assert result.status == ToolStatus.failed
    assert result.error_code == "FORBIDDEN"
    assert calls == []


@pytest.mark.asyncio
async def test_http_registry_rejects_unsafe_path_parameters_before_upstream_call():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    broker = _http_broker(handler)

    result = await broker.call(
        "order.get",
        {"order_id": "../admin"},
        _ctx(scopes=["order:read"]),
    )

    assert result.status == ToolStatus.failed
    assert result.error_code == "VALIDATION_ERROR"
    assert calls == []


@pytest.mark.asyncio
async def test_http_registry_encodes_allowed_path_segments():
    seen_paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.raw_path.decode())
        return httpx.Response(
            200,
            json={
                "customer_id": "C123",
                "name": "Lin",
                "tier": "gold",
                "verified": True,
            },
        )

    broker = _http_broker(handler)

    result = await broker.call(
        "crm.get_customer",
        {"user_id": "lin@example.com"},
        _ctx(scopes=["crm:read", "crm:admin"]),
    )

    assert result.status == ToolStatus.success
    assert seen_paths == ["/customers/lin%40example.com"]


@pytest.mark.asyncio
async def test_http_registry_rejects_order_payload_for_other_customer():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orders/A1001":
            return httpx.Response(
                200,
                json={
                    "order_id": "A1001",
                    "customer_id": "C999",
                    "status": "paid",
                    "product": "Headphones",
                    "amount": 19900,
                    "currency": "CNY",
                    "returnable": True,
                },
            )
        if request.url.path == "/customers/user_123":
            return httpx.Response(
                200,
                json={
                    "customer_id": "C123",
                    "name": "Lin",
                    "tier": "gold",
                    "verified": True,
                },
            )
        return httpx.Response(404)

    broker = _http_broker(handler)

    result = await broker.call(
        "order.get",
        {"order_id": "A1001"},
        _ctx(scopes=["crm:read", "order:read"]),
    )

    assert result.status == ToolStatus.failed
    assert result.error_code == "FORBIDDEN"


def _http_broker(handler) -> ToolBroker:
    client = HTTPBusinessClient(
        base_url="https://business.internal.test",
        transport=httpx.MockTransport(handler),
    )
    return ToolBroker(registry=create_http_registry(client), idempotency_store={})

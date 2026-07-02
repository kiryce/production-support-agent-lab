from __future__ import annotations

import httpx

from support_agent_lab.models import new_id
from support_agent_lab.tools.business_tools import (
    CreateTicketInput,
    CreateTicketOutput,
    CustomerOutput,
    GetCustomerInput,
    GetOrderInput,
    KBSearchInput,
    KBSearchOutput,
    OrderOutput,
    SearchOrdersInput,
    SearchOrdersOutput,
    TrackShipmentInput,
    TrackShipmentOutput,
)
from support_agent_lab.tools.errors import (
    CONFLICT,
    FORBIDDEN,
    NOT_FOUND,
    RATE_LIMITED,
    TIMEOUT,
    UNAUTHORIZED,
    UPSTREAM_ERROR,
    UPSTREAM_UNAVAILABLE,
    ToolError,
)
from support_agent_lab.tools.registry import ToolContext, ToolDefinition, ToolRegistry


class HTTPBusinessClient:
    """HTTP adapter for production CRM/OMS/shipping/ticketing services.

    Expected endpoints:
      GET  /customers/{user_id}
      GET  /orders?customer_id=<id>&status=<optional>
      GET  /orders/{order_id}
      GET  /shipments/{logistics_id}
      POST /tickets
      GET  /knowledge/search?query=<text>&limit=<n>
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_ms: int = 5000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout_ms / 1000
        self.transport = transport

    async def get(self, path: str, *, params: dict | None = None, ctx: ToolContext) -> dict | list:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._headers(ctx),
                transport=self.transport,
            ) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                return _json_payload(response, path)
        except httpx.TimeoutException as exc:
            raise ToolError(TIMEOUT, f"HTTP service timed out for {path}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise _tool_error_from_status(exc.response.status_code, path) from exc
        except httpx.HTTPError as exc:
            raise ToolError(UPSTREAM_UNAVAILABLE, f"HTTP service unavailable for {path}", retryable=True) from exc

    async def post(self, path: str, *, json: dict, ctx: ToolContext) -> dict | list:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._headers(ctx),
                transport=self.transport,
            ) as client:
                response = await client.post(path, json=json)
                response.raise_for_status()
                return _json_payload(response, path)
        except httpx.TimeoutException as exc:
            raise ToolError(TIMEOUT, f"HTTP service timed out for {path}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise _tool_error_from_status(exc.response.status_code, path) from exc
        except httpx.HTTPError as exc:
            raise ToolError(UPSTREAM_UNAVAILABLE, f"HTTP service unavailable for {path}", retryable=True) from exc

    def _headers(self, ctx: ToolContext) -> dict[str, str]:
        headers = {
            "X-Tenant-Id": ctx.tenant_id,
            "X-Actor-User-Id": ctx.actor.user_id,
            "X-Actor-Roles": ",".join(ctx.actor.roles),
            "X-Actor-Scopes": ",".join(ctx.actor.scopes),
            "X-Request-Id": ctx.request_id,
            "X-Trace-Id": ctx.trace_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if ctx.idempotency_key:
            headers["Idempotency-Key"] = ctx.idempotency_key
        return headers

    async def health_check(self, tenant_id: str) -> None:
        request_id = new_id("ready_req")
        headers = {
            "X-Tenant-Id": tenant_id,
            "X-Actor-User-Id": "readiness_probe",
            "X-Request-Id": request_id,
            "X-Trace-Id": request_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=headers,
                transport=self.transport,
            ) as client:
                response = await client.get("/health")
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ToolError(TIMEOUT, "Business API readiness check timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise _tool_error_from_status(exc.response.status_code, "/health") from exc
        except httpx.HTTPError as exc:
            raise ToolError(UPSTREAM_UNAVAILABLE, "Business API readiness check failed", retryable=True) from exc


def _tool_error_from_status(status_code: int, path: str) -> ToolError:
    mapping = {
        401: UNAUTHORIZED,
        403: FORBIDDEN,
        404: NOT_FOUND,
        409: CONFLICT,
        429: RATE_LIMITED,
    }
    code = mapping.get(status_code)
    if code:
        return ToolError(code, f"HTTP service returned {status_code} for {path}", retryable=status_code == 429)
    if 500 <= status_code <= 599:
        return ToolError(UPSTREAM_ERROR, f"HTTP service returned {status_code} for {path}", retryable=True)
    return ToolError(UPSTREAM_ERROR, f"HTTP service returned {status_code} for {path}", retryable=False)


def _json_payload(response: httpx.Response, path: str) -> dict | list:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ToolError(UPSTREAM_ERROR, f"HTTP service returned invalid JSON for {path}", retryable=True) from exc
    if not isinstance(payload, (dict, list)):
        raise ToolError(UPSTREAM_ERROR, f"HTTP service returned unsupported JSON for {path}", retryable=False)
    return payload


def create_http_registry(client: HTTPBusinessClient) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="crm.get_customer",
            description="Look up a masked customer profile by user id.",
            input_model=GetCustomerInput,
            output_model=CustomerOutput,
            required_scopes=["crm:read"],
            timeout_ms=int(client.timeout * 1000),
            idempotent=True,
            handler=lambda input_, ctx: _get_customer(input_, ctx, client),
        )
    )
    registry.register(
        ToolDefinition(
            name="order.search",
            description="Search orders for a customer. Use before acting on ambiguous order requests.",
            input_model=SearchOrdersInput,
            output_model=SearchOrdersOutput,
            required_scopes=["order:read"],
            timeout_ms=int(client.timeout * 1000),
            idempotent=True,
            handler=lambda input_, ctx: _search_orders(input_, ctx, client),
        )
    )
    registry.register(
        ToolDefinition(
            name="order.get",
            description="Fetch one order by id.",
            input_model=GetOrderInput,
            output_model=OrderOutput,
            required_scopes=["order:read"],
            timeout_ms=int(client.timeout * 1000),
            idempotent=True,
            handler=lambda input_, ctx: _get_order(input_, ctx, client),
        )
    )
    registry.register(
        ToolDefinition(
            name="shipping.track",
            description="Track a shipment by logistics id.",
            input_model=TrackShipmentInput,
            output_model=TrackShipmentOutput,
            required_scopes=["shipping:read"],
            timeout_ms=int(client.timeout * 1000),
            idempotent=True,
            handler=lambda input_, ctx: _track_shipment(input_, ctx, client),
        )
    )
    registry.register(
        ToolDefinition(
            name="ticket.create",
            description="Create a support ticket for follow-up or human handoff.",
            input_model=CreateTicketInput,
            output_model=CreateTicketOutput,
            required_scopes=["ticket:write"],
            timeout_ms=int(client.timeout * 1000),
            idempotent=False,
            handler=lambda input_, ctx: _create_ticket(input_, ctx, client),
        )
    )
    registry.register(
        ToolDefinition(
            name="kb.search",
            description="Search the support knowledge base and return source-backed snippets.",
            input_model=KBSearchInput,
            output_model=KBSearchOutput,
            required_scopes=["kb:read"],
            timeout_ms=int(client.timeout * 1000),
            idempotent=True,
            handler=lambda input_, ctx: _kb_search(input_, ctx, client),
        )
    )
    return registry


async def _get_customer(input_: GetCustomerInput, ctx: ToolContext, client: HTTPBusinessClient) -> CustomerOutput:
    payload = await client.get(f"/customers/{input_.user_id}", ctx=ctx)
    return CustomerOutput.model_validate(payload)


async def _search_orders(input_: SearchOrdersInput, ctx: ToolContext, client: HTTPBusinessClient) -> SearchOrdersOutput:
    params = {"customer_id": input_.customer_id}
    if input_.status:
        params["status"] = input_.status
    payload = await client.get("/orders", params=params, ctx=ctx)
    orders = payload.get("orders", payload) if isinstance(payload, dict) else payload
    return SearchOrdersOutput(orders=[OrderOutput.model_validate(item) for item in orders])


async def _get_order(input_: GetOrderInput, ctx: ToolContext, client: HTTPBusinessClient) -> OrderOutput:
    payload = await client.get(f"/orders/{input_.order_id}", ctx=ctx)
    return OrderOutput.model_validate(payload)


async def _track_shipment(
    input_: TrackShipmentInput,
    ctx: ToolContext,
    client: HTTPBusinessClient,
) -> TrackShipmentOutput:
    payload = await client.get(f"/shipments/{input_.logistics_id}", ctx=ctx)
    return TrackShipmentOutput.model_validate(payload)


async def _create_ticket(
    input_: CreateTicketInput,
    ctx: ToolContext,
    client: HTTPBusinessClient,
) -> CreateTicketOutput:
    payload = await client.post("/tickets", json=input_.model_dump(), ctx=ctx)
    return CreateTicketOutput.model_validate(payload)


async def _kb_search(input_: KBSearchInput, ctx: ToolContext, client: HTTPBusinessClient) -> KBSearchOutput:
    payload = await client.get(
        "/knowledge/search",
        params={"query": input_.query, "limit": input_.limit},
        ctx=ctx,
    )
    hits = payload.get("hits", payload) if isinstance(payload, dict) else payload
    return KBSearchOutput(hits=hits)

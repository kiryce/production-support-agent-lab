from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

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


_RETRYABLE_ERROR_CODES = {RATE_LIMITED, TIMEOUT, UPSTREAM_ERROR, UPSTREAM_UNAVAILABLE}


@dataclass
class _CircuitState:
    failure_count: int = 0
    opened_at: float | None = None


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
        retry_attempts: int = 2,
        retry_backoff_ms: int = 100,
        circuit_failure_threshold: int = 5,
        circuit_reset_seconds: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout_ms / 1000
        self.retry_attempts = max(1, retry_attempts)
        self.retry_backoff_ms = max(0, retry_backoff_ms)
        self.circuit_failure_threshold = max(1, circuit_failure_threshold)
        self.circuit_reset_seconds = max(0, circuit_reset_seconds)
        self.transport = transport
        self._clock = clock or time.monotonic
        self._circuit = _CircuitState()

    @property
    def timeout_ms(self) -> int:
        return int(self.timeout * 1000)

    @property
    def operation_timeout_ms(self) -> int:
        backoff_ms = sum(self.retry_backoff_ms * (2 ** (attempt - 1)) for attempt in range(1, self.retry_attempts))
        return (self.timeout_ms * self.retry_attempts) + backoff_ms

    async def get(self, path: str, *, params: dict | None = None, ctx: ToolContext) -> dict | list:
        payload = await self._request(
            "GET",
            path,
            headers=self._headers(ctx),
            params=params,
            parse_json=True,
            safe_to_retry=True,
        )
        return _require_payload(payload, path)

    async def post(self, path: str, *, json: dict, ctx: ToolContext) -> dict | list:
        payload = await self._request(
            "POST",
            path,
            headers=self._headers(ctx),
            json_body=json,
            parse_json=True,
            safe_to_retry=bool(ctx.idempotency_key),
        )
        return _require_payload(payload, path)

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
        await self._request(
            "GET",
            "/health",
            headers=headers,
            parse_json=False,
            safe_to_retry=True,
        )

    def circuit_status(self) -> dict[str, object]:
        opened_seconds_ago: float | None = None
        if self._circuit.opened_at is not None:
            opened_seconds_ago = round(max(0.0, self._clock() - self._circuit.opened_at), 3)
        return {
            "state": self._circuit_state(),
            "failure_count": self._circuit.failure_count,
            "failure_threshold": self.circuit_failure_threshold,
            "reset_seconds": self.circuit_reset_seconds,
            "opened_seconds_ago": opened_seconds_ago,
            "retry_attempts": self.retry_attempts,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: dict | None = None,
        json_body: dict | None = None,
        parse_json: bool,
        safe_to_retry: bool,
    ) -> dict | list | None:
        attempts = self.retry_attempts if safe_to_retry else 1
        last_error: ToolError | None = None
        for attempt in range(1, attempts + 1):
            self._raise_if_circuit_open(path)
            try:
                payload = await self._send_once(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    json_body=json_body,
                    parse_json=parse_json,
                )
            except ToolError as exc:
                last_error = exc
                self._record_failure(exc)
                if not self._should_retry(exc, attempt=attempt, attempts=attempts, safe_to_retry=safe_to_retry):
                    raise
                await self._sleep_before_retry(attempt)
                continue
            self._record_success()
            return payload
        if last_error:
            raise last_error
        raise ToolError(UPSTREAM_ERROR, f"HTTP service failed without response for {path}", retryable=False)

    async def _send_once(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: dict | None,
        json_body: dict | None,
        parse_json: bool,
    ) -> dict | list | None:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=headers,
                transport=self.transport,
            ) as client:
                if method == "GET":
                    response = await client.get(path, params=params)
                elif method == "POST":
                    response = await client.post(path, json=json_body)
                else:
                    raise ToolError(UPSTREAM_ERROR, f"Unsupported HTTP method {method}", retryable=False)
                response.raise_for_status()
                if not parse_json:
                    return None
                return _json_payload(response, path)
        except httpx.TimeoutException as exc:
            raise ToolError(TIMEOUT, f"HTTP service timed out for {path}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise _tool_error_from_status(exc.response.status_code, path) from exc
        except httpx.HTTPError as exc:
            raise ToolError(UPSTREAM_UNAVAILABLE, f"HTTP service unavailable for {path}", retryable=True) from exc

    def _should_retry(
        self,
        exc: ToolError,
        *,
        attempt: int,
        attempts: int,
        safe_to_retry: bool,
    ) -> bool:
        if not safe_to_retry or attempt >= attempts:
            return False
        if self._circuit_state() == "open":
            return False
        return exc.retryable and exc.code in _RETRYABLE_ERROR_CODES

    async def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_ms <= 0:
            return
        backoff_seconds = (self.retry_backoff_ms / 1000) * (2 ** (attempt - 1))
        await asyncio.sleep(backoff_seconds)

    def _raise_if_circuit_open(self, path: str) -> None:
        if self._circuit_state() == "open":
            raise ToolError(
                UPSTREAM_UNAVAILABLE,
                f"Business API circuit is open for {path}; retry after reset window",
                retryable=True,
            )

    def _record_success(self) -> None:
        self._circuit.failure_count = 0
        self._circuit.opened_at = None

    def _record_failure(self, exc: ToolError) -> None:
        if not exc.retryable or exc.code not in _RETRYABLE_ERROR_CODES:
            return
        self._circuit.failure_count += 1
        if self._circuit.failure_count >= self.circuit_failure_threshold:
            self._circuit.opened_at = self._clock()

    def _circuit_state(self) -> str:
        opened_at = self._circuit.opened_at
        if opened_at is None:
            return "closed"
        if self._clock() - opened_at >= self.circuit_reset_seconds:
            return "half_open"
        return "open"


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


def _require_payload(payload: dict | list | None, path: str) -> dict | list:
    if payload is None:
        raise ToolError(UPSTREAM_ERROR, f"HTTP service returned empty payload for {path}", retryable=False)
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
            timeout_ms=client.operation_timeout_ms,
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
            timeout_ms=client.operation_timeout_ms,
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
            timeout_ms=client.operation_timeout_ms,
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
            timeout_ms=client.operation_timeout_ms,
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
            timeout_ms=client.operation_timeout_ms,
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
            timeout_ms=client.operation_timeout_ms,
            idempotent=True,
            handler=lambda input_, ctx: _kb_search(input_, ctx, client),
        )
    )
    return registry


async def _get_customer(input_: GetCustomerInput, ctx: ToolContext, client: HTTPBusinessClient) -> CustomerOutput:
    if input_.user_id != ctx.actor.user_id and "crm:admin" not in ctx.actor.scopes:
        raise ToolError(FORBIDDEN, "Cannot read another user's customer profile")
    payload = await client.get(f"/customers/{_path_segment(input_.user_id)}", ctx=ctx)
    return CustomerOutput.model_validate(payload)


async def _search_orders(input_: SearchOrdersInput, ctx: ToolContext, client: HTTPBusinessClient) -> SearchOrdersOutput:
    customer_id = await _resolve_customer_id(
        input_.customer_id,
        ctx,
        client,
        admin_scope="order:admin",
    )
    params = {"customer_id": customer_id}
    if input_.status:
        params["status"] = input_.status
    payload = await client.get("/orders", params=params, ctx=ctx)
    orders = payload.get("orders", payload) if isinstance(payload, dict) else payload
    validated_orders = [OrderOutput.model_validate(item) for item in orders]
    if "order:admin" not in ctx.actor.scopes:
        for order in validated_orders:
            if order.customer_id != customer_id:
                raise ToolError(FORBIDDEN, "Order resource does not belong to actor")
    return SearchOrdersOutput(orders=validated_orders)


async def _get_order(input_: GetOrderInput, ctx: ToolContext, client: HTTPBusinessClient) -> OrderOutput:
    payload = await client.get(f"/orders/{_path_segment(input_.order_id)}", ctx=ctx)
    order = OrderOutput.model_validate(payload)
    await _ensure_order_owned(order.customer_id, ctx, client)
    return order


async def _track_shipment(
    input_: TrackShipmentInput,
    ctx: ToolContext,
    client: HTTPBusinessClient,
) -> TrackShipmentOutput:
    payload = await client.get(f"/shipments/{_path_segment(input_.logistics_id)}", ctx=ctx)
    if isinstance(payload, dict) and payload.get("customer_id"):
        await _ensure_order_owned(str(payload["customer_id"]), ctx, client)
    return TrackShipmentOutput.model_validate(payload)


async def _create_ticket(
    input_: CreateTicketInput,
    ctx: ToolContext,
    client: HTTPBusinessClient,
) -> CreateTicketOutput:
    customer_id = await _resolve_customer_id(
        input_.customer_id,
        ctx,
        client,
        admin_scope="crm:admin",
    )
    body = input_.model_dump()
    body["customer_id"] = customer_id
    payload = await client.post("/tickets", json=body, ctx=ctx)
    return CreateTicketOutput.model_validate(payload)


async def _kb_search(input_: KBSearchInput, ctx: ToolContext, client: HTTPBusinessClient) -> KBSearchOutput:
    payload = await client.get(
        "/knowledge/search",
        params={"query": input_.query, "limit": input_.limit},
        ctx=ctx,
    )
    hits = payload.get("hits", payload) if isinstance(payload, dict) else payload
    return KBSearchOutput(hits=hits)


async def _actor_customer(ctx: ToolContext, client: HTTPBusinessClient) -> CustomerOutput:
    return await _get_customer(GetCustomerInput(user_id=ctx.actor.user_id), ctx, client)


async def _resolve_customer_id(
    customer_id: str,
    ctx: ToolContext,
    client: HTTPBusinessClient,
    *,
    admin_scope: str,
) -> str:
    actor_customer = await _actor_customer(ctx, client)
    if customer_id == "SELF":
        return actor_customer.customer_id
    if customer_id != actor_customer.customer_id and admin_scope not in ctx.actor.scopes:
        raise ToolError(FORBIDDEN, "Customer resource does not belong to actor")
    return customer_id


async def _ensure_order_owned(
    customer_id: str,
    ctx: ToolContext,
    client: HTTPBusinessClient,
) -> None:
    if "order:admin" in ctx.actor.scopes:
        return
    actor_customer = await _actor_customer(ctx, client)
    if customer_id != actor_customer.customer_id:
        raise ToolError(FORBIDDEN, "Order resource does not belong to actor")


def _path_segment(value: str) -> str:
    return quote(value, safe="")

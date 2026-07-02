from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from support_agent_lab.data.fixtures import DemoStore
from support_agent_lab.memory.store import KnowledgeIndex
from support_agent_lab.tools.errors import FORBIDDEN, NOT_FOUND, ToolError
from support_agent_lab.tools.registry import ToolContext, ToolDefinition, ToolRegistry


USER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$"
CUSTOMER_ID_PATTERN = r"^(SELF|[A-Za-z0-9][A-Za-z0-9_.:-]{0,127})$"
ORDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
LOGISTICS_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
STATUS_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"


class GetCustomerInput(BaseModel):
    user_id: str = Field(pattern=USER_ID_PATTERN)


class CustomerOutput(BaseModel):
    customer_id: str
    name: str
    tier: str
    email_masked: str | None = None
    phone_masked: str | None = None
    verified: bool


class SearchOrdersInput(BaseModel):
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    status: str | None = Field(default=None, pattern=STATUS_PATTERN)


class GetOrderInput(BaseModel):
    order_id: str = Field(pattern=ORDER_ID_PATTERN)


class OrderOutput(BaseModel):
    order_id: str
    customer_id: str
    status: str
    product: str
    amount: int
    currency: str
    delivered_at: str | None = None
    logistics_id: str | None = None
    returnable: bool


class SearchOrdersOutput(BaseModel):
    orders: list[OrderOutput]


class TrackShipmentInput(BaseModel):
    logistics_id: str = Field(pattern=LOGISTICS_ID_PATTERN)


class TrackShipmentOutput(BaseModel):
    logistics_id: str
    status: str
    latest_event: str
    eta: str


class CreateTicketInput(BaseModel):
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    priority: str = "normal"
    tags: list[str] = Field(default_factory=list)


class CreateTicketOutput(BaseModel):
    ticket_id: str
    status: str
    created_at: str


class KBSearchInput(BaseModel):
    query: str
    limit: int = Field(default=4, ge=1, le=8)


class KBSearchOutput(BaseModel):
    hits: list[dict]


async def get_customer(input_: GetCustomerInput, ctx: ToolContext, store: DemoStore) -> CustomerOutput:
    if input_.user_id != ctx.actor.user_id and "crm:admin" not in ctx.actor.scopes:
        raise ToolError(FORBIDDEN, "Cannot read another user's customer profile")
    customer = store.customers.get(input_.user_id)
    if not customer:
        raise ToolError(NOT_FOUND, f"Customer for user {input_.user_id} not found")
    return CustomerOutput.model_validate(customer)


async def search_orders(input_: SearchOrdersInput, ctx: ToolContext, store: DemoStore) -> SearchOrdersOutput:
    customer_id = _resolve_customer_id(input_.customer_id, ctx, store, admin_scope="order:admin")
    orders = []
    for order in store.orders.values():
        if order["customer_id"] != customer_id:
            continue
        if input_.status and order["status"] != input_.status:
            continue
        orders.append(OrderOutput.model_validate(order))
    return SearchOrdersOutput(orders=orders)


async def get_order(input_: GetOrderInput, ctx: ToolContext, store: DemoStore) -> OrderOutput:
    order = store.orders.get(input_.order_id.upper())
    if not order:
        raise ToolError(NOT_FOUND, f"Order {input_.order_id} not found")
    _ensure_order_owned(order, ctx, store)
    return OrderOutput.model_validate(order)


async def track_shipment(input_: TrackShipmentInput, ctx: ToolContext, store: DemoStore) -> TrackShipmentOutput:
    order = next(
        (item for item in store.orders.values() if item.get("logistics_id") == input_.logistics_id),
        None,
    )
    if not order:
        raise ToolError(NOT_FOUND, f"Shipment {input_.logistics_id} not found")
    _ensure_order_owned(order, ctx, store)
    return TrackShipmentOutput(
        logistics_id=input_.logistics_id,
        status="in_transit",
        latest_event="Package arrived at Shanghai transfer center",
        eta="2026-07-04",
    )


async def create_ticket(input_: CreateTicketInput, ctx: ToolContext, store: DemoStore) -> CreateTicketOutput:
    customer_id = _resolve_customer_id(input_.customer_id, ctx, store, admin_scope="crm:admin")
    ticket_id = f"T{len(store.tickets) + 1001}"
    created_at = datetime.now(timezone.utc).isoformat()
    store.tickets[ticket_id] = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "title": input_.title,
        "description": input_.description,
        "priority": input_.priority,
        "tags": input_.tags,
        "status": "open",
        "created_at": created_at,
    }
    return CreateTicketOutput(ticket_id=ticket_id, status="open", created_at=created_at)


async def kb_search(input_: KBSearchInput, ctx: ToolContext, knowledge: KnowledgeIndex) -> KBSearchOutput:
    trace = knowledge.search(input_.query, limit=input_.limit)
    return KBSearchOutput(
        hits=[
            {
                "document_id": hit.document_id,
                "title": hit.title,
                "source_uri": hit.source_uri,
                "score": hit.score,
                "content": hit.content,
            }
            for hit in trace.selected_context
        ]
    )


def create_registry(store: DemoStore, knowledge: KnowledgeIndex) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="crm.get_customer",
            description="Look up a masked customer profile by user id.",
            input_model=GetCustomerInput,
            output_model=CustomerOutput,
            required_scopes=["crm:read"],
            timeout_ms=1500,
            idempotent=True,
            handler=lambda input_, ctx: get_customer(input_, ctx, store),
        )
    )
    registry.register(
        ToolDefinition(
            name="order.search",
            description="Search orders for a customer. Use before acting on ambiguous order requests.",
            input_model=SearchOrdersInput,
            output_model=SearchOrdersOutput,
            required_scopes=["order:read"],
            timeout_ms=1500,
            idempotent=True,
            handler=lambda input_, ctx: search_orders(input_, ctx, store),
        )
    )
    registry.register(
        ToolDefinition(
            name="order.get",
            description="Fetch one order by id.",
            input_model=GetOrderInput,
            output_model=OrderOutput,
            required_scopes=["order:read"],
            timeout_ms=1500,
            idempotent=True,
            handler=lambda input_, ctx: get_order(input_, ctx, store),
        )
    )
    registry.register(
        ToolDefinition(
            name="shipping.track",
            description="Track a shipment by logistics id.",
            input_model=TrackShipmentInput,
            output_model=TrackShipmentOutput,
            required_scopes=["shipping:read"],
            timeout_ms=1500,
            idempotent=True,
            handler=lambda input_, ctx: track_shipment(input_, ctx, store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ticket.create",
            description="Create a support ticket for follow-up or human handoff.",
            input_model=CreateTicketInput,
            output_model=CreateTicketOutput,
            required_scopes=["ticket:write"],
            timeout_ms=2000,
            idempotent=False,
            handler=lambda input_, ctx: create_ticket(input_, ctx, store),
        )
    )
    registry.register(
        ToolDefinition(
            name="kb.search",
            description="Search the support knowledge base and return source-backed snippets.",
            input_model=KBSearchInput,
            output_model=KBSearchOutput,
            required_scopes=["kb:read"],
            timeout_ms=1500,
            idempotent=True,
            handler=lambda input_, ctx: kb_search(input_, ctx, knowledge),
        )
    )
    return registry


def _customer_for_actor(ctx: ToolContext, store: DemoStore) -> dict:
    customer = store.customers.get(ctx.actor.user_id)
    if not customer:
        raise ToolError(NOT_FOUND, f"Customer for user {ctx.actor.user_id} not found")
    return customer


def _resolve_customer_id(
    customer_id: str,
    ctx: ToolContext,
    store: DemoStore,
    *,
    admin_scope: str,
) -> str:
    actor_customer = _customer_for_actor(ctx, store)
    expected = actor_customer["customer_id"]
    if customer_id == "SELF":
        return expected
    if customer_id != expected and admin_scope not in ctx.actor.scopes:
        raise ToolError(FORBIDDEN, "Customer resource does not belong to actor")
    return customer_id


def _ensure_order_owned(order: dict, ctx: ToolContext, store: DemoStore) -> None:
    actor_customer = _customer_for_actor(ctx, store)
    if order["customer_id"] != actor_customer["customer_id"] and "order:admin" not in ctx.actor.scopes:
        raise ToolError(FORBIDDEN, "Order resource does not belong to actor")

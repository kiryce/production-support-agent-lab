import pytest

from support_agent_lab.bootstrap import create_container
from support_agent_lab.models import ToolStatus
from support_agent_lab.tools.registry import Actor, ToolContext, ToolFault, ToolFaultProfile


@pytest.mark.asyncio
async def test_write_tool_requires_idempotency_key():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
    )

    result = await container.tools.call(
        "ticket.create",
        {
            "customer_id": "cust_1001",
            "title": "Need help",
            "description": "A write without idempotency should fail.",
        },
        ctx,
    )

    assert result.status == ToolStatus.failed
    assert result.error_code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_idempotent_replay_returns_first_write_result():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
        idempotency_key="same-ticket",
    )
    payload = {
        "customer_id": "cust_1001",
        "title": "Need help",
        "description": "The same request may be replayed safely.",
    }

    first = await container.tools.call("ticket.create", payload, ctx)
    second = await container.tools.call("ticket.create", payload, ctx)

    assert first.status == ToolStatus.success
    assert second.status == ToolStatus.success
    assert first.data["ticket_id"] == second.data["ticket_id"]


@pytest.mark.asyncio
async def test_idempotency_key_conflict_rejects_changed_payload():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
        idempotency_key="same-key-different-payload",
    )
    first_payload = {
        "customer_id": "cust_1001",
        "title": "Need help",
        "description": "Original payload.",
    }
    changed_payload = {
        "customer_id": "cust_1001",
        "title": "Need help again",
        "description": "Changed payload.",
    }

    first = await container.tools.call("ticket.create", first_payload, ctx)
    conflict = await container.tools.call("ticket.create", changed_payload, ctx)

    assert first.status == ToolStatus.success
    assert conflict.status == ToolStatus.failed
    assert conflict.error_code == "IDEMPOTENCY_CONFLICT"
    assert len(container.store.tickets) == 1


@pytest.mark.asyncio
async def test_idempotency_keys_are_scoped_by_actor_user():
    container = create_container()
    demo_ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_demo",
        trace_id="trace_1",
        tenant_id="demo_tenant",
        idempotency_key="shared-operation-key",
    )
    guest_ctx = ToolContext(
        actor=Actor(
            user_id="user_guest",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_guest",
        trace_id="trace_2",
        tenant_id="demo_tenant",
        idempotency_key="shared-operation-key",
    )

    demo_ticket = await container.tools.call(
        "ticket.create",
        {
            "customer_id": "cust_1001",
            "title": "Demo user ticket",
            "description": "Same idempotency key as guest.",
        },
        demo_ctx,
    )
    guest_ticket = await container.tools.call(
        "ticket.create",
        {
            "customer_id": "cust_2001",
            "title": "Guest user ticket",
            "description": "Same idempotency key as demo user.",
        },
        guest_ctx,
    )

    assert demo_ticket.status == ToolStatus.success
    assert guest_ticket.status == ToolStatus.success
    assert demo_ticket.data["ticket_id"] != guest_ticket.data["ticket_id"]
    assert len(container.store.tickets) == 2


@pytest.mark.asyncio
async def test_order_tool_enforces_customer_ownership():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_guest",
            tenant_id="demo_tenant",
            scopes=["order:read"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
    )

    result = await container.tools.call("order.get", {"order_id": "A1001"}, ctx)

    assert result.status == ToolStatus.failed
    assert result.error_code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_shipping_tool_enforces_customer_ownership():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_guest",
            tenant_id="demo_tenant",
            scopes=["shipping:read"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
    )

    result = await container.tools.call("shipping.track", {"logistics_id": "YT99887766CN"}, ctx)

    assert result.status == ToolStatus.failed
    assert result.error_code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_ticket_create_enforces_customer_ownership():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_guest",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
        idempotency_key="guest-cross-customer-ticket",
    )

    result = await container.tools.call(
        "ticket.create",
        {
            "customer_id": "cust_1001",
            "title": "Cross-customer ticket",
            "description": "Guest should not create tickets for another customer.",
        },
        ctx,
    )

    assert result.status == ToolStatus.failed
    assert result.error_code == "FORBIDDEN"
    assert container.store.tickets == {}


@pytest.mark.asyncio
async def test_fault_profile_injects_retryable_timeout_once_and_audits_it():
    container = create_container()
    container.tools.fault_profile = ToolFaultProfile().add(
        "shipping.track",
        ToolFault(
            error_code="TIMEOUT",
            message="Injected shipping timeout.",
            retryable=True,
        ),
    )
    ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=["shipping:read"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
    )

    failed = await container.tools.call("shipping.track", {"logistics_id": "YT99887766CN"}, ctx)
    retried = await container.tools.call("shipping.track", {"logistics_id": "YT99887766CN"}, ctx)

    assert failed.status == ToolStatus.failed
    assert failed.error_code == "TIMEOUT"
    assert failed.retryable is True
    assert retried.status == ToolStatus.success
    assert container.tools.audit_log[-2].error_code == "TIMEOUT"
    assert container.tools.audit_log[-1].error_code is None


@pytest.mark.asyncio
async def test_fault_profile_does_not_bypass_authorization_or_get_consumed():
    container = create_container()
    container.tools.fault_profile = ToolFaultProfile().add(
        "shipping.track",
        ToolFault(
            error_code="TIMEOUT",
            message="Injected shipping timeout.",
            retryable=True,
        ),
    )
    ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=[],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
    )

    result = await container.tools.call("shipping.track", {"logistics_id": "YT99887766CN"}, ctx)

    assert result.status == ToolStatus.failed
    assert result.error_code == "FORBIDDEN"
    assert container.tools.fault_profile.faults_by_tool["shipping.track"]


@pytest.mark.asyncio
async def test_fault_profile_does_not_break_idempotent_write_replay():
    container = create_container()
    ctx = ToolContext(
        actor=Actor(
            user_id="user_demo",
            tenant_id="demo_tenant",
            scopes=["ticket:write"],
        ),
        request_id="req_1",
        trace_id="trace_1",
        tenant_id="demo_tenant",
        idempotency_key="fault-after-success",
    )
    payload = {
        "customer_id": "cust_1001",
        "title": "Need follow-up",
        "description": "The first write should be replayed safely.",
    }

    first = await container.tools.call("ticket.create", payload, ctx)
    container.tools.fault_profile = ToolFaultProfile().add(
        "ticket.create",
        ToolFault(
            error_code="UPSTREAM_UNAVAILABLE",
            message="Injected ticketing outage after the write already succeeded.",
            retryable=True,
        ),
    )
    replay = await container.tools.call("ticket.create", payload, ctx)

    assert first.status == ToolStatus.success
    assert replay.status == ToolStatus.success
    assert replay.data["ticket_id"] == first.data["ticket_id"]
    assert container.tools.fault_profile.faults_by_tool["ticket.create"]

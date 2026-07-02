import pytest

from support_agent_lab.agent.orchestrator import SupportAgentOrchestrator
from support_agent_lab.data.fixtures import DemoStore
from support_agent_lab.llm.gateway import create_default_llm_gateway
from support_agent_lab.memory.event_store import SQLiteEventStore, StoredEvent
from support_agent_lab.memory.replay import replay_conversation_memory
from support_agent_lab.memory.store import ConversationMemory, KnowledgeIndex
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.tools.business_tools import create_registry
from support_agent_lab.tools.registry import ToolBroker


@pytest.mark.asyncio
async def test_orchestrator_writes_append_only_events(tmp_path):
    store = DemoStore.seeded()
    knowledge = KnowledgeIndex()
    event_store = SQLiteEventStore(tmp_path / "events.db")
    tools = ToolBroker(
        registry=create_registry(store, knowledge),
        idempotency_store=store.idempotency,
    )
    orchestrator = SupportAgentOrchestrator(
        tenant_id="demo_tenant",
        memory=ConversationMemory(),
        knowledge=knowledge,
        tools=tools,
        llm=create_default_llm_gateway(),
        event_store=event_store,
        monitor=OnlineMonitorAgent(),
    )

    response = await orchestrator.handle_message(
        conversation_id="conv_events",
        user_id="user_demo",
        text="\u6211\u8ba2\u5355 A1001 \u7684\u8033\u673a\u574f\u4e86\uff0c\u80fd\u9000\u5417\uff1f",
    )

    events = event_store.list_events(conversation_id="conv_events")
    event_types = [event.event_type for event in events]
    assert event_types == [
        "message.user",
        "message.assistant",
        "agent.run.completed",
        "monitor.reviewed",
    ]
    run_event = [event for event in events if event.event_type == "agent.run.completed"][0]
    monitor_event = [event for event in events if event.event_type == "monitor.reviewed"][0]
    assert run_event.payload["id"] == response.trace.id
    assert run_event.payload["tool_results"]
    assert run_event.payload["llm_calls"]
    assert monitor_event.tenant_id == "demo_tenant"


@pytest.mark.asyncio
async def test_event_log_replays_conversation_memory_state(tmp_path):
    store = DemoStore.seeded()
    knowledge = KnowledgeIndex()
    event_store = SQLiteEventStore(tmp_path / "events.db")
    tools = ToolBroker(
        registry=create_registry(store, knowledge),
        idempotency_store=store.idempotency,
    )
    memory = ConversationMemory()
    orchestrator = SupportAgentOrchestrator(
        tenant_id="demo_tenant",
        memory=memory,
        knowledge=knowledge,
        tools=tools,
        llm=create_default_llm_gateway(),
        event_store=event_store,
        monitor=OnlineMonitorAgent(),
    )

    await orchestrator.handle_message("conv_replay", "user_demo", "Where is order A1002 shipping?")
    await orchestrator.handle_message("conv_replay", "user_demo", "I also need an invoice copy.")
    await orchestrator.handle_message("conv_replay", "user_demo", "Can you remind me what order this was?")

    result = replay_conversation_memory(event_store.list_events(conversation_id="conv_replay"))
    live_state = memory.states["conv_replay"]

    assert result.conversation_id == "conv_replay"
    assert result.replayed_message_count == len(live_state.messages)
    assert result.replayed_run_count == 3
    assert result.ignored_event_count == 3
    assert [message.id for message in result.state.messages] == [message.id for message in live_state.messages]
    assert [message.role for message in result.state.messages] == [message.role for message in live_state.messages]
    assert result.state.facts["last_order_id"] == "A1002"
    assert result.state.facts == live_state.facts
    assert result.state.working_summary == live_state.working_summary
    assert result.state.last_intent == live_state.last_intent


def test_memory_replay_rejects_mismatched_message_payload():
    message_payload = {
        "id": "msg_1",
        "tenant_id": "demo_tenant",
        "conversation_id": "conv_payload",
        "user_id": "user_demo",
        "role": "user",
        "content": "Where is order A1001?",
        "created_at": "2026-07-02T00:00:00+00:00",
        "metadata": {},
    }
    event = StoredEvent(
        id="evt_1",
        tenant_id="demo_tenant",
        conversation_id="conv_event",
        user_id="user_demo",
        event_type="message.user",
        payload=message_payload,
        created_at="2026-07-02T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match="conversation_id"):
        replay_conversation_memory([event])


def test_event_store_health_check_verifies_write_without_persisting_probe(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")

    event_store.health_check()

    assert event_store.list_events(event_type="readiness.probe") == []

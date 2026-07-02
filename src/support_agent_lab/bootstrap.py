from __future__ import annotations

from dataclasses import dataclass

from support_agent_lab.agent.orchestrator import SupportAgentOrchestrator
from support_agent_lab.config import Settings, get_settings
from support_agent_lab.data.fixtures import DemoStore
from support_agent_lab.llm.gateway import LLMGateway, create_llm_gateway
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex
from support_agent_lab.memory.store import ConversationMemory, KnowledgeIndex
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.tools.business_tools import create_registry
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient, create_http_registry
from support_agent_lab.tools.registry import ToolBroker


@dataclass
class AppContainer:
    settings: Settings
    store: DemoStore | None
    business_client: HTTPBusinessClient | None
    memory: ConversationMemory
    knowledge: KnowledgeIndex | HTTPKnowledgeIndex
    monitor: OnlineMonitorAgent
    tools: ToolBroker
    llm: LLMGateway
    event_store: SQLiteEventStore | None
    orchestrator: SupportAgentOrchestrator


def create_container() -> AppContainer:
    settings = get_settings()
    settings.validate_production_ready()
    memory = ConversationMemory()
    monitor = OnlineMonitorAgent()
    if settings.is_production:
        store = None
        knowledge = HTTPKnowledgeIndex(
            base_url=settings.app_knowledge_api_base_url or "",
            api_key=settings.app_knowledge_api_key,
            timeout_ms=settings.app_http_timeout_ms,
        )
        business_client = HTTPBusinessClient(
            base_url=settings.app_business_api_base_url or "",
            api_key=settings.app_business_api_key,
            timeout_ms=settings.app_http_timeout_ms,
        )
        registry = create_http_registry(business_client)
        idempotency_store = {}
    else:
        store = DemoStore.seeded()
        business_client = None
        knowledge = KnowledgeIndex()
        registry = create_registry(store, knowledge)
        idempotency_store = store.idempotency
    tools = ToolBroker(registry=registry, idempotency_store=idempotency_store)
    llm = create_llm_gateway(settings)
    event_store = SQLiteEventStore.from_url(settings.app_database_url)
    if settings.is_production and event_store is None:
        raise RuntimeError("Production mode requires a configured event store")
    orchestrator = SupportAgentOrchestrator(
        tenant_id=settings.app_tenant_id,
        memory=memory,
        knowledge=knowledge,
        tools=tools,
        llm=llm,
        event_store=event_store,
        monitor=monitor,
    )
    return AppContainer(
        settings=settings,
        store=store,
        business_client=business_client,
        memory=memory,
        knowledge=knowledge,
        monitor=monitor,
        tools=tools,
        llm=llm,
        event_store=event_store,
        orchestrator=orchestrator,
    )

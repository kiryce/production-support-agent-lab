import httpx
import pytest
from fastapi.testclient import TestClient

from support_agent_lab.api.main import app, get_container
from support_agent_lab.api.readiness import check_readiness
from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.config import Settings
from support_agent_lab.llm.gateway import LocalDeterministicProvider
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex
from support_agent_lab.memory.store import ConversationMemory, KnowledgeIndex
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient
from support_agent_lab.tools.registry import ToolBroker, ToolRegistry


def test_ready_endpoint_local_shallow_ok():
    client = TestClient(app)

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["deep"] is False
    assert body["environment"] == "local"
    assert {check["name"] for check in body["checks"]} >= {
        "config",
        "event_store",
        "llm",
        "business_api",
        "knowledge_api",
    }
    assert any(check["name"] == "business_api" and check["status"] == "skipped" for check in body["checks"])


@pytest.mark.asyncio
async def test_readiness_reports_event_store_failure():
    class BrokenEventStore:
        def health_check(self):
            raise RuntimeError("database locked")

    class HealthyLocalGateway:
        provider = LocalDeterministicProvider()

        async def health_check(self) -> None:
            return None

    container = AppContainer(
        settings=Settings(app_env="local"),
        store=None,
        business_client=None,
        memory=ConversationMemory(),
        knowledge=KnowledgeIndex(),
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=HealthyLocalGateway(),
        event_store=BrokenEventStore(),
        orchestrator=None,
    )

    report = await check_readiness(container)

    assert report.status == "not_ready"
    assert any(check.name == "event_store" and check.status == "failed" for check in report.checks)


@pytest.mark.asyncio
async def test_production_deep_readiness_checks_external_dependencies(tmp_path):
    async def business_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["authorization"] == "Bearer business-token"
        assert request.headers["x-tenant-id"] == "tenant_live"
        assert request.headers["x-actor-user-id"] == "readiness_probe"
        assert request.headers["x-trace-id"].startswith("ready_req_")
        return httpx.Response(200, json={"status": "ok"})

    async def knowledge_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["authorization"] == "Bearer knowledge-token"
        return httpx.Response(200, json={"status": "ok"})

    class HealthyGateway:
        provider = LocalDeterministicProvider(provider="openai", model="gpt-test")

        async def health_check(self) -> None:
            return None

    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret="actor-signing-secret-with-32-byte-minimum",
        app_database_url=f"sqlite:///{tmp_path / 'events.db'}",
    )
    container = AppContainer(
        settings=settings,
        store=None,
        business_client=HTTPBusinessClient(
            base_url="https://business.internal.test",
            api_key="business-token",
            transport=httpx.MockTransport(business_handler),
        ),
        memory=ConversationMemory(),
        knowledge=HTTPKnowledgeIndex(
            base_url="https://knowledge.internal.test",
            api_key="knowledge-token",
            transport=httpx.MockTransport(knowledge_handler),
        ),
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=HealthyGateway(),
        event_store=SQLiteEventStore.from_url(settings.app_database_url),
        orchestrator=None,
    )

    report = await check_readiness(container)

    assert report.status == "ok"
    assert report.deep is True
    assert {check.name: check.status for check in report.checks} == {
        "config": "ok",
        "event_store": "ok",
        "llm": "ok",
        "business_api": "ok",
        "knowledge_api": "ok",
    }


def test_ready_endpoint_returns_503_when_dependency_fails():
    class BrokenEventStore:
        def health_check(self):
            raise RuntimeError("database locked")

    class HealthyLocalGateway:
        provider = LocalDeterministicProvider()

        async def health_check(self) -> None:
            return None

    container = AppContainer(
        settings=Settings(app_env="local"),
        store=None,
        business_client=None,
        memory=ConversationMemory(),
        knowledge=KnowledgeIndex(),
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=HealthyLocalGateway(),
        event_store=BrokenEventStore(),
        orchestrator=None,
    )
    app.dependency_overrides[get_container] = lambda: container
    try:
        response = TestClient(app).get("/api/v1/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert any(check["name"] == "event_store" and check["status"] == "failed" for check in body["checks"])

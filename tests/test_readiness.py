import httpx
import pytest
from fastapi.testclient import TestClient

from support_agent_lab.audit.export_batch import AuditExportBatchOptions, run_audit_export_batch
from support_agent_lab.api.main import app, get_container
from support_agent_lab.api.readiness import check_readiness
from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.config import Settings
from support_agent_lab.llm.gateway import LLMGateway, LocalDeterministicProvider
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex
from support_agent_lab.memory.store import ConversationMemory, KnowledgeIndex
from support_agent_lab.models import utc_now
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient
from support_agent_lab.tools.registry import ToolBroker, ToolRegistry


INTERNAL_API_KEY = "internal-api-key-with-32-byte-minimum"
WEBHOOK_SECRET = "webhook-signing-secret-with-32-byte-minimum"


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
        "event_store_backup_dir",
        "audit_export_dir",
        "llm",
        "business_api",
        "knowledge_api",
    }
    assert any(
        check["name"] == "event_store_backup_dir" and check["status"] == "skipped"
        for check in body["checks"]
    )
    assert any(check["name"] == "audit_export_dir" and check["status"] == "skipped" for check in body["checks"])
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

    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key=INTERNAL_API_KEY,
        app_actor_signature_secret="actor-signing-secret-with-32-byte-minimum",
        app_database_url=f"sqlite:///{tmp_path / 'events.db'}",
        app_event_store_backup_dir=str(tmp_path / "backups"),
        app_audit_export_dir=str(tmp_path / "audit-exports"),
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
        llm=LLMGateway(provider=LocalDeterministicProvider(provider="openai", model="gpt-test")),
        event_store=SQLiteEventStore.from_url(settings.app_database_url),
        orchestrator=None,
    )

    report = await check_readiness(container)

    assert report.status == "ok"
    assert report.deep is True
    assert {check.name: check.status for check in report.checks} == {
        "config": "ok",
        "event_store": "ok",
        "event_store_backup_dir": "ok",
        "audit_export_dir": "ok",
        "llm": "ok",
        "business_api": "ok",
        "knowledge_api": "ok",
    }
    business_detail = {check.name: check.detail for check in report.checks}["business_api"]
    knowledge_detail = {check.name: check.detail for check in report.checks}["knowledge_api"]
    llm_detail = {check.name: check.detail for check in report.checks}["llm"]
    assert "circuit=closed" in llm_detail
    assert "retry_attempts=2" in llm_detail
    assert "circuit=closed" in business_detail
    assert "retry_attempts=2" in business_detail
    assert "circuit=closed" in knowledge_detail
    assert "retry_attempts=2" in knowledge_detail


@pytest.mark.asyncio
async def test_production_readiness_fails_when_backup_directory_is_not_writable(tmp_path):
    class HealthyLocalGateway:
        provider = LocalDeterministicProvider()

        async def health_check(self) -> None:
            return None

    backup_path = tmp_path / "backups"
    backup_path.write_text("not a directory", encoding="utf-8")
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key=INTERNAL_API_KEY,
        app_actor_signature_secret="actor-signing-secret-with-32-byte-minimum",
        app_database_url=f"sqlite:///{tmp_path / 'events.db'}",
        app_event_store_backup_dir=str(backup_path),
        app_audit_export_dir=str(tmp_path / "audit-exports"),
    )
    container = AppContainer(
        settings=settings,
        store=None,
        business_client=None,
        memory=ConversationMemory(),
        knowledge=KnowledgeIndex(),
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=HealthyLocalGateway(),
        event_store=SQLiteEventStore.from_url(settings.app_database_url),
        orchestrator=None,
    )

    report = await check_readiness(container, deep=False)

    checks = {check.name: check for check in report.checks}
    assert report.status == "not_ready"
    assert checks["event_store_backup_dir"].status == "failed"
    assert "backup directory probe failed" in checks["event_store_backup_dir"].detail


@pytest.mark.asyncio
async def test_production_readiness_fails_when_audit_export_directory_is_not_writable(tmp_path):
    class HealthyLocalGateway:
        provider = LocalDeterministicProvider()

        async def health_check(self) -> None:
            return None

    audit_export_path = tmp_path / "audit-exports"
    audit_export_path.write_text("not a directory", encoding="utf-8")
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key=INTERNAL_API_KEY,
        app_actor_signature_secret="actor-signing-secret-with-32-byte-minimum",
        app_database_url=f"sqlite:///{tmp_path / 'events.db'}",
        app_event_store_backup_dir=str(tmp_path / "backups"),
        app_audit_export_dir=str(audit_export_path),
    )
    container = AppContainer(
        settings=settings,
        store=None,
        business_client=None,
        memory=ConversationMemory(),
        knowledge=KnowledgeIndex(),
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=HealthyLocalGateway(),
        event_store=SQLiteEventStore.from_url(settings.app_database_url),
        orchestrator=None,
    )

    report = await check_readiness(container, deep=False)

    checks = {check.name: check for check in report.checks}
    assert report.status == "not_ready"
    assert checks["event_store_backup_dir"].status == "ok"
    assert checks["audit_export_dir"].status == "failed"
    assert "audit export directory probe failed" in checks["audit_export_dir"].detail


@pytest.mark.asyncio
async def test_readiness_skips_ops_checks_by_default(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    container = _production_ops_container(tmp_path, event_store=event_store)

    report = await check_readiness(container, deep=False)

    assert report.status == "ok"
    assert report.ops is False
    assert {
        "alert_dispatcher_worker",
        "monitor_review_worker",
        "audit_export_batch",
    }.isdisjoint({check.name for check in report.checks})


@pytest.mark.asyncio
async def test_readiness_ops_checks_fail_when_background_workers_are_missing(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    container = _production_ops_container(tmp_path, event_store=event_store)

    report = await check_readiness(container, deep=False, ops=True)

    checks = {check.name: check for check in report.checks}
    assert report.status == "not_ready"
    assert report.ops is True
    assert checks["alert_dispatcher_worker"].status == "failed"
    assert "status=missing" in checks["alert_dispatcher_worker"].detail
    assert checks["monitor_review_worker"].status == "failed"
    assert "status=missing" in checks["monitor_review_worker"].detail
    assert checks["audit_export_batch"].status == "failed"
    assert "status=missing" in checks["audit_export_batch"].detail


@pytest.mark.asyncio
async def test_readiness_ops_checks_pass_when_background_workers_are_fresh(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    now = utc_now()
    event_store.record_alert_dispatcher_heartbeat(
        tenant_id="tenant_live",
        worker_id="dispatcher-private-host",
        status="idle",
        cycle_status="success",
        last_cycle_completed_at=now,
        sent_count=1,
        now=now,
    )
    event_store.record_monitor_review_worker_heartbeat(
        tenant_id="tenant_live",
        worker_id="monitor-review-private-host",
        status="idle",
        cycle_status="success",
        last_cycle_completed_at=now,
        reviewed_count=2,
        now=now,
    )
    event_store.append(
        tenant_id="tenant_live",
        conversation_id="conv_ops_readiness",
        user_id="user_ops_readiness",
        run_id="run_ops_readiness",
        event_type="message.user",
        payload={"content": "PRIVATE readiness audit payload"},
    )
    run_audit_export_batch(
        event_store=event_store,
        tenant_id="tenant_live",
        output_dir=tmp_path / "audit-exports",
        actor_user_id="audit-readiness-private-actor",
        owner_id="audit-readiness-private-worker",
        options=AuditExportBatchOptions(limit=10),
    )
    container = _production_ops_container(tmp_path, event_store=event_store)

    report = await check_readiness(container, deep=False, ops=True)

    checks = {check.name: check for check in report.checks}
    serialized = report.model_dump_json()
    assert report.status == "ok"
    assert checks["alert_dispatcher_worker"].status == "ok"
    assert checks["monitor_review_worker"].status == "ok"
    assert checks["audit_export_batch"].status == "ok"
    assert "dispatcher-private-host" not in serialized
    assert "monitor-review-private-host" not in serialized
    assert "audit-readiness-private-worker" not in serialized
    assert "PRIVATE readiness audit payload" not in serialized


def test_ready_endpoint_runs_ops_checks_when_requested(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    container = _production_ops_container(tmp_path, event_store=event_store)
    app.dependency_overrides[get_container] = lambda: container
    try:
        response = TestClient(app).get("/api/v1/ready?deep=false&ops=true")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["ops"] is True
    assert {check["name"] for check in body["checks"]} >= {
        "alert_dispatcher_worker",
        "monitor_review_worker",
        "audit_export_batch",
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


def _production_ops_container(tmp_path, *, event_store: SQLiteEventStore) -> AppContainer:
    class HealthyLocalGateway:
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
        app_internal_api_key=INTERNAL_API_KEY,
        app_actor_signature_secret="actor-signing-secret-with-32-byte-minimum",
        app_monitor_alert_webhook_enabled=True,
        app_monitor_alert_webhook_url="https://hooks.internal.test/alerts",
        app_monitor_alert_webhook_secret=WEBHOOK_SECRET,
        app_database_url=f"sqlite:///{tmp_path / 'events.db'}",
        app_event_store_backup_dir=str(tmp_path / "backups"),
        app_audit_export_dir=str(tmp_path / "audit-exports"),
    )
    return AppContainer(
        settings=settings,
        store=None,
        business_client=None,
        memory=ConversationMemory(),
        knowledge=KnowledgeIndex(),
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=HealthyLocalGateway(),
        event_store=event_store,
        orchestrator=None,
    )

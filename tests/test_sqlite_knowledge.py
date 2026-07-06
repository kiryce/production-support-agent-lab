import json

import httpx
import pytest
from fastapi.testclient import TestClient

from support_agent_lab.api.main import app, get_container
from support_agent_lab.api.readiness import check_readiness
from support_agent_lab.bootstrap import AppContainer, create_container
from support_agent_lab.config import Settings, get_settings
from support_agent_lab.llm.gateway import LLMGateway, LocalDeterministicProvider
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.memory.sqlite_knowledge import (
    KnowledgeDocumentInput,
    SQLiteKnowledgeIndex,
    load_documents_from_paths,
)
from support_agent_lab.memory.store import ConversationMemory
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.scripts.knowledge_index_ops import main as knowledge_index_main
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient
from support_agent_lab.tools.registry import ToolBroker, ToolRegistry


ACTOR_SIGNATURE_SECRET = "actor-signing-secret-with-32-byte-minimum"


def _db_url(path) -> str:
    return f"sqlite:///{path}"


def test_sqlite_knowledge_ingests_documents_and_returns_retrieval_trace(tmp_path):
    index = SQLiteKnowledgeIndex(tmp_path / "knowledge.db", tenant_id="tenant_docs")

    report = index.ingest_documents(
        [
            KnowledgeDocumentInput(
                document_id="returns-policy",
                title="Return Policy",
                content=(
                    "# Return Policy\n\n"
                    "Damaged headphones can be returned within 30 days after inspection. "
                    "Refunds are issued after the warehouse receives the item."
                ),
                source_uri="kb://policies/returns.md",
                metadata={"category": "returns", "private_note": "do-not-render-in-summary"},
            )
        ],
        source_label="policies",
    )
    trace = index.search("broken headphones refund", limit=2)
    summary = index.summary()

    assert report.document_count == 1
    assert report.chunk_count >= 1
    assert trace.selected_context
    assert trace.selected_context[0].document_id == "returns-policy"
    assert trace.selected_context[0].source_uri == "kb://policies/returns.md"
    assert trace.candidates_by_stage["selected"] == 1
    assert summary.document_count == 1
    assert summary.chunk_count >= 1
    assert summary.database_file == "knowledge.db"
    assert summary.database_path_hash


def test_load_documents_from_paths_uses_safe_source_uri_and_path_hash(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "returns.md").write_text("# Returns\n\nReturn damaged goods within 30 days.", encoding="utf-8")

    documents = load_documents_from_paths([source], source_label="merchant policies")

    assert len(documents) == 1
    doc = documents[0]
    assert doc.document_id.startswith("returns_")
    assert doc.source_uri == "kb://merchant-policies/returns.md"
    assert doc.metadata["source_file"] == "returns.md"
    assert doc.metadata["source_path_hash"]
    assert str(tmp_path) not in json.dumps(doc.metadata, ensure_ascii=False)


def test_sqlite_knowledge_ingest_cli_stats_and_search(tmp_path, capsys):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "invoice.md").write_text(
        "# Invoice Policy\n\nInvoices are issued within 24 hours after payment.",
        encoding="utf-8",
    )
    db_path = tmp_path / "knowledge.db"

    ingest_exit = knowledge_index_main(
        [
            "--database-url",
            _db_url(db_path),
            "--tenant-id",
            "tenant_cli",
            "--json",
            "ingest",
            "--source",
            str(source),
            "--source-label",
            "policies",
        ]
    )
    ingest_out = json.loads(capsys.readouterr().out)
    stats_exit = knowledge_index_main(
        [
            "--database-url",
            _db_url(db_path),
            "--tenant-id",
            "tenant_cli",
            "--json",
            "stats",
        ]
    )
    stats_out = json.loads(capsys.readouterr().out)
    search_exit = knowledge_index_main(
        [
            "--database-url",
            _db_url(db_path),
            "--tenant-id",
            "tenant_cli",
            "--json",
            "search",
            "invoice",
        ]
    )
    search_out = json.loads(capsys.readouterr().out)

    assert ingest_exit == 0
    assert ingest_out["document_count"] == 1
    assert ingest_out["chunk_count"] == 1
    assert stats_exit == 0
    assert stats_out["document_count"] == 1
    assert stats_out["database_file"] == "knowledge.db"
    assert search_exit == 0
    assert search_out["selected_context"][0]["source_uri"] == "kb://policies/invoice.md"
    assert "Invoices are issued" in search_out["selected_context"][0]["content_snippet"]


def test_production_config_accepts_sqlite_knowledge_backend(tmp_path):
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_backend="sqlite",
        app_knowledge_database_url=_db_url(tmp_path / "knowledge.db"),
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
    )

    settings.validate_production_ready()


def test_production_config_rejects_memory_knowledge_backend():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_backend="memory",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
    )

    with pytest.raises(RuntimeError, match="APP_KNOWLEDGE_BACKEND"):
        settings.validate_production_ready()


def test_production_container_can_use_sqlite_knowledge_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_live")
    monkeypatch.setenv("APP_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("APP_BUSINESS_API_BASE_URL", "https://business.internal.test")
    monkeypatch.setenv("APP_BUSINESS_API_KEY", "business-token")
    monkeypatch.setenv("APP_KNOWLEDGE_BACKEND", "sqlite")
    monkeypatch.setenv("APP_KNOWLEDGE_DATABASE_URL", _db_url(tmp_path / "knowledge.db"))
    monkeypatch.setenv("APP_INTERNAL_API_KEY", "internal-test-key")
    monkeypatch.setenv("APP_ACTOR_SIGNATURE_SECRET", ACTOR_SIGNATURE_SECRET)
    monkeypatch.setenv("APP_DATABASE_URL", _db_url(tmp_path / "events.db"))
    get_settings.cache_clear()
    try:
        container = create_container()
    finally:
        get_settings.cache_clear()

    assert container.store is None
    assert isinstance(container.knowledge, SQLiteKnowledgeIndex)
    assert container.knowledge.database_path.name == "knowledge.db"


@pytest.mark.asyncio
async def test_readiness_accepts_production_sqlite_knowledge_index(tmp_path):
    async def business_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    knowledge = SQLiteKnowledgeIndex(tmp_path / "knowledge.db", tenant_id="tenant_live")
    knowledge.ingest_documents(
        [
            KnowledgeDocumentInput(
                document_id="invoice-policy",
                title="Invoice Policy",
                content="Invoices are issued within 24 hours.",
                source_uri="kb://policies/invoice.md",
            )
        ],
        source_label="policies",
    )
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_backend="sqlite",
        app_knowledge_database_url=_db_url(tmp_path / "knowledge.db"),
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
        app_database_url=_db_url(tmp_path / "events.db"),
        app_event_store_backup_dir=str(tmp_path / "backups"),
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
        knowledge=knowledge,
        monitor=OnlineMonitorAgent(),
        tools=ToolBroker(registry=ToolRegistry(), idempotency_store={}),
        llm=LLMGateway(provider=LocalDeterministicProvider(provider="openai", model="gpt-test")),
        event_store=SQLiteEventStore.from_url(settings.app_database_url),
        orchestrator=None,
    )

    report = await check_readiness(container, deep=True)
    checks = {check.name: check for check in report.checks}

    assert checks["knowledge_api"].status == "ok"
    assert "backend=sqlite" in checks["knowledge_api"].detail
    assert "documents=1" in checks["knowledge_api"].detail


def test_admin_knowledge_summary_uses_sanitized_sqlite_metadata(tmp_path, monkeypatch):
    knowledge = SQLiteKnowledgeIndex(tmp_path / "knowledge.db", tenant_id="demo_tenant")
    knowledge.ingest_documents(
        [
            KnowledgeDocumentInput(
                document_id="returns-policy",
                title="Return Policy",
                content="SECRET_DOC_BODY damaged goods can be returned within 30 days.",
                source_uri="kb://policies/returns.md",
                metadata={"private": "SECRET_METADATA"},
            )
        ],
        source_label="policies",
    )
    get_settings.cache_clear()
    app_container = create_container()
    app_container.knowledge = knowledge
    app_container.orchestrator.knowledge = knowledge
    app.dependency_overrides[get_container] = lambda: app_container
    try:
        response = TestClient(app).get(
            "/api/v1/admin/knowledge/summary",
            headers={"X-Demo-Role": "admin"},
        )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "sqlite"
    assert body["status"] == "ready"
    assert body["document_count"] == 1
    assert body["chunk_count"] == 1
    assert body["database_file"] == "knowledge.db"
    assert body["database_path_hash"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "SECRET_DOC_BODY" not in serialized
    assert "SECRET_METADATA" not in serialized

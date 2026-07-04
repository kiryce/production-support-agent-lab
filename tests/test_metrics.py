from fastapi.testclient import TestClient

from support_agent_lab.api.main import app, get_container
from support_agent_lab.api.metrics import render_prometheus_metrics
from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.config import Settings, get_settings
from support_agent_lab.llm.gateway import LLMGateway, LocalDeterministicProvider
from support_agent_lab.memory.store import ConversationMemory, KnowledgeIndex
from support_agent_lab.models import IntentType, MonitorEvent, RiskLevel, ToolStatus, utc_now
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.tools.registry import ToolAuditRecord, ToolBroker, ToolRegistry


def test_prometheus_metrics_render_operational_window_without_sensitive_payloads():
    container = _metrics_container(settings=Settings(app_tenant_id='tenant"blue\\one'))
    container.monitor.events.append(
        MonitorEvent(
            conversation_id="conv_metrics",
            run_id="run_metrics",
            agent_version="agent_test",
            user_intent=IntentType.order_status,
            risk_level=RiskLevel.medium,
            grounded=False,
            policy_compliant=True,
            needs_human_review=True,
            failure_types=["TIMEOUT", "LLM_FALLBACK"],
            summary="raw user text should not appear",
        )
    )
    container.tools.audit_log.append(
        ToolAuditRecord(
            id="audit_metrics",
            tool_name="shipping.track",
            tenant_id="tenant",
            actor_user_id="user_should_not_appear",
            request_id="req_should_not_appear",
            trace_id="trace_should_not_appear",
            argument_hash="hash_should_not_appear",
            status=ToolStatus.failed,
            latency_ms=42,
            error_code="TIMEOUT",
            created_at=utc_now().isoformat(),
        )
    )

    body = render_prometheus_metrics(container, source="live", window_hours=1)

    assert 'tenant="tenant\\"blue\\\\one"' in body
    assert "support_agent_monitor_events_window 1" in body
    assert 'support_agent_monitor_events_by_failure_window{failure_type="TIMEOUT"} 1' in body
    assert "support_agent_llm_fallback_monitor_events_window 1" in body
    assert "support_agent_tool_calls_window 1" in body
    assert 'support_agent_tool_failed_calls_by_tool_window{tool_name="shipping.track"} 1' in body
    assert 'support_agent_adapter_circuit_open{adapter="business"} 0' in body
    assert 'support_agent_llm_circuit_open{model="deterministic-support-agent",provider="local_deterministic"} 0' in body
    assert "raw user text should not appear" not in body
    assert "user_should_not_appear" not in body
    assert "trace_should_not_appear" not in body
    assert "hash_should_not_appear" not in body


def test_metrics_endpoint_is_scrapeable_when_signatures_and_rate_limits_are_enabled(monkeypatch):
    monkeypatch.setenv("APP_REQUEST_SIGNATURE_REQUIRED", "true")
    monkeypatch.setenv("APP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("APP_RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("APP_RATE_LIMIT_BURST", "1")
    get_settings.cache_clear()
    app.state.rate_limiter.reset()
    app.state.http_metrics.reset()
    app.dependency_overrides[get_container] = lambda: _metrics_container()
    try:
        client = TestClient(app)
        first = client.get("/metrics?source=live")
        second = client.get("/metrics?source=live")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        app.state.rate_limiter.reset()
        app.state.http_metrics.reset()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "X-RateLimit-Limit" not in first.headers
    assert "support_agent_info" in first.text


def test_metrics_endpoint_exports_http_and_rate_limit_live_counters(monkeypatch):
    monkeypatch.setenv("APP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("APP_RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("APP_RATE_LIMIT_BURST", "1")
    get_settings.cache_clear()
    app.state.rate_limiter.reset()
    app.state.http_metrics.reset()
    app.dependency_overrides[get_container] = lambda: _metrics_container()
    try:
        client = TestClient(app)
        health = client.get("/api/v1/health")
        allowed = client.post("/api/v1/chat/sessions", json={})
        blocked = client.post("/api/v1/chat/sessions", json={})
        metrics = client.get("/metrics?source=live")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        app.state.rate_limiter.reset()
        app.state.http_metrics.reset()

    assert health.status_code == 200
    assert allowed.status_code == 200
    assert blocked.status_code == 429
    assert 'support_agent_http_requests_total{method="GET",route_family="health",status="200"} 1' in metrics.text
    assert 'support_agent_http_requests_total{method="POST",route_family="chat",status="200"} 1' in metrics.text
    assert 'support_agent_http_requests_total{method="POST",route_family="chat",status="429"} 1' in metrics.text
    assert 'support_agent_rate_limit_decisions_total{decision="allowed",route_family="chat"} 1' in metrics.text
    assert 'support_agent_rate_limit_decisions_total{decision="blocked",route_family="chat"} 1' in metrics.text


def _metrics_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or Settings(app_env="local")
    monitor = OnlineMonitorAgent()
    tools = ToolBroker(registry=ToolRegistry(), idempotency_store={})
    llm = LLMGateway(provider=LocalDeterministicProvider(), timeout_ms=settings.app_llm_timeout_ms)
    return AppContainer(
        settings=settings,
        store=None,
        business_client=None,
        memory=ConversationMemory(),
        knowledge=KnowledgeIndex(),
        monitor=monitor,
        tools=tools,
        llm=llm,
        event_store=None,
        orchestrator=None,
    )

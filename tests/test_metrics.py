from datetime import timedelta

from fastapi.testclient import TestClient

from support_agent_lab.api.main import app, get_container
from support_agent_lab.api.metrics import render_prometheus_metrics
from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.config import Settings, get_settings
from support_agent_lab.llm.gateway import LLMGateway, LocalDeterministicProvider
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.memory.store import ConversationMemory, KnowledgeIndex
from support_agent_lab.models import (
    AlertDeliveryStatus,
    IntentType,
    MonitorAlertStatus,
    MonitorAlertTriageEvent,
    MonitorEvent,
    RiskLevel,
    ToolStatus,
    utc_now,
)
from support_agent_lab.monitoring.alert_dispatcher import build_alert_delivery_record, hash_alert_destination
from support_agent_lab.monitoring.monitor import MonitorAlert, OnlineMonitorAgent, monitor_alert_key
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


def test_prometheus_metrics_exports_alert_delivery_outbox_health(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    destination_hash = hash_alert_destination("https://hooks.internal.test/alerts")
    pending, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:order:TIMEOUT"),
            destination_hash=destination_hash,
        )
    )
    sent, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P0", key="agent:billing:POLICY"),
            destination_hash=destination_hash,
        )
    )
    dead_target, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P0", key="agent:general:PROMPT_INJECTION_ATTEMPT"),
            destination_hash=destination_hash,
        )
    )
    event_store.record_alert_delivery_attempt(
        sent.id,
        status=AlertDeliveryStatus.sent,
        response_status_code=204,
    )
    event_store.record_alert_delivery_attempt(
        dead_target.id,
        status=AlertDeliveryStatus.failed,
        response_status_code=503,
        last_error="HTTP_503",
        max_attempts=1,
        backoff_seconds=60,
    )
    settings = Settings(
        app_env="local",
        app_monitor_alert_webhook_enabled=True,
        app_monitor_alert_webhook_url="https://hooks.internal.test/alerts",
        app_monitor_alert_webhook_secret="webhook-signing-secret-with-32-byte-minimum",
    )
    container = _metrics_container(settings=settings, event_store=event_store)

    body = render_prometheus_metrics(container, source="event_store", window_hours=1)

    assert pending.alert_key not in body
    assert sent.alert_key not in body
    assert dead_target.alert_key not in body
    assert "support_agent_alert_delivery_webhook_enabled 1" in body
    assert "support_agent_alert_delivery_outbox_configured 1" in body
    assert 'support_agent_alert_delivery_records{status="pending"} 1' in body
    assert 'support_agent_alert_delivery_records{status="sent"} 1' in body
    assert 'support_agent_alert_delivery_records{status="dead"} 1' in body
    assert 'support_agent_alert_delivery_records_by_severity{severity="P0"} 2' in body
    assert "support_agent_alert_delivery_due_records 1" in body
    assert "support_agent_alert_delivery_attempts_recorded 2" in body
    assert 'support_agent_alert_delivery_health_status{status="failed"} 1' in body


def test_prometheus_metrics_exports_monitor_triage_health_without_high_cardinality_labels(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    first_seen = utc_now() - timedelta(minutes=70)
    first_event = MonitorEvent(
        conversation_id="conv_triage_metrics_1",
        run_id="run_triage_metrics_1",
        timestamp=first_seen,
        agent_version="agent_test",
        user_intent=IntentType.order_status,
        risk_level=RiskLevel.medium,
        grounded=True,
        policy_compliant=True,
        needs_human_review=True,
        failure_types=["TIMEOUT"],
        summary="shipping timeout with raw details",
    )
    second_event = first_event.model_copy(
        update={
            "id": "mon_triage_metrics_2",
            "conversation_id": "conv_triage_metrics_2",
            "run_id": "run_triage_metrics_2",
            "timestamp": first_seen + timedelta(minutes=5),
        }
    )
    alert_key = monitor_alert_key(first_event)
    event_store.append_monitor_event(first_event, tenant_id="demo_tenant")
    event_store.append_monitor_event(second_event, tenant_id="demo_tenant")
    event_store.append_monitor_alert_triage(
        MonitorAlertTriageEvent(
            id="triage_metrics_ack",
            alert_key=alert_key,
            status=MonitorAlertStatus.acknowledged,
            assignee_user_id="backend-oncall",
            actor_user_id="admin_user",
            note="ack before follow-up timeout",
            created_at=first_seen + timedelta(minutes=1),
        ),
        tenant_id="demo_tenant",
    )
    container = _metrics_container(event_store=event_store)

    body = render_prometheus_metrics(container, source="event_store", window_hours=1)

    assert alert_key not in body
    assert "run_triage_metrics_1" not in body
    assert "backend-oncall" not in body
    assert "ack before follow-up timeout" not in body
    assert "shipping timeout with raw details" not in body
    assert "support_agent_monitor_triage_active_alerts 1" in body
    assert "support_agent_monitor_triage_unassigned_active_alerts 0" in body
    assert "support_agent_monitor_triage_untriaged_alerts 0" in body
    assert "support_agent_monitor_triage_new_events_since_triage 1" in body
    assert "support_agent_monitor_triage_stale_active_alerts 1" in body
    assert "support_agent_monitor_triage_stale_threshold_seconds 3600" in body
    assert "support_agent_monitor_triage_mtta_seconds 60" in body
    assert 'support_agent_monitor_triage_health_status{status="degraded"} 1' in body
    assert 'support_agent_monitor_triage_alerts_by_status{status="acknowledged"} 1' in body
    assert 'support_agent_monitor_triage_alerts_by_severity{severity="P2"} 1' in body
    assert 'support_agent_monitor_triage_active_alerts_by_severity{severity="P2"} 1' in body
    assert "support_agent_monitor_triage_oldest_active_alert_age_seconds" in body
    assert "support_agent_monitor_triage_latest_action_timestamp_seconds" in body


def _metrics_container(
    settings: Settings | None = None,
    event_store: SQLiteEventStore | None = None,
) -> AppContainer:
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
        event_store=event_store,
        orchestrator=None,
    )


def _alert(
    *,
    severity: str = "P1",
    key: str = "agent:general:PROMPT_INJECTION_ATTEMPT",
    status: MonitorAlertStatus = MonitorAlertStatus.open,
) -> MonitorAlert:
    now = utc_now()
    return MonitorAlert(
        severity=severity,
        key=key,
        count=2,
        reason="PROMPT_INJECTION_ATTEMPT clustered across 2 event(s)",
        first_seen_at=now,
        last_seen_at=now,
        sample_event_ids=["mon_1", "mon_2"],
        sample_run_ids=["run_1", "run_2"],
        status=status,
    )

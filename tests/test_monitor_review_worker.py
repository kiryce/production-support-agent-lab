import json
from datetime import timedelta

from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.models import (
    AgentRunTrace,
    IntentResult,
    IntentType,
    PolicyFinding,
    RiskLevel,
    RouteDecision,
    RouteTarget,
    ToolResult,
    ToolStatus,
    utc_now,
)
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent
from support_agent_lab.monitoring.monitor_review_service import run_monitor_review_cycle
from support_agent_lab.scripts.monitor_review_worker import main as worker_main


def test_monitor_review_worker_backfills_missing_monitor_events(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    trace = _completed_trace(run_id="run_backfill_private", user_id="user_private")
    event_store.append_agent_run(trace)

    report = run_monitor_review_cycle(
        settings=Settings(app_tenant_id="demo_tenant"),
        event_store=event_store,
        worker_id="monitor-worker-private",
        record_worker_heartbeat=True,
    )
    events = event_store.list_monitor_events(tenant_id="demo_tenant", run_id=trace.id)
    heartbeats = event_store.list_monitor_review_worker_heartbeats(tenant_id="demo_tenant")
    summary = event_store.summarize_monitor_review_worker_heartbeats(
        tenant_id="demo_tenant",
        stale_after_seconds=180,
    )

    assert report.cycle_status == "success"
    assert report.inspected_count == 1
    assert report.reviewed_count == 1
    assert report.failed_count == 0
    assert len(events) == 1
    assert events[0].run_id == trace.id
    assert events[0].risk_level == RiskLevel.medium
    assert events[0].failure_types == ["TIMEOUT"]
    assert events[0].needs_human_review is True
    assert events[0].timestamp == trace.completed_at
    assert heartbeats[0].worker_id == "monitor-worker-private"
    assert heartbeats[0].last_cycle_status == "success"
    assert heartbeats[0].reviewed_count == 1
    assert summary.status == "active"
    assert summary.last_reviewed_count == 1


def test_monitor_review_worker_does_not_duplicate_existing_reviews(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    trace = _completed_trace(run_id="run_existing_review")
    event_store.append_agent_run(trace)
    first_report = run_monitor_review_cycle(
        settings=Settings(app_tenant_id="demo_tenant"),
        event_store=event_store,
    )
    second_report = run_monitor_review_cycle(
        settings=Settings(app_tenant_id="demo_tenant"),
        event_store=event_store,
    )

    events = event_store.list_monitor_events(tenant_id="demo_tenant", run_id=trace.id)

    assert first_report.reviewed_count == 1
    assert second_report.inspected_count == 0
    assert second_report.reviewed_count == 0
    assert len(events) == 1


def test_monitor_review_event_append_if_absent_is_tenant_scoped(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    trace = _completed_trace(run_id="run_shared_id")
    response = _response_for_trace(trace)
    monitor_event = OnlineMonitorAgent().review(response)

    _first, created_first = event_store.append_monitor_event_if_absent(monitor_event, tenant_id="demo_tenant")
    _duplicate, created_duplicate = event_store.append_monitor_event_if_absent(monitor_event, tenant_id="demo_tenant")
    _other_tenant, created_other_tenant = event_store.append_monitor_event_if_absent(
        monitor_event,
        tenant_id="other_tenant",
    )

    assert created_first is True
    assert created_duplicate is False
    assert created_other_tenant is True
    assert len(event_store.list_monitor_events(tenant_id="demo_tenant", run_id=trace.id)) == 1
    assert len(event_store.list_monitor_events(tenant_id="other_tenant", run_id=trace.id)) == 1


def test_monitor_review_worker_cli_outputs_sanitized_json(tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "events.db"
    event_store = SQLiteEventStore(db_path)
    trace = _completed_trace(run_id="run_cli_private", user_id="user_cli_private")
    event_store.append_agent_run(trace)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_REQUIRE_PRODUCTION", "false")
    monkeypatch.setenv("APP_TENANT_ID", "demo_tenant")

    exit_code = worker_main(
        [
            "--once",
            "--json",
            "--database-url",
            f"sqlite:///{db_path}",
            "--worker-id",
            "monitor-worker-cli-private",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["cycle_status"] == "success"
    assert payload["reviewed_count"] == 1
    assert "run_cli_private" not in captured.out
    assert "user_cli_private" not in captured.out


def test_monitor_review_worker_cli_rejects_non_sqlite_database(capsys):
    exit_code = worker_main(["--once", "--json", "--database-url", "postgresql://example/db"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "monitor review worker requires a sqlite:/// APP_DATABASE_URL" in captured.err


def test_monitor_review_worker_heartbeat_reports_stale_worker(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    event_store.record_monitor_review_worker_heartbeat(
        tenant_id="demo_tenant",
        worker_id="monitor-worker-private",
        status="idle",
        cycle_status="success",
        last_cycle_completed_at=utc_now() - timedelta(minutes=10),
        reviewed_count=2,
        now=utc_now() - timedelta(minutes=10),
    )

    summary = event_store.summarize_monitor_review_worker_heartbeats(
        tenant_id="demo_tenant",
        stale_after_seconds=180,
        now=utc_now(),
    )

    assert summary.status == "stale"
    assert summary.stale_worker_count == 1
    assert summary.last_reviewed_count == 2


def _completed_trace(*, run_id: str, user_id: str = "user_test") -> AgentRunTrace:
    trace = AgentRunTrace(
        id=run_id,
        tenant_id="demo_tenant",
        conversation_id=f"conv_{run_id}",
        user_id=user_id,
    )
    trace.intent = IntentResult(primary=IntentType.order_status, confidence=0.84)
    trace.route = RouteDecision(
        target=RouteTarget.order_agent,
        reason="order status",
        allowed_tools=["shipping.track"],
        needs_human=False,
    )
    trace.tool_results.append(
        ToolResult(
            name="shipping.track",
            status=ToolStatus.failed,
            error_code="TIMEOUT",
            error_message="private timeout detail should not leave worker summary",
        )
    )
    trace.policy_findings.append(
        PolicyFinding(
            code="PII_IN_INPUT",
            risk_level=RiskLevel.low,
            message="private policy detail should not leave worker summary",
        )
    )
    trace.finish("completed")
    return trace


def _response_for_trace(trace: AgentRunTrace):
    from support_agent_lab.monitoring.monitor_review_service import _response_from_trace

    return _response_from_trace(trace)

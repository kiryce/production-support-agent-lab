from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import EventStoreOperationLockConflict, SQLiteEventStore
from support_agent_lab.models import AgentResponse, AgentRunTrace, Message, Role, utc_now
from support_agent_lab.monitoring.monitor import OnlineMonitorAgent


MONITOR_REVIEW_WORKER_LOCK_NAME = "monitor_review_worker"
MONITOR_REVIEW_WORKER_OPERATION = "monitor_review_cycle"


class MonitorReviewWorkerReport(BaseModel):
    schema_version: str = "monitor_review_worker_report.v1"
    cycle_status: str = "success"
    inspected_count: int = 0
    reviewed_count: int = 0
    skipped_existing_count: int = 0
    skipped_unreviewable_count: int = 0
    failed_count: int = 0
    lock_skipped: bool = False
    latest_run_created_at: datetime | None = None
    last_error_type: str | None = None
    warnings: list[str] = Field(default_factory=list)


def run_monitor_review_cycle(
    *,
    settings: Settings,
    event_store: SQLiteEventStore,
    limit: int = 100,
    worker_id: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    record_worker_heartbeat: bool = False,
    lock_ttl_seconds: int | None = None,
) -> MonitorReviewWorkerReport:
    cycle_started_at = utc_now()
    if record_worker_heartbeat and worker_id:
        event_store.record_monitor_review_worker_heartbeat(
            tenant_id=settings.app_tenant_id,
            worker_id=worker_id,
            status="running",
            last_cycle_started_at=cycle_started_at,
        )
    try:
        report = _run_monitor_review_cycle(
            settings=settings,
            event_store=event_store,
            limit=limit,
            worker_id=worker_id,
            created_after=created_after,
            created_before=created_before,
            lock_ttl_seconds=lock_ttl_seconds,
        )
    except Exception as exc:
        if record_worker_heartbeat and worker_id:
            event_store.record_monitor_review_worker_heartbeat(
                tenant_id=settings.app_tenant_id,
                worker_id=worker_id,
                status="failed",
                cycle_status="failed",
                last_error=exc.__class__.__name__,
                last_cycle_started_at=cycle_started_at,
                last_cycle_completed_at=utc_now(),
            )
        raise
    if record_worker_heartbeat and worker_id:
        event_store.record_monitor_review_worker_heartbeat(
            tenant_id=settings.app_tenant_id,
            worker_id=worker_id,
            status="idle",
            cycle_status=report.cycle_status,
            last_error=report.last_error_type,
            last_cycle_started_at=cycle_started_at,
            last_cycle_completed_at=utc_now(),
            inspected_count=report.inspected_count,
            reviewed_count=report.reviewed_count,
            skipped_existing_count=report.skipped_existing_count,
            skipped_unreviewable_count=report.skipped_unreviewable_count,
            failed_count=report.failed_count,
        )
    return report


def summarize_monitor_review_report(report: MonitorReviewWorkerReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "cycle_status": report.cycle_status,
        "inspected_count": report.inspected_count,
        "reviewed_count": report.reviewed_count,
        "skipped_existing_count": report.skipped_existing_count,
        "skipped_unreviewable_count": report.skipped_unreviewable_count,
        "failed_count": report.failed_count,
        "lock_skipped": report.lock_skipped,
        "latest_run_created_at": report.latest_run_created_at.isoformat() if report.latest_run_created_at else None,
        "last_error_type": report.last_error_type,
        "warnings": report.warnings,
    }


def _run_monitor_review_cycle(
    *,
    settings: Settings,
    event_store: SQLiteEventStore,
    limit: int,
    worker_id: str | None,
    created_after: str | None,
    created_before: str | None,
    lock_ttl_seconds: int | None,
) -> MonitorReviewWorkerReport:
    try:
        with event_store.event_store_operation_lock(
            tenant_id=settings.app_tenant_id,
            lock_name=MONITOR_REVIEW_WORKER_LOCK_NAME,
            operation=MONITOR_REVIEW_WORKER_OPERATION,
            owner_id=worker_id,
            ttl_seconds=lock_ttl_seconds or settings.app_event_store_operation_lock_ttl_seconds,
        ):
            return _review_missing_monitor_events(
                settings=settings,
                event_store=event_store,
                limit=limit,
                created_after=created_after,
                created_before=created_before,
            )
    except EventStoreOperationLockConflict:
        return MonitorReviewWorkerReport(cycle_status="skipped_lock", lock_skipped=True)


def _review_missing_monitor_events(
    *,
    settings: Settings,
    event_store: SQLiteEventStore,
    limit: int,
    created_after: str | None,
    created_before: str | None,
) -> MonitorReviewWorkerReport:
    traces = event_store.list_unreviewed_agent_runs(
        tenant_id=settings.app_tenant_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        order="asc",
    )
    monitor = OnlineMonitorAgent()
    report = MonitorReviewWorkerReport()
    for trace in traces:
        report.inspected_count += 1
        report.latest_run_created_at = trace.created_at
        if not _trace_is_reviewable(trace):
            report.skipped_unreviewable_count += 1
            continue
        try:
            response = _response_from_trace(trace)
            monitor_event = monitor.review(response)
            monitor_event.timestamp = trace.completed_at or trace.created_at
            _stored, created = event_store.append_monitor_event_if_absent(
                monitor_event,
                tenant_id=settings.app_tenant_id,
            )
            if created:
                report.reviewed_count += 1
            else:
                report.skipped_existing_count += 1
        except Exception as exc:
            report.failed_count += 1
            report.last_error_type = exc.__class__.__name__
    if report.failed_count:
        report.cycle_status = "partial_failure"
    return report


def _trace_is_reviewable(trace: AgentRunTrace) -> bool:
    return trace.status == "completed" and trace.intent is not None


def _response_from_trace(trace: AgentRunTrace) -> AgentResponse:
    completed_at = trace.completed_at or utc_now()
    message = Message(
        tenant_id=trace.tenant_id,
        conversation_id=trace.conversation_id,
        user_id=trace.user_id,
        role=Role.assistant,
        content="Monitor review worker reconstructed this response from a persisted run trace.",
        created_at=completed_at,
        metadata={"source": "monitor_review_worker"},
    )
    handoff_required = bool(
        (trace.route.needs_human if trace.route else False)
        or any(finding.should_escalate for finding in trace.policy_findings)
    )
    return AgentResponse(
        message=message,
        trace=trace,
        citations=(trace.retrieval.selected_context[:2] if trace.retrieval else []),
        handoff_required=handoff_required,
    )

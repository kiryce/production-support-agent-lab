from __future__ import annotations

import json
import math
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from support_agent_lab.models import (
    AgentFeedback,
    FeedbackReviewEvent,
    AgentRunTrace,
    AlertDeliveryRecord,
    AlertDeliveryStatus,
    EvalGateRecord,
    Message,
    MonitorAlertTriageEvent,
    MonitorEvent,
    ToolResult,
    ToolStatus,
    new_id,
    utc_now,
)
from support_agent_lab.tools.registry import (
    IdempotencyDecision,
    ToolAuditErrorSummary,
    ToolAuditRecord,
    ToolAuditSummary,
    ToolAuditToolSummary,
)


class StoredEvent(BaseModel):
    id: str
    tenant_id: str
    conversation_id: str | None = None
    user_id: str | None = None
    run_id: str | None = None
    event_type: str
    payload: dict[str, Any]
    created_at: str


class AlertDeliveryMetricSummary(BaseModel):
    total_count: int = 0
    due_count: int = 0
    attempt_count_total: int = 0
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    counts_by_severity: dict[str, int] = Field(default_factory=dict)
    oldest_actionable_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_dead_lettered_at: datetime | None = None


class AlertDispatcherHeartbeatRecord(BaseModel):
    tenant_id: str
    worker_id: str
    status: str
    last_seen_at: datetime
    last_cycle_started_at: datetime | None = None
    last_cycle_completed_at: datetime | None = None
    last_cycle_status: str | None = None
    last_error: str | None = None
    cycle_count: int = 0
    enqueued_count: int = 0
    claimed_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    dead_count: int = 0
    created_at: datetime
    updated_at: datetime


class AlertDispatcherHeartbeatSummary(BaseModel):
    status: str
    stale_after_seconds: int
    total_worker_count: int = 0
    active_worker_count: int = 0
    stale_worker_count: int = 0
    last_seen_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class AlertWebhookReceiptRecord(BaseModel):
    tenant_id: str
    delivery_id: str
    alert_key: str
    severity: str
    body_hash: str
    signature_hash: str
    source_hash: str | None = None
    user_agent_hash: str | None = None
    alert_count: int = 0
    sample_event_count: int = 0
    sample_run_count: int = 0
    duplicate_count: int = 0
    first_received_at: datetime
    last_received_at: datetime
    created_at: datetime
    updated_at: datetime


class AlertWebhookReceiptSummary(BaseModel):
    total_count: int = 0
    duplicate_count_total: int = 0
    sent_delivery_count: int = 0
    sent_with_receipt_count: int = 0
    sent_without_receipt_count: int = 0
    recent_sent_pending_receipt_count: int = 0
    receipt_grace_seconds: int = 0
    last_received_at: datetime | None = None
    oldest_unconfirmed_sent_at: datetime | None = None


class FeedbackReasonSummary(BaseModel):
    reason: str
    count: int


class FeedbackSummary(BaseModel):
    total_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    negative_rate: float = 0.0
    counts_by_reason: list[FeedbackReasonSummary] = Field(default_factory=list)
    window_start: str | None = None
    window_end: str | None = None


class FeedbackReviewQueueItem(BaseModel):
    feedback_id: str
    run_id: str
    conversation_id: str
    user_id: str
    rating: str
    reasons: list[str] = Field(default_factory=list)
    source: str
    feedback_created_at: datetime
    current_status: str
    review_count: int = 0
    latest_review_id: str | None = None
    latest_review_at: datetime | None = None
    assignee_user_id: str | None = None
    is_unresolved: bool = True
    is_unassigned: bool = True
    is_stale: bool = False
    age_hours: float = 0.0


class FeedbackReviewQueueSummary(BaseModel):
    total_count: int = 0
    summary_source_count: int = 0
    summary_truncated: bool = False
    reviewed_count: int = 0
    unreviewed_count: int = 0
    unresolved_count: int = 0
    unassigned_unresolved_count: int = 0
    stale_unresolved_count: int = 0
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    oldest_unresolved_feedback_at: datetime | None = None
    newest_review_at: datetime | None = None


class FeedbackReviewQueueResponse(BaseModel):
    schema_version: str = "feedback_review_queue.v1"
    generated_at: datetime = Field(default_factory=utc_now)
    stale_after_hours: int = 48
    limit: int = 100
    order: str = "desc"
    summary: FeedbackReviewQueueSummary = Field(default_factory=FeedbackReviewQueueSummary)
    items: list[FeedbackReviewQueueItem] = Field(default_factory=list)


class SQLiteBackupReport(BaseModel):
    source_path: str
    backup_path: str
    size_bytes: int
    page_count: int
    started_at: datetime
    completed_at: datetime
    verified: bool
    verification_detail: str
    backup_token: str | None = None


class SQLiteRestoreDrillReport(BaseModel):
    backup_path: str
    restore_path: str
    restore_path_retained: bool
    size_bytes: int
    page_count: int
    started_at: datetime
    completed_at: datetime
    verified: bool
    verification_detail: str
    health_check_passed: bool
    table_counts: dict[str, int] = Field(default_factory=dict)
    high_water_mark: dict[str, dict[str, Any]] = Field(default_factory=dict)
    restore_drill_token: str | None = None


class RetentionTableReport(BaseModel):
    table_name: str
    cutoff_at: datetime | None = None
    candidate_count: int = 0
    deleted_count: int = 0
    action: str
    reason: str = ""


class EventStoreRetentionReport(BaseModel):
    tenant_id: str
    dry_run: bool
    include_events: bool
    vacuum_requested: bool
    vacuum_performed: bool
    started_at: datetime
    completed_at: datetime
    tables: list[RetentionTableReport]
    total_candidates: int
    total_deleted: int
    preview_token: str | None = None


class EventStoreOperationRecord(BaseModel):
    id: str
    tenant_id: str
    actor_user_id: str
    operation: str
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class OperationsAutomationExecutionRecord(BaseModel):
    id: str
    tenant_id: str
    actor_user_id: str
    action_id: str
    action_kind: str
    title: str
    status: str
    safe_to_auto_execute: bool
    command_method: str
    command_path: str
    command_query: dict[str, Any] = Field(default_factory=dict)
    command_body_keys: list[str] = Field(default_factory=list)
    command_body_hash: str | None = None
    command_fingerprint: str
    result_summary: str
    error_detail: str | None = None
    source: str = "api"
    created_at: str


class EventStoreOperationLock(BaseModel):
    tenant_id: str
    lock_name: str
    owner_id: str
    operation: str
    acquired_at: str
    expires_at: str


EVAL_GATE_EVENT_TYPE = "eval.gate.completed"
ALERT_DELIVERY_ENQUEUED_EVENT_TYPE = "monitor.alert.delivery.enqueued"
ALERT_DELIVERY_ATTEMPTED_EVENT_TYPE = "monitor.alert.delivery.attempted"
ALERT_DELIVERY_REQUEUED_EVENT_TYPE = "monitor.alert.delivery.requeued"
ALERT_DELIVERY_CLOSED_EVENT_TYPE = "monitor.alert.delivery.closed"
ALERT_WEBHOOK_RECEIVED_EVENT_TYPE = "monitor.alert.webhook.received"
FEEDBACK_EVENT_TYPE = "agent.response.feedback"
FEEDBACK_REVIEW_EVENT_TYPE = "agent.response.feedback.reviewed"
MEMORY_REPLAY_EVENT_TYPES = ("message.user", "message.assistant", "agent.run.completed")
SQLITE_REQUIRED_TABLES = (
    "events",
    "tool_idempotency",
    "tool_audit_records",
    "api_request_nonces",
    "api_rate_limits",
    "alert_delivery_outbox",
    "alert_dispatcher_heartbeats",
    "alert_webhook_receipts",
    "event_store_operations",
    "operations_automation_executions",
    "event_store_operation_locks",
)
SQLITE_EVENTS_REQUIRED_COLUMNS = {
    "id",
    "tenant_id",
    "conversation_id",
    "user_id",
    "run_id",
    "event_type",
    "payload_json",
    "created_at",
}
SQLITE_EVENT_STORE_OPERATIONS_REQUIRED_COLUMNS = {
    "id",
    "tenant_id",
    "actor_user_id",
    "operation",
    "status",
    "summary_json",
    "created_at",
}
SQLITE_OPERATIONS_AUTOMATION_EXECUTIONS_REQUIRED_COLUMNS = {
    "id",
    "tenant_id",
    "actor_user_id",
    "action_id",
    "action_kind",
    "title",
    "status",
    "safe_to_auto_execute",
    "command_method",
    "command_path",
    "command_query_json",
    "command_body_keys_json",
    "command_body_hash",
    "command_fingerprint",
    "result_summary",
    "error_detail",
    "source",
    "created_at",
}
SQLITE_EVENT_STORE_OPERATION_LOCKS_REQUIRED_COLUMNS = {
    "tenant_id",
    "lock_name",
    "owner_id",
    "operation",
    "acquired_at",
    "expires_at",
}
SQLITE_ALERT_DISPATCHER_HEARTBEATS_REQUIRED_COLUMNS = {
    "tenant_id",
    "worker_id",
    "status",
    "last_seen_at",
    "last_cycle_started_at",
    "last_cycle_completed_at",
    "last_cycle_status",
    "last_error",
    "cycle_count",
    "enqueued_count",
    "claimed_count",
    "sent_count",
    "failed_count",
    "dead_count",
    "created_at",
    "updated_at",
}
SQLITE_ALERT_WEBHOOK_RECEIPTS_REQUIRED_COLUMNS = {
    "tenant_id",
    "delivery_id",
    "alert_key",
    "severity",
    "body_hash",
    "signature_hash",
    "source_hash",
    "user_agent_hash",
    "alert_count",
    "sample_event_count",
    "sample_run_count",
    "duplicate_count",
    "first_received_at",
    "last_received_at",
    "created_at",
    "updated_at",
}


class AlertDeliveryLockLostError(RuntimeError):
    """Raised when a dispatcher tries to complete a delivery it no longer owns."""


class EventStoreOperationLockConflict(RuntimeError):
    """Raised when another maintenance operation holds the event-store lock."""

    def __init__(self, active_lock: EventStoreOperationLock) -> None:
        self.active_lock = active_lock
        super().__init__(
            f"Event-store maintenance lock is held for {active_lock.operation} until {active_lock.expires_at}"
        )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _rounded_average(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


class SQLiteEventStore:
    """Append-only local event store for learning persistence boundaries.

    This is intentionally small and dependency-free. It teaches the shape of a
    production event log without forcing learners to run Postgres on day one.
    """

    SQLITE_BUSY_TIMEOUT_MS = 5000
    SQLITE_JOURNAL_MODE = "WAL"
    SQLITE_SYNCHRONOUS = "NORMAL"

    def __init__(self, path: str | Path, tool_idempotency_lease_seconds: int = 300) -> None:
        self.path = Path(path)
        self.tool_idempotency_lease_seconds = tool_idempotency_lease_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._configure_database_file()
        self._init_schema()

    @classmethod
    def from_url(cls, database_url: str) -> "SQLiteEventStore | None":
        if not database_url.startswith("sqlite:///"):
            return None
        return cls(database_url.removeprefix("sqlite:///"))

    def append_message(self, message: Message) -> StoredEvent:
        return self.append(
            tenant_id=message.tenant_id,
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            event_type=f"message.{message.role.value}",
            payload=message.model_dump(mode="json"),
        )

    def append_agent_run(self, trace: AgentRunTrace) -> StoredEvent:
        return self.append(
            tenant_id=trace.tenant_id,
            conversation_id=trace.conversation_id,
            user_id=trace.user_id,
            run_id=trace.id,
            event_type="agent.run.completed",
            payload=trace.model_dump(mode="json"),
        )

    def append_monitor_event(self, event: MonitorEvent, tenant_id: str = "demo_tenant") -> StoredEvent:
        return self.append(
            tenant_id=tenant_id,
            conversation_id=event.conversation_id,
            user_id=None,
            run_id=event.run_id,
            event_type="monitor.reviewed",
            payload=event.model_dump(mode="json"),
        )

    def append_monitor_alert_triage(
        self,
        event: MonitorAlertTriageEvent,
        tenant_id: str = "demo_tenant",
    ) -> StoredEvent:
        return self.append(
            tenant_id=tenant_id,
            event_type="monitor.alert.triaged",
            user_id=event.actor_user_id,
            payload=event.model_dump(mode="json"),
        )

    def append_eval_gate_record(
        self,
        record: EvalGateRecord,
        tenant_id: str = "demo_tenant",
    ) -> StoredEvent:
        return self.append(
            tenant_id=tenant_id,
            event_type=EVAL_GATE_EVENT_TYPE,
            user_id=record.actor_user_id,
            run_id=record.run_id,
            payload=record.model_dump(mode="json"),
        )

    def append_agent_feedback(self, feedback: AgentFeedback) -> StoredEvent:
        return self.append(
            tenant_id=feedback.tenant_id,
            conversation_id=feedback.conversation_id,
            user_id=feedback.user_id,
            run_id=feedback.run_id,
            event_type=FEEDBACK_EVENT_TYPE,
            payload=feedback.model_dump(mode="json"),
        )

    def append_feedback_review(self, review: FeedbackReviewEvent) -> StoredEvent:
        return self.append(
            tenant_id=review.tenant_id,
            conversation_id=review.conversation_id,
            user_id=review.actor_user_id,
            run_id=review.run_id,
            event_type=FEEDBACK_REVIEW_EVENT_TYPE,
            payload=review.model_dump(mode="json"),
        )

    def list_feedback_review_events(
        self,
        *,
        feedback_id: str,
        tenant_id: str | None = None,
        limit: int = 100,
        order: str = "asc",
    ) -> list[FeedbackReviewEvent]:
        sql = (
            "select payload_json from events where event_type = ? "
            "and json_extract(payload_json, '$.feedback_id') = ?"
        )
        params: list[Any] = [FEEDBACK_REVIEW_EVENT_TYPE, feedback_id]
        if tenant_id:
            sql += " and tenant_id = ?"
            params.append(tenant_id)
        direction = "desc" if order == "desc" else "asc"
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        with self._connect() as conn:
            rows = conn.execute(sql, [*params, limit]).fetchall()
        return [FeedbackReviewEvent.model_validate(json.loads(row["payload_json"])) for row in rows]

    def count_feedback_review_events(
        self,
        *,
        feedback_id: str,
        tenant_id: str | None = None,
    ) -> int:
        sql = (
            "select count(*) as total_count from events where event_type = ? "
            "and json_extract(payload_json, '$.feedback_id') = ?"
        )
        params: list[Any] = [FEEDBACK_REVIEW_EVENT_TYPE, feedback_id]
        if tenant_id:
            sql += " and tenant_id = ?"
            params.append(tenant_id)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total_count"] or 0)

    def list_agent_feedback(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        rating: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> list[AgentFeedback]:
        sql = f"select payload_json from events where event_type = ?"
        params, clauses = self._feedback_filter_params(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            created_after=created_after,
            created_before=created_before,
        )
        params.insert(0, FEEDBACK_EVENT_TYPE)
        if clauses:
            sql += " and " + " and ".join(clauses)
        direction = "asc" if order == "asc" else "desc"
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        with self._connect() as conn:
            rows = conn.execute(sql, [*params, limit]).fetchall()
        return [AgentFeedback.model_validate(json.loads(row["payload_json"])) for row in rows]

    def get_agent_feedback(
        self,
        feedback_id: str,
        *,
        tenant_id: str | None = None,
    ) -> AgentFeedback | None:
        sql = "select payload_json from events where event_type = ? and json_extract(payload_json, '$.id') = ?"
        params: list[Any] = [FEEDBACK_EVENT_TYPE, feedback_id]
        if tenant_id:
            sql += " and tenant_id = ?"
            params.append(tenant_id)
        sql += " order by created_at desc, rowid desc limit 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return AgentFeedback.model_validate(json.loads(row["payload_json"]))

    def summarize_agent_feedback(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        rating: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> FeedbackSummary:
        params, clauses = self._feedback_filter_params(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            created_after=created_after,
            created_before=created_before,
        )
        where_sql = f"where event_type = ?"
        query_params: list[Any] = [FEEDBACK_EVENT_TYPE, *params]
        if clauses:
            where_sql += " and " + " and ".join(clauses)
        totals_sql = f"""
            select
              count(*) as total_count,
              coalesce(sum(case when json_extract(payload_json, '$.rating') = 'positive' then 1 else 0 end), 0)
                as positive_count,
              coalesce(sum(case when json_extract(payload_json, '$.rating') = 'negative' then 1 else 0 end), 0)
                as negative_count,
              min(created_at) as window_start,
              max(created_at) as window_end
            from events
            {where_sql}
        """
        reasons_sql = f"""
            select reason.value as reason, count(*) as count
            from events, json_each(events.payload_json, '$.reasons') as reason
            {where_sql}
            group by reason.value
            order by count desc, reason asc
            limit 10
        """
        with self._connect() as conn:
            totals = conn.execute(totals_sql, query_params).fetchone()
            reason_rows = conn.execute(reasons_sql, query_params).fetchall()
        total_count = int(totals["total_count"] or 0)
        positive_count = int(totals["positive_count"] or 0)
        negative_count = int(totals["negative_count"] or 0)
        return FeedbackSummary(
            total_count=total_count,
            positive_count=positive_count,
            negative_count=negative_count,
            negative_rate=_rate(negative_count, total_count),
            counts_by_reason=[
                FeedbackReasonSummary(reason=str(row["reason"]), count=int(row["count"]))
                for row in reason_rows
            ],
            window_start=totals["window_start"],
            window_end=totals["window_end"],
        )

    def count_agent_feedback(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        rating: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> int:
        params, clauses = self._feedback_filter_params(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            created_after=created_after,
            created_before=created_before,
        )
        sql = "select count(*) as total_count from events where event_type = ?"
        query_params: list[Any] = [FEEDBACK_EVENT_TYPE, *params]
        if clauses:
            sql += " and " + " and ".join(clauses)
        with self._connect() as conn:
            row = conn.execute(sql, query_params).fetchone()
        return int(row["total_count"] or 0)

    def feedback_review_queue(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        rating: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 100,
        order: str = "desc",
        stale_after_hours: int = 48,
    ) -> FeedbackReviewQueueResponse:
        filtered_count = self.count_agent_feedback(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            created_after=created_after,
            created_before=created_before,
        )
        summary_limit = 5000
        summary_source_count = min(filtered_count, summary_limit)
        feedback_for_summary = self.list_agent_feedback(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            created_after=created_after,
            created_before=created_before,
            limit=max(summary_source_count, limit),
            order=order,
        )
        if not feedback_for_summary:
            return FeedbackReviewQueueResponse(
                stale_after_hours=stale_after_hours,
                limit=limit,
                order=order,
                summary=FeedbackReviewQueueSummary(
                    total_count=filtered_count,
                    summary_source_count=0,
                    summary_truncated=False,
                ),
            )

        feedback_ids = [feedback.id for feedback in feedback_for_summary]
        placeholders = ", ".join("?" for _ in feedback_ids)
        sql = f"""
            select payload_json
            from events
            where event_type = ?
              and json_extract(payload_json, '$.feedback_id') in ({placeholders})
        """
        params: list[Any] = [FEEDBACK_REVIEW_EVENT_TYPE, *feedback_ids]
        if tenant_id:
            sql += " and tenant_id = ?"
            params.append(tenant_id)
        sql += " order by created_at asc, rowid asc"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        reviews_by_feedback: dict[tuple[str, str], list[FeedbackReviewEvent]] = {
            (feedback.tenant_id, feedback.id): [] for feedback in feedback_for_summary
        }
        for row in rows:
            review = FeedbackReviewEvent.model_validate(json.loads(row["payload_json"]))
            review_key = (review.tenant_id, review.feedback_id)
            if review_key in reviews_by_feedback:
                reviews_by_feedback[review_key].append(review)

        generated_at = utc_now()
        stale_cutoff = generated_at - timedelta(hours=stale_after_hours)
        counts_by_status: dict[str, int] = {}
        oldest_unresolved_feedback_at: datetime | None = None
        newest_review_at: datetime | None = None
        reviewed_count = 0
        unresolved_count = 0
        unassigned_unresolved_count = 0
        stale_unresolved_count = 0

        all_items: list[FeedbackReviewQueueItem] = []
        for feedback in feedback_for_summary:
            reviews = reviews_by_feedback[(feedback.tenant_id, feedback.id)]
            latest = reviews[-1] if reviews else None
            status = latest.status if latest else "unreviewed"
            is_unresolved = status in {"unreviewed", "acknowledged", "investigating"}
            is_unassigned = is_unresolved and not (latest and latest.assignee_user_id)
            is_stale = is_unresolved and feedback.created_at <= stale_cutoff
            age_hours = max((generated_at - feedback.created_at).total_seconds() / 3600, 0)
            counts_by_status[status] = counts_by_status.get(status, 0) + 1
            if latest:
                reviewed_count += 1
                if newest_review_at is None or latest.created_at > newest_review_at:
                    newest_review_at = latest.created_at
            if is_unresolved:
                unresolved_count += 1
                if oldest_unresolved_feedback_at is None or feedback.created_at < oldest_unresolved_feedback_at:
                    oldest_unresolved_feedback_at = feedback.created_at
            if is_unassigned:
                unassigned_unresolved_count += 1
            if is_stale:
                stale_unresolved_count += 1
            all_items.append(
                FeedbackReviewQueueItem(
                    feedback_id=feedback.id,
                    run_id=feedback.run_id,
                    conversation_id=feedback.conversation_id,
                    user_id=feedback.user_id,
                    rating=feedback.rating.value,
                    reasons=feedback.reasons,
                    source=feedback.source,
                    feedback_created_at=feedback.created_at,
                    current_status=status,
                    review_count=len(reviews),
                    latest_review_id=latest.id if latest else None,
                    latest_review_at=latest.created_at if latest else None,
                    assignee_user_id=latest.assignee_user_id if latest else None,
                    is_unresolved=is_unresolved,
                    is_unassigned=is_unassigned,
                    is_stale=is_stale,
                    age_hours=round(age_hours, 2),
                )
            )

        summary = FeedbackReviewQueueSummary(
            total_count=filtered_count,
            summary_source_count=len(all_items),
            summary_truncated=filtered_count > len(all_items),
            reviewed_count=reviewed_count,
            unreviewed_count=counts_by_status.get("unreviewed", 0),
            unresolved_count=unresolved_count,
            unassigned_unresolved_count=unassigned_unresolved_count,
            stale_unresolved_count=stale_unresolved_count,
            counts_by_status=counts_by_status,
            oldest_unresolved_feedback_at=oldest_unresolved_feedback_at,
            newest_review_at=newest_review_at,
        )
        return FeedbackReviewQueueResponse(
            generated_at=generated_at,
            stale_after_hours=stale_after_hours,
            limit=limit,
            order=order,
            summary=summary,
            items=all_items[:limit],
        )

    def _feedback_filter_params(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        rating: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> tuple[list[Any], list[str]]:
        params: list[Any] = []
        clauses: list[str] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if rating:
            clauses.append("json_extract(payload_json, '$.rating') = ?")
            params.append(rating)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        return params, clauses

    def enqueue_alert_delivery(self, record: AlertDeliveryRecord) -> tuple[AlertDeliveryRecord, bool]:
        created = False
        now = utc_now().isoformat()
        record = record.model_copy(update={"created_at": record.created_at, "updated_at": record.updated_at})
        with self._connect() as conn:
            conn.execute("begin immediate")
            try:
                conn.execute(
                    """
                    insert into alert_delivery_outbox (
                      id, tenant_id, alert_key, severity, channel, destination_hash, status,
                      alert_first_seen_at, alert_last_seen_at, alert_count, reason,
                      sample_event_ids_json, sample_run_ids_json, payload_hash,
                      attempt_count, next_attempt_at, last_attempt_at, delivered_at,
                      dead_lettered_at, locked_until, locked_by, operator_action,
                      operator_action_at, operator_action_by, operator_action_note,
                      response_status_code, last_error, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.tenant_id,
                        record.alert_key,
                        record.severity,
                        record.channel,
                        record.destination_hash,
                        record.status.value,
                        record.alert_first_seen_at.isoformat(),
                        record.alert_last_seen_at.isoformat(),
                        record.alert_count,
                        record.reason,
                        json.dumps(record.sample_event_ids, ensure_ascii=False, sort_keys=True),
                        json.dumps(record.sample_run_ids, ensure_ascii=False, sort_keys=True),
                        record.payload_hash,
                        record.attempt_count,
                        record.next_attempt_at.isoformat() if record.next_attempt_at else None,
                        record.last_attempt_at.isoformat() if record.last_attempt_at else None,
                        record.delivered_at.isoformat() if record.delivered_at else None,
                        record.dead_lettered_at.isoformat() if record.dead_lettered_at else None,
                        record.locked_until.isoformat() if record.locked_until else None,
                        record.locked_by,
                        record.operator_action,
                        record.operator_action_at.isoformat() if record.operator_action_at else None,
                        record.operator_action_by,
                        record.operator_action_note,
                        record.response_status_code,
                        record.last_error,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
                created = True
            except sqlite3.IntegrityError:
                pass
            row = conn.execute(
                """
                select *
                from alert_delivery_outbox
                where tenant_id = ?
                  and alert_key = ?
                  and alert_last_seen_at = ?
                  and destination_hash = ?
                limit 1
                """,
                (
                    record.tenant_id,
                    record.alert_key,
                    record.alert_last_seen_at.isoformat(),
                    record.destination_hash,
                ),
            ).fetchone()
        persisted = self._alert_delivery_from_row(row)
        if created:
            self.append(
                tenant_id=persisted.tenant_id,
                event_type=ALERT_DELIVERY_ENQUEUED_EVENT_TYPE,
                payload=persisted.model_dump(mode="json"),
            )
        return persisted, created

    def claim_alert_delivery_records(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
        due_at: datetime | None = None,
    ) -> list[AlertDeliveryRecord]:
        now = due_at or utc_now()
        locked_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as conn:
            conn.execute("begin immediate")
            rows = conn.execute(
                """
                select *
                from alert_delivery_outbox
                where tenant_id = ?
                  and attempt_count < ?
                  and (
                    status in ('pending', 'failed')
                    or (status = 'in_progress' and locked_until is not null and locked_until <= ?)
                  )
                  and (next_attempt_at is null or next_attempt_at <= ?)
                order by created_at asc, rowid asc
                limit ?
                """,
                (tenant_id, max_attempts, now.isoformat(), now.isoformat(), limit),
            ).fetchall()
            delivery_ids = [row["id"] for row in rows]
            if delivery_ids:
                placeholders = ", ".join("?" for _ in delivery_ids)
                conn.execute(
                    f"""
                    update alert_delivery_outbox
                    set status = ?,
                        locked_by = ?,
                        locked_until = ?,
                        updated_at = ?
                    where id in ({placeholders})
                    """,
                    [
                        AlertDeliveryStatus.in_progress.value,
                        worker_id,
                        locked_until.isoformat(),
                        now.isoformat(),
                        *delivery_ids,
                    ],
                )
                claimed_rows = conn.execute(
                    f"select * from alert_delivery_outbox where id in ({placeholders}) order by created_at asc, rowid asc",
                    delivery_ids,
                ).fetchall()
            else:
                claimed_rows = []
        return [self._alert_delivery_from_row(row) for row in claimed_rows]

    def refresh_alert_delivery_lock(
        self,
        delivery_id: str,
        *,
        tenant_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AlertDeliveryRecord:
        effective_now = now or utc_now()
        locked_until = effective_now + timedelta(seconds=lease_seconds)
        with self._connect() as conn:
            conn.execute("begin immediate")
            updated = conn.execute(
                """
                update alert_delivery_outbox
                set locked_until = ?,
                    updated_at = ?
                where id = ?
                  and tenant_id = ?
                  and status = ?
                  and locked_by = ?
                """,
                (
                    locked_until.isoformat(),
                    effective_now.isoformat(),
                    delivery_id,
                    tenant_id,
                    AlertDeliveryStatus.in_progress.value,
                    worker_id,
                ),
            )
            if updated.rowcount != 1:
                exists = conn.execute(
                    "select 1 from alert_delivery_outbox where id = ? and tenant_id = ?",
                    (delivery_id, tenant_id),
                ).fetchone()
                if exists is None:
                    raise KeyError(f"Alert delivery not found: {delivery_id}")
                raise AlertDeliveryLockLostError(f"Alert delivery lock is not held by worker: {delivery_id}")
            refreshed = conn.execute(
                "select * from alert_delivery_outbox where id = ? and tenant_id = ?",
                (delivery_id, tenant_id),
            ).fetchone()
        return self._alert_delivery_from_row(refreshed)

    def list_alert_delivery_records(
        self,
        *,
        tenant_id: str | None = None,
        alert_key: str | None = None,
        destination_hash: str | None = None,
        statuses: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        max_attempts: int | None = None,
        due_before: str | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> list[AlertDeliveryRecord]:
        sql = "select * from alert_delivery_outbox"
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if alert_key:
            clauses.append("alert_key = ?")
            params.append(alert_key)
        if destination_hash:
            clauses.append("destination_hash = ?")
            params.append(destination_hash)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status in ({placeholders})")
            params.extend(statuses)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        if max_attempts is not None:
            clauses.append("attempt_count < ?")
            params.append(max_attempts)
        if due_before:
            clauses.append("(next_attempt_at is null or next_attempt_at <= ?)")
            params.append(due_before)
        if clauses:
            sql += " where " + " and ".join(clauses)
        direction = "asc" if order == "asc" else "desc"
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._alert_delivery_from_row(row) for row in rows]

    def list_alert_delivery_receipt_gaps(
        self,
        *,
        tenant_id: str,
        receipt_grace_seconds: int = 0,
        now: datetime | None = None,
        limit: int = 100,
        order: str = "asc",
    ) -> list[AlertDeliveryRecord]:
        effective_now = now or utc_now()
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        grace_seconds = max(0, receipt_grace_seconds)
        receipt_cutoff = effective_now - timedelta(seconds=grace_seconds)
        direction = "asc" if order == "asc" else "desc"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select deliveries.*
                from alert_delivery_outbox deliveries
                left join alert_webhook_receipts receipts
                  on receipts.tenant_id = deliveries.tenant_id
                 and receipts.delivery_id = deliveries.id
                where deliveries.tenant_id = ?
                  and deliveries.status = ?
                  and receipts.delivery_id is null
                  and coalesce(deliveries.delivered_at, deliveries.last_attempt_at, deliveries.updated_at) <= ?
                order by
                  coalesce(deliveries.delivered_at, deliveries.last_attempt_at, deliveries.updated_at) {direction},
                  deliveries.rowid {direction}
                limit ?
                """,
                (
                    tenant_id,
                    AlertDeliveryStatus.sent.value,
                    receipt_cutoff.isoformat(),
                    limit,
                ),
            ).fetchall()
        return [self._alert_delivery_from_row(row) for row in rows]

    def summarize_alert_delivery_records(self, *, tenant_id: str) -> AlertDeliveryMetricSummary:
        now = utc_now().isoformat()
        with self._connect() as conn:
            status_rows = conn.execute(
                """
                select status, count(*) as count
                from alert_delivery_outbox
                where tenant_id = ?
                group by status
                """,
                (tenant_id,),
            ).fetchall()
            severity_rows = conn.execute(
                """
                select severity, count(*) as count
                from alert_delivery_outbox
                where tenant_id = ?
                group by severity
                """,
                (tenant_id,),
            ).fetchall()
            row = conn.execute(
                """
                select
                  count(*) as total_count,
                  coalesce(sum(
                    case
                      when status in ('pending', 'failed')
                       and (next_attempt_at is null or next_attempt_at <= ?)
                      then 1
                      when status = 'in_progress'
                       and locked_until is not null
                       and locked_until <= ?
                      then 1 else 0
                    end
                  ), 0) as due_count,
                  coalesce(sum(attempt_count), 0) as attempt_count_total,
                  min(case when status in ('pending', 'in_progress', 'failed') then created_at end)
                    as oldest_actionable_at,
                  min(case when status in ('pending', 'in_progress', 'failed')
                            and next_attempt_at is not null then next_attempt_at end)
                    as next_attempt_at,
                  max(last_attempt_at) as last_attempt_at,
                  max(delivered_at) as last_success_at,
                  max(dead_lettered_at) as last_dead_lettered_at
                from alert_delivery_outbox
                where tenant_id = ?
                """,
                (now, now, tenant_id),
            ).fetchone()
        return AlertDeliveryMetricSummary(
            total_count=int(row["total_count"] or 0),
            due_count=int(row["due_count"] or 0),
            attempt_count_total=int(row["attempt_count_total"] or 0),
            counts_by_status={str(item["status"]): int(item["count"]) for item in status_rows},
            counts_by_severity={str(item["severity"]): int(item["count"]) for item in severity_rows},
            oldest_actionable_at=_parse_optional_datetime(row["oldest_actionable_at"]),
            next_attempt_at=_parse_optional_datetime(row["next_attempt_at"]),
            last_attempt_at=_parse_optional_datetime(row["last_attempt_at"]),
            last_success_at=_parse_optional_datetime(row["last_success_at"]),
            last_dead_lettered_at=_parse_optional_datetime(row["last_dead_lettered_at"]),
        )

    def record_alert_dispatcher_heartbeat(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        status: str,
        cycle_status: str | None = None,
        last_error: str | None = None,
        last_cycle_started_at: datetime | None = None,
        last_cycle_completed_at: datetime | None = None,
        enqueued_count: int = 0,
        claimed_count: int = 0,
        sent_count: int = 0,
        failed_count: int = 0,
        dead_count: int = 0,
        now: datetime | None = None,
    ) -> AlertDispatcherHeartbeatRecord:
        effective_now = now or utc_now()
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        cycle_increment = 1 if last_cycle_completed_at is not None or cycle_status is not None else 0
        sanitized_error = last_error[:500] if last_error else None
        with self._connect() as conn:
            conn.execute(
                """
                insert into alert_dispatcher_heartbeats (
                  tenant_id, worker_id, status, last_seen_at,
                  last_cycle_started_at, last_cycle_completed_at, last_cycle_status, last_error,
                  cycle_count, enqueued_count, claimed_count, sent_count, failed_count, dead_count,
                  created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(tenant_id, worker_id) do update set
                  status = excluded.status,
                  last_seen_at = excluded.last_seen_at,
                  last_cycle_started_at = coalesce(excluded.last_cycle_started_at, alert_dispatcher_heartbeats.last_cycle_started_at),
                  last_cycle_completed_at = coalesce(excluded.last_cycle_completed_at, alert_dispatcher_heartbeats.last_cycle_completed_at),
                  last_cycle_status = coalesce(excluded.last_cycle_status, alert_dispatcher_heartbeats.last_cycle_status),
                  last_error = excluded.last_error,
                  cycle_count = alert_dispatcher_heartbeats.cycle_count + ?,
                  enqueued_count = excluded.enqueued_count,
                  claimed_count = excluded.claimed_count,
                  sent_count = excluded.sent_count,
                  failed_count = excluded.failed_count,
                  dead_count = excluded.dead_count,
                  updated_at = excluded.updated_at
                """,
                (
                    tenant_id,
                    worker_id,
                    status,
                    effective_now.isoformat(),
                    last_cycle_started_at.isoformat() if last_cycle_started_at else None,
                    last_cycle_completed_at.isoformat() if last_cycle_completed_at else None,
                    cycle_status,
                    sanitized_error,
                    cycle_increment,
                    enqueued_count,
                    claimed_count,
                    sent_count,
                    failed_count,
                    dead_count,
                    effective_now.isoformat(),
                    effective_now.isoformat(),
                    cycle_increment,
                ),
            )
            row = conn.execute(
                """
                select *
                from alert_dispatcher_heartbeats
                where tenant_id = ? and worker_id = ?
                """,
                (tenant_id, worker_id),
            ).fetchone()
        return self._alert_dispatcher_heartbeat_from_row(row)

    def list_alert_dispatcher_heartbeats(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
        order: str = "desc",
    ) -> list[AlertDispatcherHeartbeatRecord]:
        direction = "asc" if order == "asc" else "desc"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select *
                from alert_dispatcher_heartbeats
                where tenant_id = ?
                order by last_seen_at {direction}, rowid {direction}
                limit ?
                """,
                (tenant_id, limit),
            ).fetchall()
        return [self._alert_dispatcher_heartbeat_from_row(row) for row in rows]

    def summarize_alert_dispatcher_heartbeats(
        self,
        *,
        tenant_id: str,
        stale_after_seconds: int,
        now: datetime | None = None,
    ) -> AlertDispatcherHeartbeatSummary:
        effective_now = now or utc_now()
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        stale_cutoff = effective_now - timedelta(seconds=max(1, stale_after_seconds))
        records = self.list_alert_dispatcher_heartbeats(tenant_id=tenant_id, limit=1000)
        active = [record for record in records if record.last_seen_at >= stale_cutoff]
        stale = [record for record in records if record.last_seen_at < stale_cutoff]
        if not records:
            status = "missing"
        elif active:
            status = "active"
        else:
            status = "stale"
        success_times = [
            record.last_cycle_completed_at
            for record in records
            if record.last_cycle_status == "success" and record.last_cycle_completed_at is not None
        ]
        errors = [record.last_error for record in records if record.last_error]
        return AlertDispatcherHeartbeatSummary(
            status=status,
            stale_after_seconds=stale_after_seconds,
            total_worker_count=len(records),
            active_worker_count=len(active),
            stale_worker_count=len(stale),
            last_seen_at=max((record.last_seen_at for record in records), default=None),
            last_success_at=max(success_times) if success_times else None,
            last_error=errors[0] if errors else None,
        )

    def record_alert_webhook_receipt(
        self,
        *,
        tenant_id: str,
        delivery_id: str,
        alert_key: str,
        severity: str,
        body_hash: str,
        signature_hash: str,
        source_hash: str | None = None,
        user_agent_hash: str | None = None,
        alert_count: int = 0,
        sample_event_count: int = 0,
        sample_run_count: int = 0,
        now: datetime | None = None,
    ) -> tuple[AlertWebhookReceiptRecord, bool]:
        effective_now = now or utc_now()
        created = False
        with self._connect() as conn:
            conn.execute("begin immediate")
            existing = conn.execute(
                """
                select body_hash
                from alert_webhook_receipts
                where tenant_id = ? and delivery_id = ?
                """,
                (tenant_id, delivery_id),
            ).fetchone()
            if existing and existing["body_hash"] != body_hash:
                raise ValueError("Alert webhook delivery id was reused with a different payload")
            if existing is None:
                conn.execute(
                    """
                    insert into alert_webhook_receipts (
                      tenant_id, delivery_id, alert_key, severity, body_hash, signature_hash,
                      source_hash, user_agent_hash, alert_count, sample_event_count, sample_run_count,
                      duplicate_count, first_received_at, last_received_at, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        delivery_id,
                        alert_key,
                        severity,
                        body_hash,
                        signature_hash,
                        source_hash,
                        user_agent_hash,
                        alert_count,
                        sample_event_count,
                        sample_run_count,
                        effective_now.isoformat(),
                        effective_now.isoformat(),
                        effective_now.isoformat(),
                        effective_now.isoformat(),
                    ),
                )
                created = True
            else:
                conn.execute(
                    """
                    update alert_webhook_receipts
                    set signature_hash = ?,
                        source_hash = coalesce(?, source_hash),
                        user_agent_hash = coalesce(?, user_agent_hash),
                        duplicate_count = duplicate_count + 1,
                        last_received_at = ?,
                        updated_at = ?
                    where tenant_id = ? and delivery_id = ?
                    """,
                    (
                        signature_hash,
                        source_hash,
                        user_agent_hash,
                        effective_now.isoformat(),
                        effective_now.isoformat(),
                        tenant_id,
                        delivery_id,
                    ),
                )
            row = conn.execute(
                """
                select *
                from alert_webhook_receipts
                where tenant_id = ? and delivery_id = ?
                """,
                (tenant_id, delivery_id),
            ).fetchone()
        record = self._alert_webhook_receipt_from_row(row)
        self.append(
            tenant_id=tenant_id,
            event_type=ALERT_WEBHOOK_RECEIVED_EVENT_TYPE,
            payload={
                "delivery_id": record.delivery_id,
                "alert_key": record.alert_key,
                "severity": record.severity,
                "body_hash": record.body_hash,
                "duplicate": not created,
                "duplicate_count": record.duplicate_count,
                "received_at": record.last_received_at.isoformat(),
            },
        )
        return record, created

    def list_alert_webhook_receipts(
        self,
        *,
        tenant_id: str,
        alert_key: str | None = None,
        delivery_id: str | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> list[AlertWebhookReceiptRecord]:
        clauses = ["tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if alert_key:
            clauses.append("alert_key = ?")
            params.append(alert_key)
        if delivery_id:
            clauses.append("delivery_id = ?")
            params.append(delivery_id)
        direction = "asc" if order == "asc" else "desc"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select *
                from alert_webhook_receipts
                where {" and ".join(clauses)}
                order by last_received_at {direction}, rowid {direction}
                limit ?
                """,
                params,
            ).fetchall()
        return [self._alert_webhook_receipt_from_row(row) for row in rows]

    def summarize_alert_webhook_receipts(
        self,
        *,
        tenant_id: str,
        receipt_grace_seconds: int = 0,
        now: datetime | None = None,
    ) -> AlertWebhookReceiptSummary:
        effective_now = now or utc_now()
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        grace_seconds = max(0, receipt_grace_seconds)
        receipt_cutoff = effective_now - timedelta(seconds=grace_seconds)
        with self._connect() as conn:
            receipt_row = conn.execute(
                """
                select
                  count(*) as total_count,
                  coalesce(sum(duplicate_count), 0) as duplicate_count_total,
                  max(last_received_at) as last_received_at
                from alert_webhook_receipts
                where tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
            coverage_row = conn.execute(
                """
                select
                  count(*) as sent_delivery_count,
                  coalesce(sum(case when receipts.delivery_id is not null then 1 else 0 end), 0)
                    as sent_with_receipt_count,
                  coalesce(sum(
                    case
                      when receipts.delivery_id is null
                       and coalesce(deliveries.delivered_at, deliveries.last_attempt_at, deliveries.updated_at) <= ?
                      then 1 else 0
                    end
                  ), 0)
                    as sent_without_receipt_count,
                  coalesce(sum(
                    case
                      when receipts.delivery_id is null
                       and coalesce(deliveries.delivered_at, deliveries.last_attempt_at, deliveries.updated_at) > ?
                      then 1 else 0
                    end
                  ), 0)
                    as recent_sent_pending_receipt_count,
                  min(
                    case
                      when receipts.delivery_id is null
                       and coalesce(deliveries.delivered_at, deliveries.last_attempt_at, deliveries.updated_at) <= ?
                      then coalesce(deliveries.delivered_at, deliveries.last_attempt_at, deliveries.updated_at)
                    end
                  )
                    as oldest_unconfirmed_sent_at
                from alert_delivery_outbox deliveries
                left join alert_webhook_receipts receipts
                  on receipts.tenant_id = deliveries.tenant_id
                 and receipts.delivery_id = deliveries.id
                where deliveries.tenant_id = ?
                  and deliveries.status = ?
                """,
                (
                    receipt_cutoff.isoformat(),
                    receipt_cutoff.isoformat(),
                    receipt_cutoff.isoformat(),
                    tenant_id,
                    AlertDeliveryStatus.sent.value,
                ),
            ).fetchone()
        return AlertWebhookReceiptSummary(
            total_count=int(receipt_row["total_count"] or 0),
            duplicate_count_total=int(receipt_row["duplicate_count_total"] or 0),
            sent_delivery_count=int(coverage_row["sent_delivery_count"] or 0),
            sent_with_receipt_count=int(coverage_row["sent_with_receipt_count"] or 0),
            sent_without_receipt_count=int(coverage_row["sent_without_receipt_count"] or 0),
            recent_sent_pending_receipt_count=int(coverage_row["recent_sent_pending_receipt_count"] or 0),
            receipt_grace_seconds=grace_seconds,
            last_received_at=_parse_optional_datetime(receipt_row["last_received_at"]),
            oldest_unconfirmed_sent_at=_parse_optional_datetime(coverage_row["oldest_unconfirmed_sent_at"]),
        )

    def record_alert_delivery_attempt(
        self,
        delivery_id: str,
        *,
        status: AlertDeliveryStatus,
        response_status_code: int | None = None,
        last_error: str | None = None,
        max_attempts: int | None = None,
        backoff_seconds: int | None = None,
        worker_id: str | None = None,
    ) -> AlertDeliveryRecord:
        now = utc_now()
        delivered_at = now if status == AlertDeliveryStatus.sent else None
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select attempt_count, locked_by, status from alert_delivery_outbox where id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Alert delivery not found: {delivery_id}")
            if worker_id is not None and (
                row["status"] != AlertDeliveryStatus.in_progress.value or row["locked_by"] != worker_id
            ):
                raise AlertDeliveryLockLostError(f"Alert delivery lock is not held by worker: {delivery_id}")
            attempt_count = int(row["attempt_count"]) + 1
            final_status = status
            next_attempt_at = None
            dead_lettered_at = None
            if status == AlertDeliveryStatus.failed:
                if max_attempts is not None and attempt_count >= max_attempts:
                    final_status = AlertDeliveryStatus.dead
                    dead_lettered_at = now
                elif backoff_seconds is not None:
                    next_attempt_at = now + timedelta(seconds=backoff_seconds)
            conn.execute(
                """
                update alert_delivery_outbox
                set status = ?,
                    attempt_count = ?,
                    next_attempt_at = ?,
                    last_attempt_at = ?,
                    delivered_at = coalesce(?, delivered_at),
                    dead_lettered_at = coalesce(?, dead_lettered_at),
                    locked_until = null,
                    locked_by = null,
                    response_status_code = ?,
                    last_error = ?,
                    updated_at = ?
                where id = ?
                """,
                (
                    final_status.value,
                    attempt_count,
                    next_attempt_at.isoformat() if next_attempt_at else None,
                    now.isoformat(),
                    delivered_at.isoformat() if delivered_at else None,
                    dead_lettered_at.isoformat() if dead_lettered_at else None,
                    response_status_code,
                    last_error,
                    now.isoformat(),
                    delivery_id,
                ),
            )
            row = conn.execute(
                "select * from alert_delivery_outbox where id = ?",
                (delivery_id,),
            ).fetchone()
        record = self._alert_delivery_from_row(row)
        self.append(
            tenant_id=record.tenant_id,
            event_type=ALERT_DELIVERY_ATTEMPTED_EVENT_TYPE,
            payload={
                "delivery_id": record.id,
                "alert_key": record.alert_key,
                "severity": record.severity,
                "status": record.status.value,
                "attempt_count": record.attempt_count,
                "response_status_code": record.response_status_code,
                "last_error": record.last_error,
                "updated_at": record.updated_at.isoformat(),
            },
        )
        return record

    def requeue_alert_delivery(
        self,
        delivery_id: str,
        *,
        tenant_id: str,
        actor_user_id: str,
        note: str = "",
    ) -> AlertDeliveryRecord:
        record = self._record_alert_delivery_operator_action(
            delivery_id,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            note=note,
            action="requeued",
            final_status=AlertDeliveryStatus.pending,
            allowed_statuses={
                AlertDeliveryStatus.dead.value,
            },
            reset_attempts=True,
            event_type=ALERT_DELIVERY_REQUEUED_EVENT_TYPE,
        )
        return record

    def close_alert_delivery(
        self,
        delivery_id: str,
        *,
        tenant_id: str,
        actor_user_id: str,
        note: str = "",
    ) -> AlertDeliveryRecord:
        record = self._record_alert_delivery_operator_action(
            delivery_id,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            note=note,
            action="closed",
            final_status=AlertDeliveryStatus.closed,
            allowed_statuses={
                AlertDeliveryStatus.dead.value,
            },
            reset_attempts=False,
            event_type=ALERT_DELIVERY_CLOSED_EVENT_TYPE,
        )
        return record

    def _record_alert_delivery_operator_action(
        self,
        delivery_id: str,
        *,
        tenant_id: str,
        actor_user_id: str,
        note: str,
        action: str,
        final_status: AlertDeliveryStatus,
        allowed_statuses: set[str],
        reset_attempts: bool,
        event_type: str,
    ) -> AlertDeliveryRecord:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from alert_delivery_outbox where id = ? and tenant_id = ?",
                (delivery_id, tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Alert delivery not found: {delivery_id}")
            if row["status"] not in allowed_statuses:
                raise ValueError(f"Alert delivery {delivery_id} is not eligible for {action}")
            conn.execute(
                """
                update alert_delivery_outbox
                set status = ?,
                    attempt_count = ?,
                    next_attempt_at = null,
                    dead_lettered_at = ?,
                    locked_until = null,
                    locked_by = null,
                    operator_action = ?,
                    operator_action_at = ?,
                    operator_action_by = ?,
                    operator_action_note = ?,
                    response_status_code = ?,
                    last_error = ?,
                    updated_at = ?
                where id = ? and tenant_id = ?
                """,
                (
                    final_status.value,
                    0 if reset_attempts else int(row["attempt_count"]),
                    None if reset_attempts else row["dead_lettered_at"],
                    action,
                    now.isoformat(),
                    actor_user_id,
                    note,
                    None if reset_attempts else row["response_status_code"],
                    None if reset_attempts else row["last_error"],
                    now.isoformat(),
                    delivery_id,
                    tenant_id,
                ),
            )
            updated = conn.execute(
                "select * from alert_delivery_outbox where id = ? and tenant_id = ?",
                (delivery_id, tenant_id),
            ).fetchone()
        record = self._alert_delivery_from_row(updated)
        self.append(
            tenant_id=tenant_id,
            event_type=event_type,
            payload={
                "delivery_id": record.id,
                "alert_key": record.alert_key,
                "severity": record.severity,
                "status": record.status.value,
                "operator_action": action,
                "operator_action_by": actor_user_id,
                "operator_action_note": note,
                "attempt_count": record.attempt_count,
                "updated_at": record.updated_at.isoformat(),
            },
        )
        return record

    def reserve_api_request_nonce(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        nonce: str,
        request_hash: str,
        expires_at: str,
    ) -> bool:
        now = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute("begin immediate")
            conn.execute("delete from api_request_nonces where expires_at <= ?", (now,))
            try:
                conn.execute(
                    """
                    insert into api_request_nonces (
                      tenant_id, actor_user_id, nonce, request_hash, created_at, expires_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (tenant_id, actor_user_id, nonce, request_hash, now, expires_at),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def consume_api_rate_limit_token(
        self,
        *,
        bucket_key: str,
        requests_per_minute: int,
        burst: int,
        now_epoch_seconds: float | None = None,
    ) -> dict[str, int | bool]:
        now = float(now_epoch_seconds if now_epoch_seconds is not None else time.time())
        refill_per_second = requests_per_minute / 60
        stale_after_seconds = max(3600, math.ceil(max(1, burst) / refill_per_second) * 4)
        stale_before = now - stale_after_seconds

        with self._connect() as conn:
            conn.execute("begin immediate")
            conn.execute("delete from api_rate_limits where updated_at <= ?", (stale_before,))
            row = conn.execute(
                """
                select tokens, updated_at
                from api_rate_limits
                where bucket_key = ?
                """,
                (bucket_key,),
            ).fetchone()
            if row:
                elapsed = max(0.0, now - float(row["updated_at"]))
                tokens = min(float(burst), float(row["tokens"]) + elapsed * refill_per_second)
            else:
                tokens = float(burst)

            if tokens < 1:
                retry_after = math.ceil((1 - tokens) / refill_per_second) if refill_per_second > 0 else 60
                conn.execute(
                    """
                    insert into api_rate_limits (bucket_key, tokens, updated_at)
                    values (?, ?, ?)
                    on conflict(bucket_key) do update set
                      tokens = excluded.tokens,
                      updated_at = excluded.updated_at
                    """,
                    (bucket_key, tokens, now),
                )
                return {
                    "allowed": False,
                    "remaining": 0,
                    "retry_after_seconds": max(1, retry_after),
                }

            tokens -= 1
            conn.execute(
                """
                insert into api_rate_limits (bucket_key, tokens, updated_at)
                values (?, ?, ?)
                on conflict(bucket_key) do update set
                  tokens = excluded.tokens,
                  updated_at = excluded.updated_at
                """,
                (bucket_key, tokens, now),
            )
            return {
                "allowed": True,
                "remaining": max(0, math.floor(tokens)),
                "retry_after_seconds": 0,
            }

    def reserve(self, key: str, arg_hash: str) -> IdempotencyDecision:
        now = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                """
                select scope_key, argument_hash, status, result_json, updated_at
                from tool_idempotency
                where scope_key = ?
                """,
                (key,),
            ).fetchone()
            if not row:
                conn.execute(
                    """
                    insert into tool_idempotency (
                      scope_key, argument_hash, status, result_json, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (key, arg_hash, "in_progress", None, now, now),
                )
                return IdempotencyDecision(status="reserved")
            if row["argument_hash"] != arg_hash:
                return IdempotencyDecision(status="conflict")
            if row["status"] == "completed" and row["result_json"]:
                return IdempotencyDecision(
                    status="replay",
                    result=ToolResult.model_validate(json.loads(row["result_json"])),
                )
            if self._idempotency_row_is_stale(row["updated_at"]):
                conn.execute(
                    """
                    update tool_idempotency
                    set status = ?, result_json = ?, updated_at = ?
                    where scope_key = ? and argument_hash = ?
                    """,
                    ("in_progress", None, now, key, arg_hash),
                )
                return IdempotencyDecision(status="reserved")
            return IdempotencyDecision(status="in_progress")

    def complete(self, key: str, arg_hash: str, result: ToolResult) -> None:
        now = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                update tool_idempotency
                set status = ?, result_json = ?, updated_at = ?
                where scope_key = ? and argument_hash = ? and status = ?
                """,
                (
                    "completed",
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                    now,
                    key,
                    arg_hash,
                    "in_progress",
                ),
            )

    def release(self, key: str, arg_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                delete from tool_idempotency
                where scope_key = ? and argument_hash = ? and status = ?
                """,
                (key, arg_hash, "in_progress"),
            )

    def backup_to(
        self,
        target_path: str | Path,
        *,
        overwrite: bool = False,
        verify: bool = True,
    ) -> SQLiteBackupReport:
        started_at = utc_now()
        backup_path = Path(target_path)
        if backup_path.resolve() == self.path.resolve():
            raise ValueError("Backup path must be different from the source database path")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if backup_path.exists():
            if not overwrite:
                raise FileExistsError(f"Backup already exists: {backup_path}")
            backup_path.unlink()

        source = self._connect()
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        page_count = 0
        verified = not verify
        verification_detail = "verification skipped"
        if verify:
            conn = sqlite3.connect(backup_path)
            try:
                quick_check = conn.execute("pragma quick_check").fetchone()
                page_count = int(conn.execute("pragma page_count").fetchone()[0])
                placeholders = ", ".join("?" for _ in SQLITE_REQUIRED_TABLES)
                table_rows = conn.execute(
                    f"""
                    select name
                    from sqlite_master
                    where type = 'table'
                      and name in ({placeholders})
                    """,
                    SQLITE_REQUIRED_TABLES,
                ).fetchall()
            finally:
                conn.close()
            present_tables = {row[0] for row in table_rows}
            verified = bool(
                quick_check
                and quick_check[0] == "ok"
                and set(SQLITE_REQUIRED_TABLES).issubset(present_tables)
            )
            verification_detail = "quick_check=ok; required tables present" if verified else "backup verification failed"
            if not verified:
                raise RuntimeError(verification_detail)
        else:
            conn = sqlite3.connect(backup_path)
            try:
                page_count = int(conn.execute("pragma page_count").fetchone()[0])
            finally:
                conn.close()

        return SQLiteBackupReport(
            source_path=str(self.path),
            backup_path=str(backup_path),
            size_bytes=backup_path.stat().st_size,
            page_count=page_count,
            started_at=started_at,
            completed_at=utc_now(),
            verified=verified,
            verification_detail=verification_detail,
        )

    def restore_drill(
        self,
        backup_path: str | Path,
        *,
        restore_path: str | Path | None = None,
        overwrite: bool = False,
        tenant_id: str = "demo_tenant",
    ) -> SQLiteRestoreDrillReport:
        started_at = utc_now()
        source_backup = Path(backup_path)
        if not source_backup.exists() or not source_backup.is_file():
            raise FileNotFoundError(f"Backup file does not exist: {source_backup}")
        source_backup_resolved = source_backup.resolve()
        if source_backup_resolved == self.path.resolve():
            raise ValueError("Restore drill backup path must be different from the live database path")

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if restore_path is None:
            temp_dir = tempfile.TemporaryDirectory(prefix="support-agent-restore-drill-")
            target_path = Path(temp_dir.name) / source_backup.name
            restore_path_retained = False
        else:
            target_path = Path(restore_path)
            restore_path_retained = True

        try:
            target_resolved = target_path.resolve()
            if target_resolved in {self.path.resolve(), source_backup_resolved}:
                raise ValueError("Restore drill target must not overwrite the live database or backup file")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists():
                if not overwrite:
                    raise FileExistsError(f"Restore drill target already exists: {target_path}")
                if not target_path.is_file():
                    raise ValueError(f"Restore drill target is not a file: {target_path}")
                target_path.unlink()

            shutil.copy2(source_backup, target_path)
            page_count, table_counts = self._verify_required_sqlite_file(target_path)
            restored_store = object.__new__(SQLiteEventStore)
            restored_store.path = target_path
            restored_store.tool_idempotency_lease_seconds = self.tool_idempotency_lease_seconds
            restored_store.health_check()
            high_water_mark = restored_store.retention_high_water_mark(tenant_id=tenant_id)
            return SQLiteRestoreDrillReport(
                backup_path=str(source_backup),
                restore_path=str(target_path),
                restore_path_retained=restore_path_retained,
                size_bytes=target_path.stat().st_size,
                page_count=page_count,
                started_at=started_at,
                completed_at=utc_now(),
                verified=True,
                verification_detail="quick_check=ok; required schema present; restore health_check passed",
                health_check_passed=True,
                table_counts=table_counts,
                high_water_mark=high_water_mark,
            )
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    def retention_high_water_mark(self, *, tenant_id: str) -> dict[str, dict[str, Any]]:
        scope_prefix = f"{tenant_id}:"
        conn = self._connect()
        try:
            return {
                "tool_idempotency": self._table_high_water_mark(
                    conn,
                    table_name="tool_idempotency",
                    where_sql="substr(scope_key, 1, ?) = ?",
                    params=[len(scope_prefix), scope_prefix],
                    timestamp_columns=["updated_at"],
                ),
                "tool_audit_records": self._table_high_water_mark(
                    conn,
                    table_name="tool_audit_records",
                    where_sql="tenant_id = ?",
                    params=[tenant_id],
                    timestamp_columns=["created_at"],
                ),
                "alert_delivery_outbox": self._table_high_water_mark(
                    conn,
                    table_name="alert_delivery_outbox",
                    where_sql="tenant_id = ?",
                    params=[tenant_id],
                    timestamp_columns=["created_at", "updated_at"],
                ),
                "alert_webhook_receipts": self._table_high_water_mark(
                    conn,
                    table_name="alert_webhook_receipts",
                    where_sql="tenant_id = ?",
                    params=[tenant_id],
                    timestamp_columns=["first_received_at", "last_received_at"],
                ),
                "events": self._table_high_water_mark(
                    conn,
                    table_name="events",
                    where_sql="tenant_id = ?",
                    params=[tenant_id],
                    timestamp_columns=["created_at"],
                ),
            }
        finally:
            conn.close()

    def apply_retention_policy(
        self,
        *,
        tenant_id: str,
        dry_run: bool = True,
        include_events: bool = False,
        event_retention_days: int = 365,
        tool_audit_retention_days: int = 180,
        idempotency_retention_days: int = 30,
        alert_delivery_retention_days: int = 90,
        vacuum: bool = False,
        now: datetime | None = None,
    ) -> EventStoreRetentionReport:
        started_at = utc_now()
        effective_now = now or started_at
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        event_cutoff = effective_now - timedelta(days=event_retention_days)
        audit_cutoff = effective_now - timedelta(days=tool_audit_retention_days)
        idempotency_cutoff = effective_now - timedelta(days=idempotency_retention_days)
        alert_cutoff = effective_now - timedelta(days=alert_delivery_retention_days)
        reports: list[RetentionTableReport] = []

        with self._connect() as conn:
            if not dry_run:
                conn.execute("begin immediate")
            reports.append(
                self._retention_table_report(
                    conn,
                    table_name="api_request_nonces",
                    where_sql="tenant_id = ? and expires_at <= ?",
                    params=[tenant_id, effective_now.isoformat()],
                    dry_run=dry_run,
                    cutoff_at=effective_now,
                    reason="expired request-signature nonces",
                )
            )
            reports.append(
                self._retention_table_report(
                    conn,
                    table_name="tool_idempotency",
                    where_sql="substr(scope_key, 1, ?) = ? and updated_at <= ?",
                    params=[len(f"{tenant_id}:"), f"{tenant_id}:", idempotency_cutoff.isoformat()],
                    dry_run=dry_run,
                    cutoff_at=idempotency_cutoff,
                    reason="old tool idempotency replay entries",
                )
            )
            reports.append(
                self._retention_table_report(
                    conn,
                    table_name="tool_audit_records",
                    where_sql="tenant_id = ? and created_at <= ?",
                    params=[tenant_id, audit_cutoff.isoformat()],
                    dry_run=dry_run,
                    cutoff_at=audit_cutoff,
                    reason="old durable tool audit rows",
                )
            )
            reports.append(
                self._retention_table_report(
                    conn,
                    table_name="alert_delivery_outbox",
                    where_sql="tenant_id = ? and status in ('sent', 'closed') and updated_at <= ?",
                    params=[tenant_id, alert_cutoff.isoformat()],
                    dry_run=dry_run,
                    cutoff_at=alert_cutoff,
                    reason="terminal alert deliveries only; pending, failed, in-progress, and dead rows are retained",
                )
            )
            reports.append(
                self._retention_table_report(
                    conn,
                    table_name="alert_webhook_receipts",
                    where_sql="tenant_id = ? and last_received_at <= ?",
                    params=[tenant_id, alert_cutoff.isoformat()],
                    dry_run=dry_run,
                    cutoff_at=alert_cutoff,
                    reason="old inbound alert webhook receipt summaries",
                )
            )
            if include_events:
                reports.append(
                    self._retention_table_report(
                        conn,
                        table_name="events",
                        where_sql="tenant_id = ? and created_at <= ?",
                        params=[tenant_id, event_cutoff.isoformat()],
                        dry_run=dry_run,
                        cutoff_at=event_cutoff,
                        reason="old append-only event log rows; run export or backup before applying",
                    )
                )
            else:
                candidate_count = self._retention_count(
                    conn,
                    table_name="events",
                    where_sql="tenant_id = ? and created_at <= ?",
                    params=[tenant_id, event_cutoff.isoformat()],
                )
                reports.append(
                    RetentionTableReport(
                        table_name="events",
                        cutoff_at=event_cutoff,
                        candidate_count=candidate_count,
                        deleted_count=0,
                        action="skipped",
                        reason="event log retention requires include_events=true",
                    )
                )

        total_deleted = sum(report.deleted_count for report in reports)
        vacuum_performed = False
        if vacuum and not dry_run and total_deleted > 0:
            with self._connect() as conn:
                conn.execute("vacuum")
            vacuum_performed = True

        return EventStoreRetentionReport(
            tenant_id=tenant_id,
            dry_run=dry_run,
            include_events=include_events,
            vacuum_requested=vacuum,
            vacuum_performed=vacuum_performed,
            started_at=started_at,
            completed_at=utc_now(),
            tables=reports,
            total_candidates=sum(report.candidate_count for report in reports),
            total_deleted=total_deleted,
        )

    def append_event_store_operation(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        operation: str,
        status: str,
        summary: dict[str, Any],
        created_at: str | None = None,
    ) -> EventStoreOperationRecord:
        record = EventStoreOperationRecord(
            id=new_id("evt_op"),
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            operation=operation,
            status=status,
            summary=summary,
            created_at=created_at or utc_now().isoformat(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into event_store_operations (
                  id, tenant_id, actor_user_id, operation, status, summary_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.actor_user_id,
                    record.operation,
                    record.status,
                    json.dumps(record.summary, ensure_ascii=False, sort_keys=True),
                    record.created_at,
                ),
            )
        return record

    def list_event_store_operations(
        self,
        *,
        tenant_id: str | None = None,
        actor_user_id: str | None = None,
        operation: str | None = None,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 50,
        order: str = "desc",
    ) -> list[EventStoreOperationRecord]:
        sql = """
            select id, tenant_id, actor_user_id, operation, status, summary_json, created_at
            from event_store_operations
        """
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if actor_user_id:
            clauses.append("actor_user_id = ?")
            params.append(actor_user_id)
        if operation:
            clauses.append("operation = ?")
            params.append(operation)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        if clauses:
            sql += " where " + " and ".join(clauses)
        direction = "asc" if order == "asc" else "desc"
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            EventStoreOperationRecord(
                id=row["id"],
                tenant_id=row["tenant_id"],
                actor_user_id=row["actor_user_id"],
                operation=row["operation"],
                status=row["status"],
                summary=json.loads(row["summary_json"] or "{}"),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def append_operations_automation_execution(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        action_id: str,
        action_kind: str,
        title: str,
        status: str,
        safe_to_auto_execute: bool,
        command_method: str,
        command_path: str,
        command_query: dict[str, Any],
        command_body_keys: list[str],
        command_body_hash: str | None,
        command_fingerprint: str,
        result_summary: str,
        error_detail: str | None = None,
        source: str = "api",
        created_at: str | None = None,
    ) -> OperationsAutomationExecutionRecord:
        record = OperationsAutomationExecutionRecord(
            id=new_id("ops_exec"),
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action_id=action_id,
            action_kind=action_kind,
            title=title,
            status=status,
            safe_to_auto_execute=safe_to_auto_execute,
            command_method=command_method,
            command_path=command_path,
            command_query=command_query,
            command_body_keys=command_body_keys,
            command_body_hash=command_body_hash,
            command_fingerprint=command_fingerprint,
            result_summary=result_summary,
            error_detail=error_detail,
            source=source,
            created_at=created_at or utc_now().isoformat(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into operations_automation_executions (
                  id, tenant_id, actor_user_id, action_id, action_kind, title, status,
                  safe_to_auto_execute, command_method, command_path, command_query_json,
                  command_body_keys_json, command_body_hash, command_fingerprint,
                  result_summary, error_detail, source, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.actor_user_id,
                    record.action_id,
                    record.action_kind,
                    record.title,
                    record.status,
                    1 if record.safe_to_auto_execute else 0,
                    record.command_method,
                    record.command_path,
                    json.dumps(record.command_query, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.command_body_keys, ensure_ascii=False, sort_keys=True),
                    record.command_body_hash,
                    record.command_fingerprint,
                    record.result_summary,
                    record.error_detail,
                    record.source,
                    record.created_at,
                ),
            )
        return record

    def list_operations_automation_executions(
        self,
        *,
        tenant_id: str | None = None,
        actor_user_id: str | None = None,
        action_kind: str | None = None,
        status: str | None = None,
        source: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 50,
        order: str = "desc",
    ) -> list[OperationsAutomationExecutionRecord]:
        sql = """
            select
              id, tenant_id, actor_user_id, action_id, action_kind, title, status,
              safe_to_auto_execute, command_method, command_path, command_query_json,
              command_body_keys_json, command_body_hash, command_fingerprint,
              result_summary, error_detail, source, created_at
            from operations_automation_executions
        """
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if actor_user_id:
            clauses.append("actor_user_id = ?")
            params.append(actor_user_id)
        if action_kind:
            clauses.append("action_kind = ?")
            params.append(action_kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        if clauses:
            sql += " where " + " and ".join(clauses)
        direction = "asc" if order == "asc" else "desc"
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._operations_automation_execution_from_row(row) for row in rows]

    @contextmanager
    def event_store_operation_lock(
        self,
        *,
        tenant_id: str,
        lock_name: str,
        operation: str,
        owner_id: str | None = None,
        ttl_seconds: int = 1800,
        now: datetime | None = None,
    ) -> Iterator[EventStoreOperationLock]:
        lock = self.acquire_event_store_operation_lock(
            tenant_id=tenant_id,
            lock_name=lock_name,
            operation=operation,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        try:
            yield lock
        finally:
            self.release_event_store_operation_lock(lock)

    def acquire_event_store_operation_lock(
        self,
        *,
        tenant_id: str,
        lock_name: str,
        operation: str,
        owner_id: str | None = None,
        ttl_seconds: int = 1800,
        now: datetime | None = None,
    ) -> EventStoreOperationLock:
        effective_now = now or utc_now()
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        ttl_seconds = max(1, int(ttl_seconds))
        lock = EventStoreOperationLock(
            tenant_id=tenant_id,
            lock_name=lock_name,
            owner_id=owner_id or new_id("evt_lock"),
            operation=operation,
            acquired_at=effective_now.isoformat(),
            expires_at=(effective_now + timedelta(seconds=ttl_seconds)).isoformat(),
        )
        with self._connect() as conn:
            conn.execute("begin immediate")
            conn.execute(
                """
                delete from event_store_operation_locks
                where tenant_id = ? and lock_name = ? and expires_at <= ?
                """,
                (tenant_id, lock_name, effective_now.isoformat()),
            )
            active = conn.execute(
                """
                select tenant_id, lock_name, owner_id, operation, acquired_at, expires_at
                from event_store_operation_locks
                where tenant_id = ? and lock_name = ?
                """,
                (tenant_id, lock_name),
            ).fetchone()
            if active:
                raise EventStoreOperationLockConflict(self._event_store_operation_lock_from_row(active))
            conn.execute(
                """
                insert into event_store_operation_locks (
                  tenant_id, lock_name, owner_id, operation, acquired_at, expires_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    lock.tenant_id,
                    lock.lock_name,
                    lock.owner_id,
                    lock.operation,
                    lock.acquired_at,
                    lock.expires_at,
                ),
            )
        return lock

    def release_event_store_operation_lock(self, lock: EventStoreOperationLock) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                delete from event_store_operation_locks
                where tenant_id = ? and lock_name = ? and owner_id = ?
                """,
                (lock.tenant_id, lock.lock_name, lock.owner_id),
            )
        return cursor.rowcount > 0

    def append_tool_audit(self, record: ToolAuditRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into tool_audit_records (
                  id, tenant_id, actor_user_id, request_id, trace_id, tool_name,
                  argument_hash, status, latency_ms, error_code,
                  idempotency_key_hash, replayed, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.actor_user_id,
                    record.request_id,
                    record.trace_id,
                    record.tool_name,
                    record.argument_hash,
                    record.status.value,
                    record.latency_ms,
                    record.error_code,
                    record.idempotency_key_hash,
                    int(record.replayed),
                    record.created_at or utc_now().isoformat(),
                ),
            )

    def list_tool_audit_records(
        self,
        *,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        actor_user_id: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        replayed: bool | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 100,
        order: str = "asc",
    ) -> list[ToolAuditRecord]:
        sql = """
            select id, tenant_id, actor_user_id, request_id, trace_id, tool_name,
                   argument_hash, status, latency_ms, error_code,
                   idempotency_key_hash, replayed, created_at
            from tool_audit_records
        """
        clauses, params = self._tool_audit_filter_clauses(
            tenant_id=tenant_id,
            tool_name=tool_name,
            actor_user_id=actor_user_id,
            trace_id=trace_id,
            request_id=request_id,
            status=status,
            error_code=error_code,
            replayed=replayed,
            created_after=created_after,
            created_before=created_before,
        )
        if clauses:
            sql += " where " + " and ".join(clauses)
        direction = "desc" if order == "desc" else "asc"
        sql += f" order by rowid {direction} limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            ToolAuditRecord(
                id=row["id"],
                tenant_id=row["tenant_id"],
                actor_user_id=row["actor_user_id"],
                request_id=row["request_id"],
                trace_id=row["trace_id"],
                tool_name=row["tool_name"],
                argument_hash=row["argument_hash"],
                status=ToolStatus(row["status"]),
                latency_ms=row["latency_ms"],
                error_code=row["error_code"],
                idempotency_key_hash=row["idempotency_key_hash"],
                replayed=bool(row["replayed"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def summarize_tool_audit_records(
        self,
        *,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        actor_user_id: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        replayed: bool | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> ToolAuditSummary:
        clauses, params = self._tool_audit_filter_clauses(
            tenant_id=tenant_id,
            tool_name=tool_name,
            actor_user_id=actor_user_id,
            trace_id=trace_id,
            request_id=request_id,
            status=status,
            error_code=error_code,
            replayed=replayed,
            created_after=created_after,
            created_before=created_before,
        )
        where_sql = f" where {' and '.join(clauses)}" if clauses else ""
        totals_sql = f"""
            select
              count(*) as total_calls,
              coalesce(sum(case when status = 'failed' then 1 else 0 end), 0) as failed_calls,
              coalesce(sum(case when replayed = 1 then 1 else 0 end), 0) as replayed_calls,
              avg(latency_ms) as average_latency_ms,
              max(latency_ms) as max_latency_ms,
              min(created_at) as window_start,
              max(created_at) as window_end
            from tool_audit_records
            {where_sql}
        """
        tools_sql = f"""
            select
              tool_name,
              count(*) as total_calls,
              coalesce(sum(case when status = 'failed' then 1 else 0 end), 0) as failed_calls,
              coalesce(sum(case when replayed = 1 then 1 else 0 end), 0) as replayed_calls,
              avg(latency_ms) as average_latency_ms,
              max(latency_ms) as max_latency_ms,
              max(created_at) as last_seen_at
            from tool_audit_records
            {where_sql}
            group by tool_name
            order by failed_calls desc, total_calls desc, tool_name asc
            limit 50
        """
        error_clauses = [*clauses, "error_code is not null"]
        error_where_sql = " where " + " and ".join(error_clauses)
        errors_sql = f"""
            select error_code, count(*) as count
            from tool_audit_records
            {error_where_sql}
            group by error_code
            order by count desc, error_code asc
            limit 5
        """
        tool_errors_sql = f"""
            select tool_name, error_code, count(*) as count
            from tool_audit_records
            {error_where_sql}
            group by tool_name, error_code
            order by count desc, error_code asc
        """
        with self._connect() as conn:
            totals = conn.execute(totals_sql, params).fetchone()
            tool_rows = conn.execute(tools_sql, params).fetchall()
            error_rows = conn.execute(errors_sql, params).fetchall()
            tool_error_rows = conn.execute(tool_errors_sql, params).fetchall()

        top_error_by_tool: dict[str, str] = {}
        for row in tool_error_rows:
            top_error_by_tool.setdefault(row["tool_name"], row["error_code"])

        total_calls = int(totals["total_calls"] or 0)
        failed_calls = int(totals["failed_calls"] or 0)
        replayed_calls = int(totals["replayed_calls"] or 0)
        return ToolAuditSummary(
            total_calls=total_calls,
            failed_calls=failed_calls,
            replayed_calls=replayed_calls,
            failure_rate=_rate(failed_calls, total_calls),
            average_latency_ms=_rounded_average(totals["average_latency_ms"]),
            max_latency_ms=totals["max_latency_ms"],
            window_start=totals["window_start"],
            window_end=totals["window_end"],
            top_error_codes=[
                ToolAuditErrorSummary(error_code=row["error_code"], count=int(row["count"]))
                for row in error_rows
            ],
            tools=[
                ToolAuditToolSummary(
                    tool_name=row["tool_name"],
                    total_calls=int(row["total_calls"]),
                    failed_calls=int(row["failed_calls"]),
                    replayed_calls=int(row["replayed_calls"]),
                    failure_rate=_rate(int(row["failed_calls"]), int(row["total_calls"])),
                    average_latency_ms=_rounded_average(row["average_latency_ms"]),
                    max_latency_ms=row["max_latency_ms"],
                    top_error_code=top_error_by_tool.get(row["tool_name"]),
                    last_seen_at=row["last_seen_at"],
                )
                for row in tool_rows
            ],
        )

    def _tool_audit_filter_clauses(
        self,
        *,
        tenant_id: str | None = None,
        tool_name: str | None = None,
        actor_user_id: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        replayed: bool | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if actor_user_id:
            clauses.append("actor_user_id = ?")
            params.append(actor_user_id)
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if request_id:
            clauses.append("request_id = ?")
            params.append(request_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if error_code:
            clauses.append("error_code = ?")
            params.append(error_code)
        if replayed is not None:
            clauses.append("replayed = ?")
            params.append(int(replayed))
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        return clauses, params

    def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
    ) -> StoredEvent:
        event = StoredEvent(
            id=new_id("evt"),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            created_at=utc_now().isoformat(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into events (
                  id, tenant_id, conversation_id, user_id, run_id, event_type, payload_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.tenant_id,
                    event.conversation_id,
                    event.user_id,
                    event.run_id,
                    event.event_type,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    event.created_at,
                ),
            )
        return event

    def list_events(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 100,
        order: str = "asc",
    ) -> list[StoredEvent]:
        sql = "select id, tenant_id, conversation_id, user_id, run_id, event_type, payload_json, created_at from events"
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        if clauses:
            sql += " where " + " and ".join(clauses)
        direction = "desc" if order == "desc" else "asc"
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            StoredEvent(
                id=row["id"],
                tenant_id=row["tenant_id"],
                conversation_id=row["conversation_id"],
                user_id=row["user_id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_conversation_memory_events(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[StoredEvent]:
        placeholders = ", ".join("?" for _ in MEMORY_REPLAY_EVENT_TYPES)
        sql = f"""
            select id, tenant_id, conversation_id, user_id, run_id, event_type, payload_json, created_at
            from events
            where tenant_id = ?
              and conversation_id = ?
              and event_type in ({placeholders})
            order by created_at asc, rowid asc
        """
        params: list[Any] = [tenant_id, conversation_id, *MEMORY_REPLAY_EVENT_TYPES]
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            StoredEvent(
                id=row["id"],
                tenant_id=row["tenant_id"],
                conversation_id=row["conversation_id"],
                user_id=row["user_id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_agent_run_trace(
        self,
        run_id: str,
        *,
        tenant_id: str | None = None,
        limit: int = 1000,
    ) -> AgentRunTrace | None:
        sql = "select payload_json from events where event_type = ? and run_id = ?"
        params: list[Any] = ["agent.run.completed", run_id]
        if tenant_id:
            sql += " and tenant_id = ?"
            params.append(tenant_id)
        sql += " order by created_at desc, rowid desc limit 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row:
            payload = json.loads(row["payload_json"])
            return AgentRunTrace.model_validate(payload)

        fallback_sql = "select payload_json from events where event_type = ?"
        fallback_params: list[Any] = ["agent.run.completed"]
        if tenant_id:
            fallback_sql += " and tenant_id = ?"
            fallback_params.append(tenant_id)
        fallback_sql += " order by created_at desc, rowid desc limit ?"
        fallback_params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(fallback_sql, fallback_params).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("id") == run_id:
                return AgentRunTrace.model_validate(payload)
        return None

    def search_agent_run_traces(
        self,
        *,
        tenant_id: str | None = None,
        query: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        request_id: str | None = None,
        parent_trace_id: str | None = None,
        intent: str | None = None,
        route: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "desc",
    ) -> tuple[list[AgentRunTrace], int]:
        where_sql = "where event_type = ?"
        params: list[Any] = ["agent.run.completed"]
        clauses: list[str] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if request_id:
            clauses.append("json_extract(payload_json, '$.request_id') = ?")
            params.append(request_id.strip())
        if parent_trace_id:
            clauses.append("json_extract(payload_json, '$.parent_trace_id') = ?")
            params.append(parent_trace_id.strip())
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        if intent:
            clauses.append("json_extract(payload_json, '$.intent.primary') = ?")
            params.append(intent)
        if route:
            clauses.append("json_extract(payload_json, '$.route.target') = ?")
            params.append(route)
        if status:
            clauses.append("json_extract(payload_json, '$.status') = ?")
            params.append(status)
        if error_code:
            clauses.append(
                """
                exists (
                  select 1
                  from json_each(events.payload_json, '$.tool_results') as tool
                  where json_extract(tool.value, '$.error_code') = ?
                )
                """
            )
            params.append(error_code.strip())
        if query and query.strip():
            query_like = f"%{query.strip().lower()}%"
            clauses.append(
                """
                (
                  lower(coalesce(run_id, '')) like ?
                  or lower(coalesce(conversation_id, '')) like ?
                  or lower(coalesce(user_id, '')) like ?
                  or lower(coalesce(json_extract(payload_json, '$.request_id'), '')) like ?
                  or lower(coalesce(json_extract(payload_json, '$.parent_trace_id'), '')) like ?
                  or lower(coalesce(json_extract(payload_json, '$.intent.primary'), '')) like ?
                  or lower(coalesce(json_extract(payload_json, '$.route.target'), '')) like ?
                  or exists (
                    select 1
                    from events as message_events
                    where message_events.tenant_id = events.tenant_id
                      and message_events.conversation_id = events.conversation_id
                      and message_events.event_type in ('message.user', 'message.assistant')
                      and lower(coalesce(json_extract(message_events.payload_json, '$.content'), '')) like ?
                  )
                )
                """
            )
            params.extend(
                [
                    query_like,
                    query_like,
                    query_like,
                    query_like,
                    query_like,
                    query_like,
                    query_like,
                    query_like,
                ]
            )
        if clauses:
            where_sql += " and " + " and ".join(clauses)
        direction = "asc" if order == "asc" else "desc"
        count_sql = f"select count(*) from events {where_sql}"
        select_sql = f"""
            select payload_json
            from events
            {where_sql}
            order by created_at {direction}, rowid {direction}
            limit ? offset ?
        """

        with self._connect() as conn:
            total = int(conn.execute(count_sql, params).fetchone()[0])
            rows = conn.execute(select_sql, [*params, limit, offset]).fetchall()

        traces = [AgentRunTrace.model_validate(json.loads(row["payload_json"])) for row in rows]
        return traces, total

    def list_monitor_events(
        self,
        *,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 500,
        order: str = "asc",
    ) -> list[MonitorEvent]:
        events = self.list_events(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type="monitor.reviewed",
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            order=order,
        )
        return [MonitorEvent.model_validate(event.payload) for event in events]

    def list_monitor_alert_triage_events(
        self,
        *,
        tenant_id: str | None = None,
        alert_key: str | None = None,
        limit: int = 500,
    ) -> list[MonitorAlertTriageEvent]:
        sql = "select payload_json from events where event_type = ?"
        params: list[Any] = ["monitor.alert.triaged"]
        if tenant_id:
            sql += " and tenant_id = ?"
            params.append(tenant_id)
        if alert_key:
            sql += " and json_extract(payload_json, '$.alert_key') = ?"
            params.append(alert_key)
        sql += " order by created_at asc, rowid asc limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            MonitorAlertTriageEvent.model_validate(json.loads(row["payload_json"]))
            for row in rows
        ]

    def list_eval_gate_records(
        self,
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_key: str | None = None,
        gate_name: str | None = None,
        runner: str | None = None,
        status: str | None = None,
        actor_user_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 20,
        order: str = "desc",
    ) -> list[EvalGateRecord]:
        direction = "asc" if order == "asc" else "desc"
        sql = "select payload_json from events where event_type = ?"
        params: list[Any] = [EVAL_GATE_EVENT_TYPE]
        clauses: list[str] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if run_id:
            clauses.append("json_extract(payload_json, '$.run_id') = ?")
            params.append(run_id)
        if alert_key:
            clauses.append("json_extract(payload_json, '$.alert_key') = ?")
            params.append(alert_key)
        if gate_name:
            clauses.append("json_extract(payload_json, '$.gate_name') = ?")
            params.append(gate_name)
        if runner:
            clauses.append("json_extract(payload_json, '$.runner') = ?")
            params.append(runner)
        if status:
            clauses.append("json_extract(payload_json, '$.status') = ?")
            params.append(status)
        if actor_user_id:
            clauses.append("user_id = ?")
            params.append(actor_user_id)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            params.append(created_before)
        if clauses:
            sql += " and " + " and ".join(clauses)
        sql += f" order by created_at {direction}, rowid {direction} limit ?"
        with self._connect() as conn:
            rows = conn.execute(sql, [*params, limit]).fetchall()
        return [EvalGateRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    def health_check(self) -> None:
        conn = self._connect()
        try:
            quick_check = conn.execute("pragma quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise RuntimeError("SQLite quick_check failed")
            row = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'events'"
            ).fetchone()
            if not row:
                raise RuntimeError("events table is missing")
            columns = {
                item["name"]
                for item in conn.execute("pragma table_info(events)").fetchall()
            }
            required = {
                "id",
                "tenant_id",
                "conversation_id",
                "user_id",
                "run_id",
                "event_type",
                "payload_json",
                "created_at",
            }
            missing = sorted(required - columns)
            if missing:
                raise RuntimeError(f"events table missing columns: {', '.join(missing)}")
            tool_idempotency = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'tool_idempotency'"
            ).fetchone()
            if not tool_idempotency:
                raise RuntimeError("tool_idempotency table is missing")
            tool_audit_records = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'tool_audit_records'"
            ).fetchone()
            if not tool_audit_records:
                raise RuntimeError("tool_audit_records table is missing")
            api_request_nonces = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'api_request_nonces'"
            ).fetchone()
            if not api_request_nonces:
                raise RuntimeError("api_request_nonces table is missing")
            alert_delivery_outbox = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'alert_delivery_outbox'"
            ).fetchone()
            if not alert_delivery_outbox:
                raise RuntimeError("alert_delivery_outbox table is missing")
            alert_dispatcher_heartbeats = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'alert_dispatcher_heartbeats'"
            ).fetchone()
            if not alert_dispatcher_heartbeats:
                raise RuntimeError("alert_dispatcher_heartbeats table is missing")
            heartbeat_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_dispatcher_heartbeats)").fetchall()
            }
            heartbeat_missing = sorted(SQLITE_ALERT_DISPATCHER_HEARTBEATS_REQUIRED_COLUMNS - heartbeat_columns)
            if heartbeat_missing:
                raise RuntimeError(
                    f"alert_dispatcher_heartbeats table missing columns: {', '.join(heartbeat_missing)}"
                )
            alert_webhook_receipts = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'alert_webhook_receipts'"
            ).fetchone()
            if not alert_webhook_receipts:
                raise RuntimeError("alert_webhook_receipts table is missing")
            receipt_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_webhook_receipts)").fetchall()
            }
            receipt_missing = sorted(SQLITE_ALERT_WEBHOOK_RECEIPTS_REQUIRED_COLUMNS - receipt_columns)
            if receipt_missing:
                raise RuntimeError(
                    f"alert_webhook_receipts table missing columns: {', '.join(receipt_missing)}"
                )
            event_store_operations = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'event_store_operations'"
            ).fetchone()
            if not event_store_operations:
                raise RuntimeError("event_store_operations table is missing")
            operation_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(event_store_operations)").fetchall()
            }
            operation_missing = sorted(SQLITE_EVENT_STORE_OPERATIONS_REQUIRED_COLUMNS - operation_columns)
            if operation_missing:
                raise RuntimeError(
                    f"event_store_operations table missing columns: {', '.join(operation_missing)}"
                )
            automation_executions = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'operations_automation_executions'"
            ).fetchone()
            if not automation_executions:
                raise RuntimeError("operations_automation_executions table is missing")
            automation_execution_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(operations_automation_executions)").fetchall()
            }
            automation_execution_missing = sorted(
                SQLITE_OPERATIONS_AUTOMATION_EXECUTIONS_REQUIRED_COLUMNS - automation_execution_columns
            )
            if automation_execution_missing:
                raise RuntimeError(
                    "operations_automation_executions table missing columns: "
                    f"{', '.join(automation_execution_missing)}"
                )
            operation_locks = conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'event_store_operation_locks'"
            ).fetchone()
            if not operation_locks:
                raise RuntimeError("event_store_operation_locks table is missing")
            lock_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(event_store_operation_locks)").fetchall()
            }
            lock_missing = sorted(SQLITE_EVENT_STORE_OPERATION_LOCKS_REQUIRED_COLUMNS - lock_columns)
            if lock_missing:
                raise RuntimeError(
                    f"event_store_operation_locks table missing columns: {', '.join(lock_missing)}"
                )
            conn.execute("begin immediate")
            conn.execute(
                """
                insert into events (
                  id, tenant_id, conversation_id, user_id, run_id, event_type, payload_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("ready"),
                    "readiness",
                    None,
                    None,
                    None,
                    "readiness.probe",
                    "{}",
                    utc_now().isoformat(),
                ),
            )
            conn.rollback()
        finally:
            conn.close()

    @classmethod
    def _verify_required_sqlite_file(cls, path: Path) -> tuple[int, dict[str, int]]:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            quick_check = conn.execute("pragma quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise RuntimeError("SQLite quick_check failed")
            page_count = int(conn.execute("pragma page_count").fetchone()[0])
            rows = conn.execute(
                """
                select name
                from sqlite_master
                where type = 'table'
                """
            ).fetchall()
            table_names = {row["name"] for row in rows}
            missing_tables = sorted(set(SQLITE_REQUIRED_TABLES) - table_names)
            if missing_tables:
                raise RuntimeError(f"required tables missing: {', '.join(missing_tables)}")
            event_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(events)").fetchall()
            }
            missing_columns = sorted(SQLITE_EVENTS_REQUIRED_COLUMNS - event_columns)
            if missing_columns:
                raise RuntimeError(f"events table missing columns: {', '.join(missing_columns)}")
            operation_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(event_store_operations)").fetchall()
            }
            operation_missing = sorted(SQLITE_EVENT_STORE_OPERATIONS_REQUIRED_COLUMNS - operation_columns)
            if operation_missing:
                raise RuntimeError(
                    f"event_store_operations table missing columns: {', '.join(operation_missing)}"
                )
            automation_execution_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(operations_automation_executions)").fetchall()
            }
            automation_execution_missing = sorted(
                SQLITE_OPERATIONS_AUTOMATION_EXECUTIONS_REQUIRED_COLUMNS - automation_execution_columns
            )
            if automation_execution_missing:
                raise RuntimeError(
                    "operations_automation_executions table missing columns: "
                    f"{', '.join(automation_execution_missing)}"
                )
            lock_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(event_store_operation_locks)").fetchall()
            }
            lock_missing = sorted(SQLITE_EVENT_STORE_OPERATION_LOCKS_REQUIRED_COLUMNS - lock_columns)
            if lock_missing:
                raise RuntimeError(
                    f"event_store_operation_locks table missing columns: {', '.join(lock_missing)}"
                )
            heartbeat_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_dispatcher_heartbeats)").fetchall()
            }
            heartbeat_missing = sorted(SQLITE_ALERT_DISPATCHER_HEARTBEATS_REQUIRED_COLUMNS - heartbeat_columns)
            if heartbeat_missing:
                raise RuntimeError(
                    f"alert_dispatcher_heartbeats table missing columns: {', '.join(heartbeat_missing)}"
                )
            receipt_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_webhook_receipts)").fetchall()
            }
            receipt_missing = sorted(SQLITE_ALERT_WEBHOOK_RECEIPTS_REQUIRED_COLUMNS - receipt_columns)
            if receipt_missing:
                raise RuntimeError(
                    f"alert_webhook_receipts table missing columns: {', '.join(receipt_missing)}"
                )
            table_counts = {
                table_name: int(conn.execute(f"select count(*) from {table_name}").fetchone()[0])
                for table_name in SQLITE_REQUIRED_TABLES
            }
            return page_count, table_counts
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"SQLite restore drill verification failed: {exc}") from exc
        finally:
            if conn is not None:
                conn.close()

    def _retention_table_report(
        self,
        conn: sqlite3.Connection,
        *,
        table_name: str,
        where_sql: str,
        params: list[Any],
        dry_run: bool,
        cutoff_at: datetime,
        reason: str,
    ) -> RetentionTableReport:
        candidate_count = self._retention_count(
            conn,
            table_name=table_name,
            where_sql=where_sql,
            params=params,
        )
        deleted_count = 0
        if not dry_run and candidate_count:
            conn.execute(f"delete from {table_name} where {where_sql}", params)
            deleted_count = candidate_count
        return RetentionTableReport(
            table_name=table_name,
            cutoff_at=cutoff_at,
            candidate_count=candidate_count,
            deleted_count=deleted_count,
            action="dry_run" if dry_run else "deleted",
            reason=reason,
        )

    def _retention_count(
        self,
        conn: sqlite3.Connection,
        *,
        table_name: str,
        where_sql: str,
        params: list[Any],
    ) -> int:
        row = conn.execute(
            f"select count(*) from {table_name} where {where_sql}",
            params,
        ).fetchone()
        return int(row[0])

    def _table_high_water_mark(
        self,
        conn: sqlite3.Connection,
        *,
        table_name: str,
        where_sql: str,
        params: list[Any],
        timestamp_columns: list[str],
    ) -> dict[str, Any]:
        select_parts = [
            "count(*) as row_count",
            "coalesce(max(rowid), 0) as max_rowid",
            *[f"max({column}) as max_{column}" for column in timestamp_columns],
        ]
        row = conn.execute(
            f"select {', '.join(select_parts)} from {table_name} where {where_sql}",
            params,
        ).fetchone()
        return {
            key: row[key]
            for key in row.keys()
        }

    def _event_store_operation_lock_from_row(self, row: sqlite3.Row) -> EventStoreOperationLock:
        return EventStoreOperationLock(
            tenant_id=row["tenant_id"],
            lock_name=row["lock_name"],
            owner_id=row["owner_id"],
            operation=row["operation"],
            acquired_at=row["acquired_at"],
            expires_at=row["expires_at"],
        )

    def _operations_automation_execution_from_row(
        self, row: sqlite3.Row
    ) -> OperationsAutomationExecutionRecord:
        return OperationsAutomationExecutionRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            actor_user_id=row["actor_user_id"],
            action_id=row["action_id"],
            action_kind=row["action_kind"],
            title=row["title"],
            status=row["status"],
            safe_to_auto_execute=bool(row["safe_to_auto_execute"]),
            command_method=row["command_method"],
            command_path=row["command_path"],
            command_query=json.loads(row["command_query_json"] or "{}"),
            command_body_keys=json.loads(row["command_body_keys_json"] or "[]"),
            command_body_hash=row["command_body_hash"],
            command_fingerprint=row["command_fingerprint"],
            result_summary=row["result_summary"],
            error_detail=row["error_detail"],
            source=row["source"],
            created_at=row["created_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.SQLITE_BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        self._configure_connection(conn)
        return conn

    def _configure_database_file(self) -> None:
        conn = sqlite3.connect(self.path, timeout=self.SQLITE_BUSY_TIMEOUT_MS / 1000)
        try:
            self._configure_connection(conn)
            conn.execute(f"pragma journal_mode = {self.SQLITE_JOURNAL_MODE}")
        finally:
            conn.close()

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"pragma busy_timeout = {self.SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute(f"pragma synchronous = {self.SQLITE_SYNCHRONOUS}")
        conn.execute("pragma foreign_keys = on")

    def _idempotency_row_is_stale(self, updated_at: str) -> bool:
        if self.tool_idempotency_lease_seconds <= 0:
            return True
        try:
            updated = datetime.fromisoformat(updated_at)
        except ValueError:
            return True
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return updated <= utc_now() - timedelta(seconds=self.tool_idempotency_lease_seconds)

    def _alert_delivery_from_row(self, row: sqlite3.Row) -> AlertDeliveryRecord:
        return AlertDeliveryRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            alert_key=row["alert_key"],
            severity=row["severity"],
            channel=row["channel"],
            destination_hash=row["destination_hash"],
            status=AlertDeliveryStatus(row["status"]),
            alert_first_seen_at=datetime.fromisoformat(row["alert_first_seen_at"]),
            alert_last_seen_at=datetime.fromisoformat(row["alert_last_seen_at"]),
            alert_count=int(row["alert_count"]),
            reason=row["reason"],
            sample_event_ids=json.loads(row["sample_event_ids_json"] or "[]"),
            sample_run_ids=json.loads(row["sample_run_ids_json"] or "[]"),
            payload_hash=row["payload_hash"],
            attempt_count=int(row["attempt_count"]),
            next_attempt_at=datetime.fromisoformat(row["next_attempt_at"]) if row["next_attempt_at"] else None,
            last_attempt_at=datetime.fromisoformat(row["last_attempt_at"]) if row["last_attempt_at"] else None,
            delivered_at=datetime.fromisoformat(row["delivered_at"]) if row["delivered_at"] else None,
            dead_lettered_at=datetime.fromisoformat(row["dead_lettered_at"]) if row["dead_lettered_at"] else None,
            locked_until=datetime.fromisoformat(row["locked_until"]) if row["locked_until"] else None,
            locked_by=row["locked_by"],
            operator_action=row["operator_action"],
            operator_action_at=datetime.fromisoformat(row["operator_action_at"]) if row["operator_action_at"] else None,
            operator_action_by=row["operator_action_by"],
            operator_action_note=row["operator_action_note"],
            response_status_code=row["response_status_code"],
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _alert_dispatcher_heartbeat_from_row(self, row: sqlite3.Row) -> AlertDispatcherHeartbeatRecord:
        return AlertDispatcherHeartbeatRecord(
            tenant_id=row["tenant_id"],
            worker_id=row["worker_id"],
            status=row["status"],
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            last_cycle_started_at=(
                datetime.fromisoformat(row["last_cycle_started_at"])
                if row["last_cycle_started_at"]
                else None
            ),
            last_cycle_completed_at=(
                datetime.fromisoformat(row["last_cycle_completed_at"])
                if row["last_cycle_completed_at"]
                else None
            ),
            last_cycle_status=row["last_cycle_status"],
            last_error=row["last_error"],
            cycle_count=int(row["cycle_count"]),
            enqueued_count=int(row["enqueued_count"]),
            claimed_count=int(row["claimed_count"]),
            sent_count=int(row["sent_count"]),
            failed_count=int(row["failed_count"]),
            dead_count=int(row["dead_count"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _alert_webhook_receipt_from_row(self, row: sqlite3.Row) -> AlertWebhookReceiptRecord:
        return AlertWebhookReceiptRecord(
            tenant_id=row["tenant_id"],
            delivery_id=row["delivery_id"],
            alert_key=row["alert_key"],
            severity=row["severity"],
            body_hash=row["body_hash"],
            signature_hash=row["signature_hash"],
            source_hash=row["source_hash"],
            user_agent_hash=row["user_agent_hash"],
            alert_count=int(row["alert_count"]),
            sample_event_count=int(row["sample_event_count"]),
            sample_run_count=int(row["sample_run_count"]),
            duplicate_count=int(row["duplicate_count"]),
            first_received_at=datetime.fromisoformat(row["first_received_at"]),
            last_received_at=datetime.fromisoformat(row["last_received_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists events (
                  id text primary key,
                  tenant_id text not null,
                  conversation_id text,
                  user_id text,
                  run_id text,
                  event_type text not null,
                  payload_json text not null,
                  created_at text not null
                )
                """
            )
            event_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(events)").fetchall()
            }
            if "run_id" not in event_columns:
                conn.execute("alter table events add column run_id text")
            conn.execute("create index if not exists idx_events_conversation on events(conversation_id)")
            conn.execute("create index if not exists idx_events_tenant_conversation on events(tenant_id, conversation_id)")
            conn.execute("create index if not exists idx_events_run_id on events(run_id)")
            conn.execute("create index if not exists idx_events_tenant_run on events(tenant_id, run_id)")
            conn.execute("create index if not exists idx_events_type on events(event_type)")
            conn.execute(
                "create index if not exists idx_events_tenant_type_created on events(tenant_id, event_type, created_at)"
            )
            conn.execute(
                "create index if not exists idx_events_tenant_type_user_created on events(tenant_id, event_type, user_id, created_at)"
            )
            conn.execute(
                "create index if not exists idx_events_tenant_type_conversation_created on events(tenant_id, event_type, conversation_id, created_at)"
            )
            conn.execute(
                """
                create table if not exists tool_idempotency (
                  scope_key text primary key,
                  argument_hash text not null,
                  status text not null,
                  result_json text,
                  created_at text not null,
                  updated_at text not null
                )
                """
            )
            conn.execute("create index if not exists idx_tool_idempotency_status on tool_idempotency(status)")
            conn.execute(
                """
                create table if not exists tool_audit_records (
                  id text primary key,
                  tenant_id text not null,
                  actor_user_id text not null,
                  request_id text not null,
                  trace_id text not null,
                  tool_name text not null,
                  argument_hash text not null,
                  status text not null,
                  latency_ms integer not null,
                  error_code text,
                  idempotency_key_hash text,
                  replayed integer not null default 0,
                  created_at text not null
                )
                """
            )
            conn.execute("create index if not exists idx_tool_audit_tenant on tool_audit_records(tenant_id)")
            conn.execute("create index if not exists idx_tool_audit_trace on tool_audit_records(trace_id)")
            conn.execute("create index if not exists idx_tool_audit_tool on tool_audit_records(tool_name)")
            conn.execute(
                "create index if not exists idx_tool_audit_tenant_created on tool_audit_records(tenant_id, created_at)"
            )
            conn.execute(
                "create index if not exists idx_tool_audit_tenant_tool_created on tool_audit_records(tenant_id, tool_name, created_at)"
            )
            conn.execute(
                "create index if not exists idx_tool_audit_tenant_status_created on tool_audit_records(tenant_id, status, created_at)"
            )
            conn.execute(
                "create index if not exists idx_tool_audit_tenant_error_created on tool_audit_records(tenant_id, error_code, created_at)"
            )
            conn.execute(
                """
                create table if not exists event_store_operations (
                  id text primary key,
                  tenant_id text not null,
                  actor_user_id text not null,
                  operation text not null,
                  status text not null,
                  summary_json text not null,
                  created_at text not null
                )
                """
            )
            operation_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(event_store_operations)").fetchall()
            }
            for column_name, column_type in {
                "actor_user_id": "text not null default ''",
                "operation": "text not null default ''",
                "status": "text not null default 'completed'",
                "summary_json": "text not null default '{}'",
                "created_at": "text not null default ''",
            }.items():
                if column_name not in operation_columns:
                    conn.execute(f"alter table event_store_operations add column {column_name} {column_type}")
            conn.execute(
                "create index if not exists idx_event_store_operations_tenant_created on event_store_operations(tenant_id, created_at)"
            )
            conn.execute(
                "create index if not exists idx_event_store_operations_tenant_operation_created on event_store_operations(tenant_id, operation, created_at)"
            )
            conn.execute(
                "create index if not exists idx_event_store_operations_tenant_status_created on event_store_operations(tenant_id, status, created_at)"
            )
            conn.execute(
                """
                create table if not exists operations_automation_executions (
                  id text primary key,
                  tenant_id text not null,
                  actor_user_id text not null,
                  action_id text not null,
                  action_kind text not null,
                  title text not null,
                  status text not null,
                  safe_to_auto_execute integer not null,
                  command_method text not null,
                  command_path text not null,
                  command_query_json text not null,
                  command_body_keys_json text not null,
                  command_body_hash text,
                  command_fingerprint text not null,
                  result_summary text not null,
                  error_detail text,
                  source text not null,
                  created_at text not null
                )
                """
            )
            execution_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(operations_automation_executions)").fetchall()
            }
            for column_name, column_type in {
                "actor_user_id": "text not null default ''",
                "action_id": "text not null default ''",
                "action_kind": "text not null default ''",
                "title": "text not null default ''",
                "status": "text not null default 'completed'",
                "safe_to_auto_execute": "integer not null default 0",
                "command_method": "text not null default ''",
                "command_path": "text not null default ''",
                "command_query_json": "text not null default '{}'",
                "command_body_keys_json": "text not null default '[]'",
                "command_body_hash": "text",
                "command_fingerprint": "text not null default ''",
                "result_summary": "text not null default ''",
                "error_detail": "text",
                "source": "text not null default 'api'",
                "created_at": "text not null default ''",
            }.items():
                if column_name not in execution_columns:
                    conn.execute(
                        f"alter table operations_automation_executions add column {column_name} {column_type}"
                    )
            conn.execute(
                "create index if not exists idx_ops_automation_exec_tenant_created on operations_automation_executions(tenant_id, created_at)"
            )
            conn.execute(
                "create index if not exists idx_ops_automation_exec_tenant_kind_created on operations_automation_executions(tenant_id, action_kind, created_at)"
            )
            conn.execute(
                "create index if not exists idx_ops_automation_exec_tenant_status_created on operations_automation_executions(tenant_id, status, created_at)"
            )
            conn.execute(
                "create index if not exists idx_ops_automation_exec_tenant_actor_created on operations_automation_executions(tenant_id, actor_user_id, created_at)"
            )
            conn.execute(
                """
                create table if not exists event_store_operation_locks (
                  tenant_id text not null,
                  lock_name text not null,
                  owner_id text not null,
                  operation text not null,
                  acquired_at text not null,
                  expires_at text not null,
                  primary key (tenant_id, lock_name)
                )
                """
            )
            lock_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(event_store_operation_locks)").fetchall()
            }
            for column_name, column_type in {
                "owner_id": "text not null default ''",
                "operation": "text not null default ''",
                "acquired_at": "text not null default ''",
                "expires_at": "text not null default ''",
            }.items():
                if column_name not in lock_columns:
                    conn.execute(f"alter table event_store_operation_locks add column {column_name} {column_type}")
            conn.execute(
                "create index if not exists idx_event_store_operation_locks_expires on event_store_operation_locks(expires_at)"
            )
            conn.execute(
                """
                create table if not exists api_request_nonces (
                  tenant_id text not null,
                  actor_user_id text not null,
                  nonce text not null,
                  request_hash text not null,
                  created_at text not null,
                  expires_at text not null,
                  primary key (tenant_id, actor_user_id, nonce)
                )
                """
            )
            conn.execute("create index if not exists idx_api_request_nonces_expires on api_request_nonces(expires_at)")
            conn.execute(
                """
                create table if not exists api_rate_limits (
                  bucket_key text primary key,
                  tokens real not null,
                  updated_at real not null
                )
                """
            )
            conn.execute("create index if not exists idx_api_rate_limits_updated on api_rate_limits(updated_at)")
            conn.execute(
                """
                create table if not exists alert_delivery_outbox (
                  id text primary key,
                  tenant_id text not null,
                  alert_key text not null,
                  severity text not null,
                  channel text not null,
                  destination_hash text not null,
                  status text not null,
                  alert_first_seen_at text not null,
                  alert_last_seen_at text not null,
                  alert_count integer not null,
                  reason text not null,
                  sample_event_ids_json text not null,
                  sample_run_ids_json text not null,
                  payload_hash text not null,
                  attempt_count integer not null default 0,
                  next_attempt_at text,
                  last_attempt_at text,
                  delivered_at text,
                  dead_lettered_at text,
                  locked_until text,
                  locked_by text,
                  operator_action text,
                  operator_action_at text,
                  operator_action_by text,
                  operator_action_note text,
                  response_status_code integer,
                  last_error text,
                  created_at text not null,
                  updated_at text not null,
                  unique (tenant_id, alert_key, alert_last_seen_at, destination_hash)
                )
                """
            )
            alert_delivery_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_delivery_outbox)").fetchall()
            }
            alert_delivery_missing_columns = {
                "next_attempt_at": "text",
                "dead_lettered_at": "text",
                "locked_until": "text",
                "locked_by": "text",
                "operator_action": "text",
                "operator_action_at": "text",
                "operator_action_by": "text",
                "operator_action_note": "text",
            }
            for column_name, column_type in alert_delivery_missing_columns.items():
                if column_name not in alert_delivery_columns:
                    conn.execute(f"alter table alert_delivery_outbox add column {column_name} {column_type}")
            conn.execute("create index if not exists idx_alert_delivery_tenant on alert_delivery_outbox(tenant_id)")
            conn.execute("create index if not exists idx_alert_delivery_alert on alert_delivery_outbox(alert_key)")
            conn.execute("create index if not exists idx_alert_delivery_status on alert_delivery_outbox(status)")
            conn.execute("create index if not exists idx_alert_delivery_due on alert_delivery_outbox(tenant_id, status, next_attempt_at)")
            conn.execute("create index if not exists idx_alert_delivery_lock on alert_delivery_outbox(tenant_id, locked_until)")
            conn.execute(
                "create index if not exists idx_alert_delivery_tenant_status_delivered on alert_delivery_outbox(tenant_id, status, delivered_at)"
            )
            conn.execute(
                "create index if not exists idx_alert_delivery_tenant_status_created on alert_delivery_outbox(tenant_id, status, created_at)"
            )
            conn.execute(
                """
                create table if not exists alert_dispatcher_heartbeats (
                  tenant_id text not null,
                  worker_id text not null,
                  status text not null,
                  last_seen_at text not null,
                  last_cycle_started_at text,
                  last_cycle_completed_at text,
                  last_cycle_status text,
                  last_error text,
                  cycle_count integer not null default 0,
                  enqueued_count integer not null default 0,
                  claimed_count integer not null default 0,
                  sent_count integer not null default 0,
                  failed_count integer not null default 0,
                  dead_count integer not null default 0,
                  created_at text not null,
                  updated_at text not null,
                  primary key (tenant_id, worker_id)
                )
                """
            )
            heartbeat_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_dispatcher_heartbeats)").fetchall()
            }
            heartbeat_missing_columns = {
                "status": "text not null default 'unknown'",
                "last_seen_at": "text not null default ''",
                "last_cycle_started_at": "text",
                "last_cycle_completed_at": "text",
                "last_cycle_status": "text",
                "last_error": "text",
                "cycle_count": "integer not null default 0",
                "enqueued_count": "integer not null default 0",
                "claimed_count": "integer not null default 0",
                "sent_count": "integer not null default 0",
                "failed_count": "integer not null default 0",
                "dead_count": "integer not null default 0",
                "created_at": "text not null default ''",
                "updated_at": "text not null default ''",
            }
            for column_name, column_type in heartbeat_missing_columns.items():
                if column_name not in heartbeat_columns:
                    conn.execute(f"alter table alert_dispatcher_heartbeats add column {column_name} {column_type}")
            conn.execute(
                "create index if not exists idx_alert_dispatcher_heartbeats_tenant_seen on alert_dispatcher_heartbeats(tenant_id, last_seen_at)"
            )
            conn.execute(
                """
                create table if not exists alert_webhook_receipts (
                  tenant_id text not null,
                  delivery_id text not null,
                  alert_key text not null,
                  severity text not null,
                  body_hash text not null,
                  signature_hash text not null,
                  source_hash text,
                  user_agent_hash text,
                  alert_count integer not null default 0,
                  sample_event_count integer not null default 0,
                  sample_run_count integer not null default 0,
                  duplicate_count integer not null default 0,
                  first_received_at text not null,
                  last_received_at text not null,
                  created_at text not null,
                  updated_at text not null,
                  primary key (tenant_id, delivery_id)
                )
                """
            )
            receipt_columns = {
                item["name"]
                for item in conn.execute("pragma table_info(alert_webhook_receipts)").fetchall()
            }
            receipt_missing_columns = {
                "alert_key": "text not null default ''",
                "severity": "text not null default ''",
                "body_hash": "text not null default ''",
                "signature_hash": "text not null default ''",
                "source_hash": "text",
                "user_agent_hash": "text",
                "alert_count": "integer not null default 0",
                "sample_event_count": "integer not null default 0",
                "sample_run_count": "integer not null default 0",
                "duplicate_count": "integer not null default 0",
                "first_received_at": "text not null default ''",
                "last_received_at": "text not null default ''",
                "created_at": "text not null default ''",
                "updated_at": "text not null default ''",
            }
            for column_name, column_type in receipt_missing_columns.items():
                if column_name not in receipt_columns:
                    conn.execute(f"alter table alert_webhook_receipts add column {column_name} {column_type}")
            conn.execute(
                "create index if not exists idx_alert_webhook_receipts_tenant_received on alert_webhook_receipts(tenant_id, last_received_at)"
            )
            conn.execute(
                "create index if not exists idx_alert_webhook_receipts_tenant_alert on alert_webhook_receipts(tenant_id, alert_key)"
            )

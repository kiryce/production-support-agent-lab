from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from support_agent_lab.models import (
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


EVAL_GATE_EVENT_TYPE = "eval.gate.completed"
ALERT_DELIVERY_ENQUEUED_EVENT_TYPE = "monitor.alert.delivery.enqueued"
ALERT_DELIVERY_ATTEMPTED_EVENT_TYPE = "monitor.alert.delivery.attempted"
ALERT_DELIVERY_REQUEUED_EVENT_TYPE = "monitor.alert.delivery.requeued"
ALERT_DELIVERY_CLOSED_EVENT_TYPE = "monitor.alert.delivery.closed"
MEMORY_REPLAY_EVENT_TYPES = ("message.user", "message.assistant", "agent.run.completed")


class AlertDeliveryLockLostError(RuntimeError):
    """Raised when a dispatcher tries to complete a delivery it no longer owns."""


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _rounded_average(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


class SQLiteEventStore:
    """Append-only local event store for learning persistence boundaries.

    This is intentionally small and dependency-free. It teaches the shape of a
    production event log without forcing learners to run Postgres on day one.
    """

    def __init__(self, path: str | Path, tool_idempotency_lease_seconds: int = 300) -> None:
        self.path = Path(path)
        self.tool_idempotency_lease_seconds = tool_idempotency_lease_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            params.extend([query_like, query_like, query_like, query_like, query_like, query_like])
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
        events = self.list_events(
            tenant_id=tenant_id,
            event_type="monitor.alert.triaged",
            limit=limit,
        )
        triage_events = [
            MonitorAlertTriageEvent.model_validate(event.payload)
            for event in events
        ]
        if alert_key is None:
            return triage_events
        return [event for event in triage_events if event.alert_key == alert_key]

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
        with self._connect() as conn:
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

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
                "create index if not exists idx_alert_delivery_tenant_status_created on alert_delivery_outbox(tenant_id, status, created_at)"
            )

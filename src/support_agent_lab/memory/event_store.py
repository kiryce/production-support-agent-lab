from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from support_agent_lab.models import (
    AgentRunTrace,
    Message,
    MonitorAlertTriageEvent,
    MonitorEvent,
    ToolResult,
    ToolStatus,
    new_id,
    utc_now,
)
from support_agent_lab.tools.registry import IdempotencyDecision, ToolAuditRecord


class StoredEvent(BaseModel):
    id: str
    tenant_id: str
    conversation_id: str | None = None
    user_id: str | None = None
    run_id: str | None = None
    event_type: str
    payload: dict[str, Any]
    created_at: str


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
        limit: int = 100,
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
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by created_at asc, rowid asc limit ?"
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
        limit: int = 500,
    ) -> list[MonitorEvent]:
        events = self.list_events(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type="monitor.reviewed",
            limit=limit,
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

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from support_agent_lab.models import AgentRunTrace, Message, MonitorEvent, new_id, utc_now


class StoredEvent(BaseModel):
    id: str
    tenant_id: str
    conversation_id: str | None = None
    user_id: str | None = None
    event_type: str
    payload: dict[str, Any]
    created_at: str


class SQLiteEventStore:
    """Append-only local event store for learning persistence boundaries.

    This is intentionally small and dependency-free. It teaches the shape of a
    production event log without forcing learners to run Postgres on day one.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
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
            event_type="agent.run.completed",
            payload=trace.model_dump(mode="json"),
        )

    def append_monitor_event(self, event: MonitorEvent, tenant_id: str = "demo_tenant") -> StoredEvent:
        return self.append(
            tenant_id=tenant_id,
            conversation_id=event.conversation_id,
            user_id=None,
            event_type="monitor.reviewed",
            payload=event.model_dump(mode="json"),
        )

    def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> StoredEvent:
        event = StoredEvent(
            id=new_id("evt"),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            event_type=event_type,
            payload=payload,
            created_at=utc_now().isoformat(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into events (
                  id, tenant_id, conversation_id, user_id, event_type, payload_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.tenant_id,
                    event.conversation_id,
                    event.user_id,
                    event.event_type,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    event.created_at,
                ),
            )
        return event

    def list_events(
        self,
        *,
        conversation_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[StoredEvent]:
        sql = "select id, tenant_id, conversation_id, user_id, event_type, payload_json, created_at from events"
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
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
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

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
                "event_type",
                "payload_json",
                "created_at",
            }
            missing = sorted(required - columns)
            if missing:
                raise RuntimeError(f"events table missing columns: {', '.join(missing)}")
            conn.execute("begin immediate")
            conn.execute(
                """
                insert into events (
                  id, tenant_id, conversation_id, user_id, event_type, payload_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("ready"),
                    "readiness",
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

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists events (
                  id text primary key,
                  tenant_id text not null,
                  conversation_id text,
                  user_id text,
                  event_type text not null,
                  payload_json text not null,
                  created_at text not null
                )
                """
            )
            conn.execute("create index if not exists idx_events_conversation on events(conversation_id)")
            conn.execute("create index if not exists idx_events_type on events(event_type)")

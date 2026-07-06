from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from support_agent_lab.memory.event_store import (
    EventStoreOperationRecord,
    OperationsAutomationExecutionRecord,
    SQLiteEventStore,
    StoredEvent,
)
from support_agent_lab.models import ToolStatus
from support_agent_lab.tools.registry import ToolAuditRecord


AUDIT_EXPORT_MEDIA_TYPE = "application/x-ndjson"
AUDIT_EXPORT_SCHEMA_VERSION = "audit_export.v1"


@dataclass(frozen=True)
class AuditExportCursor:
    created_at: str
    record_type: str
    id: str
    source_sequence: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "created_at": self.created_at,
            "record_type": self.record_type,
            "id": self.id,
            "source_sequence": self.source_sequence,
        }


def audit_export_rows(
    *,
    event_store: SQLiteEventStore | None,
    tenant_id: str,
    include_events: bool,
    include_tool_audit: bool,
    include_event_store_operations: bool,
    include_operations_automation_executions: bool,
    event_type: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    after_cursors: Mapping[str, Any] | None = None,
    limit: int = 1000,
    order: Literal["asc", "desc"] = "asc",
) -> list[dict[str, Any]]:
    if not event_store:
        return []
    cursor_by_type = coerce_audit_export_cursors(after_cursors)
    rows: list[dict[str, Any]] = []
    if include_events:
        rows.extend(
            _list_events_for_export(
                event_store=event_store,
                tenant_id=tenant_id,
                event_type=event_type,
                created_after=created_after,
                created_before=created_before,
                after_cursor=cursor_by_type.get("event"),
                limit=limit,
                order=order,
            )
        )
    if include_tool_audit:
        rows.extend(
            _list_tool_audit_for_export(
                event_store=event_store,
                tenant_id=tenant_id,
                created_after=created_after,
                created_before=created_before,
                after_cursor=cursor_by_type.get("tool_audit"),
                limit=limit,
                order=order,
            )
        )
    if include_event_store_operations:
        rows.extend(
            _list_event_store_operations_for_export(
                event_store=event_store,
                tenant_id=tenant_id,
                created_after=created_after,
                created_before=created_before,
                after_cursor=cursor_by_type.get("event_store_operation"),
                limit=limit,
                order=order,
            )
        )
    if include_operations_automation_executions:
        rows.extend(
            _list_operations_automation_executions_for_export(
                event_store=event_store,
                tenant_id=tenant_id,
                created_after=created_after,
                created_before=created_before,
                after_cursor=cursor_by_type.get("operations_automation_execution"),
                limit=limit,
                order=order,
            )
        )
    reverse = order == "desc"
    rows.sort(key=audit_export_row_sort_key, reverse=reverse)
    return rows[:limit]


def coerce_audit_export_cursor(value: AuditExportCursor | Mapping[str, Any] | None) -> AuditExportCursor | None:
    if value is None:
        return None
    if isinstance(value, AuditExportCursor):
        return value
    created_at = _cursor_text(value.get("created_at"))
    record_type = _cursor_text(value.get("record_type"))
    record_id = _cursor_text(value.get("id"))
    source_sequence = _cursor_int(value.get("source_sequence"))
    if not created_at or not record_type or not record_id or source_sequence is None:
        return None
    return AuditExportCursor(
        created_at=created_at,
        record_type=record_type,
        id=record_id,
        source_sequence=source_sequence,
    )


def coerce_audit_export_cursors(value: Mapping[str, Any] | None) -> dict[str, AuditExportCursor]:
    if not isinstance(value, Mapping):
        return {}
    single = coerce_audit_export_cursor(value)
    if single:
        return {single.record_type: single}
    result: dict[str, AuditExportCursor] = {}
    for key, raw_cursor in value.items():
        if isinstance(raw_cursor, AuditExportCursor):
            cursor = raw_cursor
        elif isinstance(raw_cursor, Mapping):
            cursor = coerce_audit_export_cursor(raw_cursor)
        else:
            continue
        if cursor and cursor.record_type == str(key):
            result[cursor.record_type] = cursor
    return result


def audit_export_cursor_from_row(row: Mapping[str, Any]) -> AuditExportCursor | None:
    if row.get("record_type") == "export_control":
        return None
    created_at = _cursor_text(row.get("created_at"))
    record_type = _cursor_text(row.get("record_type"))
    record_id = _cursor_text(row.get("id"))
    source_sequence = _cursor_int(row.get("source_sequence"))
    if not created_at or not record_type or not record_id or source_sequence is None:
        return None
    return AuditExportCursor(
        created_at=created_at,
        record_type=record_type,
        id=record_id,
        source_sequence=source_sequence,
    )


def audit_export_cursor_from_rows(rows: Iterable[Mapping[str, Any]]) -> AuditExportCursor | None:
    cursors = [cursor for row in rows if (cursor := audit_export_cursor_from_row(row))]
    return max(cursors, key=audit_export_cursor_sort_key) if cursors else None


def audit_export_cursors_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, AuditExportCursor]:
    result: dict[str, AuditExportCursor] = {}
    for row in rows:
        cursor = audit_export_cursor_from_row(row)
        if not cursor:
            continue
        current = result.get(cursor.record_type)
        if current is None or audit_export_cursor_sort_key(cursor) > audit_export_cursor_sort_key(current):
            result[cursor.record_type] = cursor
    return result


def audit_export_cursor_sort_key(cursor: AuditExportCursor) -> tuple[str, str, int, str]:
    return (cursor.created_at, cursor.record_type, cursor.source_sequence, cursor.id)


def audit_export_row_sort_key(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(row.get("created_at") or ""),
        str(row.get("record_type") or ""),
        _cursor_int(row.get("source_sequence")) or 0,
        str(row.get("id") or ""),
    )


def audit_event_row(event: StoredEvent) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_EXPORT_SCHEMA_VERSION,
        "record_type": "event",
        "source": "events",
        "id": event.id,
        "tenant_id": event.tenant_id,
        "event_type": event.event_type,
        "created_at": event.created_at,
        "correlation": audit_correlation(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            conversation_id=event.conversation_id,
            run_id=event.run_id,
        ),
        "payload_summary": audit_payload_summary(event.payload),
    }


def audit_tool_row(record: ToolAuditRecord) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_EXPORT_SCHEMA_VERSION,
        "record_type": "tool_audit",
        "source": "tool_audit_records",
        "id": record.id,
        "tenant_id": record.tenant_id,
        "tool_name": record.tool_name,
        "status": record.status.value,
        "latency_ms": record.latency_ms,
        "error_code": record.error_code,
        "argument_hash": record.argument_hash,
        "idempotency_key_hash": record.idempotency_key_hash,
        "replayed": record.replayed,
        "created_at": record.created_at,
        "correlation": audit_correlation(
            tenant_id=record.tenant_id,
            user_id=record.actor_user_id,
            run_id=record.trace_id,
            request_id=record.request_id,
        ),
    }


def audit_event_store_operation_row(record: EventStoreOperationRecord) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_EXPORT_SCHEMA_VERSION,
        "record_type": "event_store_operation",
        "source": "event_store_operations",
        "id": record.id,
        "tenant_id": record.tenant_id,
        "operation": record.operation,
        "status": record.status,
        "created_at": record.created_at,
        "correlation": audit_correlation(
            tenant_id=record.tenant_id,
            user_id=record.actor_user_id,
        ),
        "operation_summary": audit_operation_summary(record.summary),
    }


def audit_operations_automation_execution_row(record: OperationsAutomationExecutionRecord) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_EXPORT_SCHEMA_VERSION,
        "record_type": "operations_automation_execution",
        "source": "operations_automation_executions",
        "id": record.id,
        "tenant_id": record.tenant_id,
        "action_id_hash": audit_hash(record.action_id),
        "action_kind": record.action_kind,
        "status": record.status,
        "safe_to_auto_execute": record.safe_to_auto_execute,
        "created_at": record.created_at,
        "correlation": audit_correlation(
            tenant_id=record.tenant_id,
            user_id=record.actor_user_id,
        ),
        "command_summary": {
            "method": record.command_method,
            "path": record.command_path,
            "query": record.command_query,
            "body_keys": record.command_body_keys,
            "body_hash": record.command_body_hash,
            "fingerprint": record.command_fingerprint,
        },
        "result_summary": record.result_summary,
        "error_detail_hash": audit_hash(record.error_detail),
        "execution_source": record.source,
    }


def audit_correlation(
    *,
    tenant_id: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "tenant_id": tenant_id,
        "user_hash": audit_hash(user_id),
        "conversation_hash": audit_hash(conversation_id),
        "run_hash": audit_hash(run_id),
        "request_hash": audit_hash(request_id),
    }


def audit_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"{AUDIT_EXPORT_SCHEMA_VERSION}:{value}".encode("utf-8")).hexdigest()[:32]


def ndjson(rows: Iterable[dict[str, Any]]) -> str:
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def _list_events_for_export(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    event_type: str | None,
    created_after: str | None,
    created_before: str | None,
    after_cursor: AuditExportCursor | None,
    limit: int,
    order: Literal["asc", "desc"],
) -> list[dict[str, Any]]:
    sql = """
        select events.rowid as source_sequence,
               id, tenant_id, conversation_id, user_id, run_id, event_type, payload_json, created_at
        from events
    """
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    _append_created_window(clauses, params, created_after=created_after, created_before=created_before)
    _append_after_cursor_window(clauses, params, record_type="event", after_cursor=after_cursor)
    sql += " where " + " and ".join(clauses)
    direction = "desc" if order == "desc" else "asc"
    sql += f" order by created_at {direction}, events.rowid {direction} limit ?"
    params.append(limit)
    with event_store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = audit_event_row(
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
        )
        item["source_sequence"] = int(row["source_sequence"])
        result.append(item)
    return result


def _list_tool_audit_for_export(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    created_after: str | None,
    created_before: str | None,
    after_cursor: AuditExportCursor | None,
    limit: int,
    order: Literal["asc", "desc"],
) -> list[dict[str, Any]]:
    sql = """
        select tool_audit_records.rowid as source_sequence,
               id, tenant_id, actor_user_id, request_id, trace_id, tool_name,
               argument_hash, status, latency_ms, error_code,
               idempotency_key_hash, replayed, created_at
        from tool_audit_records
    """
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    _append_created_window(clauses, params, created_after=created_after, created_before=created_before)
    _append_after_cursor_window(clauses, params, record_type="tool_audit", after_cursor=after_cursor)
    sql += " where " + " and ".join(clauses)
    direction = "desc" if order == "desc" else "asc"
    sql += f" order by created_at {direction}, tool_audit_records.rowid {direction} limit ?"
    params.append(limit)
    with event_store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = audit_tool_row(
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
        )
        item["source_sequence"] = int(row["source_sequence"])
        result.append(item)
    return result


def _list_event_store_operations_for_export(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    created_after: str | None,
    created_before: str | None,
    after_cursor: AuditExportCursor | None,
    limit: int,
    order: Literal["asc", "desc"],
) -> list[dict[str, Any]]:
    sql = """
        select event_store_operations.rowid as source_sequence,
               id, tenant_id, actor_user_id, operation, status, summary_json, created_at
        from event_store_operations
    """
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    _append_created_window(clauses, params, created_after=created_after, created_before=created_before)
    _append_after_cursor_window(clauses, params, record_type="event_store_operation", after_cursor=after_cursor)
    sql += " where " + " and ".join(clauses)
    direction = "desc" if order == "desc" else "asc"
    sql += f" order by created_at {direction}, event_store_operations.rowid {direction} limit ?"
    params.append(limit)
    with event_store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = audit_event_store_operation_row(
            EventStoreOperationRecord(
                id=row["id"],
                tenant_id=row["tenant_id"],
                actor_user_id=row["actor_user_id"],
                operation=row["operation"],
                status=row["status"],
                summary=json.loads(row["summary_json"] or "{}"),
                created_at=row["created_at"],
            )
        )
        item["source_sequence"] = int(row["source_sequence"])
        result.append(item)
    return result


def _list_operations_automation_executions_for_export(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    created_after: str | None,
    created_before: str | None,
    after_cursor: AuditExportCursor | None,
    limit: int,
    order: Literal["asc", "desc"],
) -> list[dict[str, Any]]:
    sql = """
        select
          operations_automation_executions.rowid as source_sequence,
          id, tenant_id, actor_user_id, action_id, action_kind, title, status,
          safe_to_auto_execute, command_method, command_path, command_query_json,
          command_body_keys_json, command_body_hash, command_fingerprint,
          result_summary, error_detail, source, created_at
        from operations_automation_executions
    """
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    _append_created_window(clauses, params, created_after=created_after, created_before=created_before)
    _append_after_cursor_window(
        clauses,
        params,
        record_type="operations_automation_execution",
        after_cursor=after_cursor,
    )
    sql += " where " + " and ".join(clauses)
    direction = "desc" if order == "desc" else "asc"
    sql += f" order by created_at {direction}, operations_automation_executions.rowid {direction} limit ?"
    params.append(limit)
    with event_store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = audit_operations_automation_execution_row(event_store._operations_automation_execution_from_row(row))
        item["source_sequence"] = int(row["source_sequence"])
        result.append(item)
    return result


def _append_created_window(
    clauses: list[str],
    params: list[Any],
    *,
    created_after: str | None,
    created_before: str | None,
) -> None:
    if created_after:
        clauses.append("created_at >= ?")
        params.append(created_after)
    if created_before:
        clauses.append("created_at <= ?")
        params.append(created_before)


def _append_after_cursor_window(
    clauses: list[str],
    params: list[Any],
    *,
    record_type: str,
    after_cursor: AuditExportCursor | None,
) -> None:
    if not after_cursor or after_cursor.record_type != record_type:
        return
    clauses.append(
        "("
        "created_at > ? "
        "or (created_at = ? and rowid > ?)"
        ")"
    )
    params.extend(
        [
            after_cursor.created_at,
            after_cursor.created_at,
            after_cursor.source_sequence,
        ]
    )


def _cursor_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cursor_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


_SAFE_OPERATION_SUMMARY_STRING_KEYS = {
    "active_acquired_at",
    "active_expires_at",
    "active_operation",
    "backup_file",
    "batch_id",
    "content_sha256",
    "created_after",
    "created_before",
    "database_file",
    "error_type",
    "event_type",
    "export_schema_version",
    "exported_at",
    "first_record_created_at",
    "latest_record_created_at",
    "lock_name",
    "manifest_file",
    "operation",
    "order",
    "output_file",
    "schema_version",
    "source",
    "started_at",
    "status",
}
_SAFE_OPERATION_SUMMARY_SUFFIXES = (
    "_at",
    "_count",
    "_file",
    "_hash",
    "_id",
    "_ms",
    "_seconds",
    "_sha256",
    "_status",
    "_type",
    "_version",
)
_SENSITIVE_OPERATION_SUMMARY_KEY_PARTS = (
    "actor",
    "answer",
    "body",
    "comment",
    "command",
    "content",
    "detail",
    "error",
    "owner",
    "path",
    "payload",
    "query",
    "text",
    "token",
    "user",
)


def audit_operation_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    return _sanitize_operation_summary_mapping(summary)


def _sanitize_operation_summary_mapping(summary: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in summary.items():
        key_text = str(key)[:120]
        if isinstance(value, Mapping):
            if _is_sensitive_operation_summary_key(key_text):
                sanitized[f"{key_text}_hash"] = _audit_json_hash(value)
            else:
                sanitized[key_text] = _sanitize_operation_summary_mapping(value)
            continue
        if isinstance(value, list):
            if _is_sensitive_operation_summary_key(key_text):
                sanitized[f"{key_text}_hash"] = _audit_json_hash(value)
            else:
                sanitized[key_text] = [_sanitize_operation_summary_value(key_text, item) for item in value[:50]]
            continue
        sanitized.update(_sanitize_operation_summary_scalar(key_text, value))
    return sanitized


def _sanitize_operation_summary_value(key: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        return _sanitize_operation_summary_mapping(value)
    if isinstance(value, list):
        return [_sanitize_operation_summary_value(key, item) for item in value[:50]]
    return next(iter(_sanitize_operation_summary_scalar(key, value).values()))


def _sanitize_operation_summary_scalar(key: str, value: Any) -> dict[str, Any]:
    if value is None or isinstance(value, (bool, int, float)):
        return {key: value}
    if _is_safe_operation_summary_string_key(key):
        return {key: str(value)[:500]}
    safe_key = key if key.endswith("_hash") else f"{key}_hash"
    return {safe_key: audit_hash(str(value))}


def _is_safe_operation_summary_string_key(key: str) -> bool:
    if key in _SAFE_OPERATION_SUMMARY_STRING_KEYS:
        return True
    if key.endswith("_path_hash"):
        return True
    if key.endswith(_SAFE_OPERATION_SUMMARY_SUFFIXES) and not _is_sensitive_operation_summary_key(key):
        return True
    return False


def _is_sensitive_operation_summary_key(key: str) -> bool:
    lowered = key.lower()
    if lowered.endswith("_hash") or lowered.endswith("_path_hash"):
        return False
    return any(part in lowered for part in _SENSITIVE_OPERATION_SUMMARY_KEY_PARTS)


def _audit_json_hash(value: Any) -> str | None:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return audit_hash(payload)


def audit_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = payload.get("event_type") or payload.get("type")
    summary: dict[str, Any] = {}
    for key in (
        "status",
        "rating",
        "reasons",
        "risk_level",
        "failure_types",
        "tool_name",
        "error_code",
        "route",
        "intent",
    ):
        if key in payload:
            summary[key] = payload[key]
    if "role" in payload:
        summary["role"] = payload["role"]
    if "event_type" in payload:
        summary["event_type"] = event_type
    if "payload" in payload and isinstance(payload["payload"], dict):
        nested = payload["payload"]
        for key in ("status", "rating", "risk_level", "failure_types"):
            if key in nested and key not in summary:
                summary[key] = nested[key]
    if "metadata" in payload and isinstance(payload["metadata"], dict):
        safe_metadata = {
            key: value
            for key, value in payload["metadata"].items()
            if key in {"source", "agent_version", "prompt_version"} and isinstance(value, (str, int, float, bool))
        }
        if safe_metadata:
            summary["metadata"] = safe_metadata
    return summary

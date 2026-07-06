from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal

from support_agent_lab.memory.event_store import (
    EventStoreOperationRecord,
    OperationsAutomationExecutionRecord,
    SQLiteEventStore,
    StoredEvent,
)
from support_agent_lab.tools.registry import ToolAuditRecord


AUDIT_EXPORT_MEDIA_TYPE = "application/x-ndjson"
AUDIT_EXPORT_SCHEMA_VERSION = "audit_export.v1"


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
    limit: int = 1000,
    order: Literal["asc", "desc"] = "asc",
) -> list[dict[str, Any]]:
    if not event_store:
        return []
    rows: list[dict[str, Any]] = []
    if include_events:
        rows.extend(
            audit_event_row(event)
            for event in event_store.list_events(
                tenant_id=tenant_id,
                event_type=event_type,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                order=order,
            )
        )
    if include_tool_audit:
        rows.extend(
            audit_tool_row(record)
            for record in event_store.list_tool_audit_records(
                tenant_id=tenant_id,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                order=order,
            )
        )
    if include_event_store_operations:
        rows.extend(
            audit_event_store_operation_row(record)
            for record in event_store.list_event_store_operations(
                tenant_id=tenant_id,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                order=order,
            )
        )
    if include_operations_automation_executions:
        rows.extend(
            audit_operations_automation_execution_row(record)
            for record in event_store.list_operations_automation_executions(
                tenant_id=tenant_id,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                order=order,
            )
        )
    reverse = order == "desc"
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=reverse)
    return rows[:limit]


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
        "operation_summary": record.summary,
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

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from support_agent_lab.audit.export import (
    AuditExportCursor,
    audit_export_cursor_from_rows,
    audit_export_rows,
    audit_hash,
    coerce_audit_export_cursor,
    ndjson,
)
from support_agent_lab.memory.event_store import EventStoreOperationLockConflict, SQLiteEventStore
from support_agent_lab.models import new_id, utc_now


AUDIT_EXPORT_BATCH_OPERATION = "audit_export_batch"
AUDIT_EXPORT_BATCH_LOCK_NAME = "event_store_maintenance"
AUDIT_EXPORT_BATCH_SCHEMA_VERSION = "audit_export_batch.v1"
AUDIT_EXPORT_BATCH_SUMMARY_SCHEMA_VERSION = "audit_export_batch_summary.v1"
EVENT_STORE_OPERATION_SUMMARY_VERSION = "event_store_operation_summary.v1"


class AuditExportBatchReport(BaseModel):
    schema_version: str = AUDIT_EXPORT_BATCH_SCHEMA_VERSION
    status: Literal["completed", "failed", "rejected"] = "completed"
    batch_id: str
    tenant_id: str
    started_at: datetime
    exported_at: datetime
    output_file: str | None = None
    output_path_hash: str | None = None
    manifest_file: str | None = None
    manifest_path_hash: str | None = None
    record_count: int = 0
    record_type_counts: dict[str, int] = Field(default_factory=dict)
    bytes_written: int = 0
    content_sha256: str | None = None
    first_record_created_at: str | None = None
    latest_record_created_at: str | None = None
    partial: bool = False
    incremental: bool = False
    previous_cursor: dict[str, str] | None = None
    high_water_cursor: dict[str, str] | None = None
    cursor_advance_allowed: bool = False
    operation_id: str | None = None
    error_type: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AuditExportBatchSummary(BaseModel):
    schema_version: str = AUDIT_EXPORT_BATCH_SUMMARY_SCHEMA_VERSION
    status: Literal["fresh", "stale", "missing", "failed", "unknown"]
    stale_after_seconds: int
    total_batch_count: int = 0
    completed_batch_count: int = 0
    failed_batch_count: int = 0
    last_status: str | None = None
    last_exported_at: datetime | None = None
    last_record_count: int = 0
    last_record_type_counts: dict[str, int] = Field(default_factory=dict)
    last_bytes_written: int = 0
    last_output_file: str | None = None
    last_manifest_file: str | None = None
    last_content_sha256: str | None = None
    last_partial: bool = False
    last_high_water_cursor: dict[str, str] | None = None
    last_cursor_advance_allowed: bool = False
    last_error_type: str | None = None


@dataclass(frozen=True)
class AuditExportBatchOptions:
    include_events: bool = True
    include_tool_audit: bool = True
    include_event_store_operations: bool = True
    include_operations_automation_executions: bool = True
    event_type: str | None = None
    created_after: str | None = None
    created_before: str | None = None
    limit: int = 1000
    order: Literal["asc", "desc"] = "asc"
    incremental: bool = True


def run_audit_export_batch(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    output_dir: str | Path,
    actor_user_id: str = "audit_export_worker",
    owner_id: str | None = None,
    options: AuditExportBatchOptions | None = None,
    lock_ttl_seconds: int = 1800,
    now: datetime | None = None,
) -> AuditExportBatchReport:
    effective_now = _ensure_datetime(now or utc_now())
    opts = options or AuditExportBatchOptions()
    output_root = Path(output_dir)
    batch_id = new_id("audit_batch")
    try:
        with event_store.event_store_operation_lock(
            tenant_id=tenant_id,
            lock_name=AUDIT_EXPORT_BATCH_LOCK_NAME,
            operation=AUDIT_EXPORT_BATCH_OPERATION,
            owner_id=owner_id or actor_user_id,
            ttl_seconds=lock_ttl_seconds,
            now=effective_now,
        ):
            previous_cursor = _latest_completed_export_cursor(
                event_store=event_store,
                tenant_id=tenant_id,
                options=opts,
            )
            report = _write_audit_export_batch(
                event_store=event_store,
                tenant_id=tenant_id,
                output_root=output_root,
                options=opts,
                started_at=effective_now,
                batch_id=batch_id,
                previous_cursor=previous_cursor,
            )
            operation = event_store.append_event_store_operation(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                operation=AUDIT_EXPORT_BATCH_OPERATION,
                status="completed",
                summary=_operation_summary(report=report, options=opts, source="worker"),
                created_at=effective_now.isoformat(),
            )
            return report.model_copy(update={"operation_id": operation.id})
    except EventStoreOperationLockConflict as exc:
        report = AuditExportBatchReport(
            status="rejected",
            batch_id=batch_id,
            tenant_id=tenant_id,
            started_at=effective_now,
            exported_at=effective_now,
            incremental=_incremental_enabled(opts),
            error_type=exc.__class__.__name__,
        )
        operation = event_store.append_event_store_operation(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            operation=AUDIT_EXPORT_BATCH_OPERATION,
            status="rejected",
            summary={
                **_operation_summary(report=report, options=opts, source="worker"),
                **_lock_conflict_summary(exc),
            },
            created_at=effective_now.isoformat(),
        )
        return report.model_copy(update={"operation_id": operation.id})
    except Exception as exc:
        report = AuditExportBatchReport(
            status="failed",
            batch_id=batch_id,
            tenant_id=tenant_id,
            started_at=effective_now,
            exported_at=effective_now,
            output_path_hash=audit_hash(_safe_resolved_path(output_root)),
            incremental=_incremental_enabled(opts),
            error_type=exc.__class__.__name__,
        )
        operation = event_store.append_event_store_operation(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            operation=AUDIT_EXPORT_BATCH_OPERATION,
            status="failed",
            summary={
                **_operation_summary(report=report, options=opts, source="worker"),
                "error_type": exc.__class__.__name__,
            },
            created_at=effective_now.isoformat(),
        )
        return report.model_copy(update={"operation_id": operation.id})


def summarize_audit_export_batches(
    *,
    event_store: SQLiteEventStore | None,
    tenant_id: str,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> AuditExportBatchSummary:
    if not event_store:
        return AuditExportBatchSummary(status="unknown", stale_after_seconds=stale_after_seconds)
    records = event_store.list_event_store_operations(
        tenant_id=tenant_id,
        operation=AUDIT_EXPORT_BATCH_OPERATION,
        limit=100,
        order="desc",
    )
    if not records:
        return AuditExportBatchSummary(status="missing", stale_after_seconds=stale_after_seconds)
    latest = records[0]
    latest_summary = latest.summary if isinstance(latest.summary, dict) else {}
    latest_at = _parse_datetime(latest_summary.get("exported_at") or latest.created_at)
    effective_now = _ensure_datetime(now or utc_now())
    stale_cutoff = effective_now - timedelta(seconds=max(1, stale_after_seconds))
    completed_count = sum(1 for record in records if record.status == "completed")
    failed_count = sum(1 for record in records if record.status in {"failed", "rejected"})
    if latest.status in {"failed", "rejected"}:
        status: Literal["fresh", "stale", "missing", "failed", "unknown"] = "failed"
    elif latest_at and latest_at < stale_cutoff:
        status = "stale"
    elif latest_at:
        status = "fresh"
    else:
        status = "unknown"
    return AuditExportBatchSummary(
        status=status,
        stale_after_seconds=stale_after_seconds,
        total_batch_count=len(records),
        completed_batch_count=completed_count,
        failed_batch_count=failed_count,
        last_status=latest.status,
        last_exported_at=latest_at,
        last_record_count=int(latest_summary.get("record_count") or 0),
        last_record_type_counts=_dict_of_ints(latest_summary.get("record_type_counts")),
        last_bytes_written=int(latest_summary.get("bytes_written") or 0),
        last_output_file=_optional_text(latest_summary.get("output_file")),
        last_manifest_file=_optional_text(latest_summary.get("manifest_file")),
        last_content_sha256=_optional_text(latest_summary.get("content_sha256")),
        last_partial=bool(latest_summary.get("partial")),
        last_high_water_cursor=_optional_cursor_dict(latest_summary.get("high_water_cursor")),
        last_cursor_advance_allowed=bool(latest_summary.get("cursor_advance_allowed")),
        last_error_type=_optional_text(latest_summary.get("error_type")),
    )


def sanitize_audit_export_batch_report(report: AuditExportBatchReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "status": report.status,
        "batch_id": report.batch_id,
        "tenant_id": report.tenant_id,
        "started_at": report.started_at.isoformat(),
        "exported_at": report.exported_at.isoformat(),
        "output_file": report.output_file,
        "output_path_hash": report.output_path_hash,
        "manifest_file": report.manifest_file,
        "manifest_path_hash": report.manifest_path_hash,
        "record_count": report.record_count,
        "record_type_counts": report.record_type_counts,
        "bytes_written": report.bytes_written,
        "content_sha256": report.content_sha256,
        "first_record_created_at": report.first_record_created_at,
        "latest_record_created_at": report.latest_record_created_at,
        "partial": report.partial,
        "incremental": report.incremental,
        "previous_cursor": report.previous_cursor,
        "high_water_cursor": report.high_water_cursor,
        "cursor_advance_allowed": report.cursor_advance_allowed,
        "operation_id": report.operation_id,
        "error_type": report.error_type,
        "warnings": report.warnings,
    }


def _write_audit_export_batch(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    output_root: Path,
    options: AuditExportBatchOptions,
    started_at: datetime,
    batch_id: str,
    previous_cursor: AuditExportCursor | None,
) -> AuditExportBatchReport:
    _validate_options(options)
    output_root.mkdir(parents=True, exist_ok=True)
    resolved_root = output_root.resolve()
    incremental = _incremental_enabled(options)
    rows = audit_export_rows(
        event_store=event_store,
        tenant_id=tenant_id,
        include_events=options.include_events,
        include_tool_audit=options.include_tool_audit,
        include_event_store_operations=options.include_event_store_operations,
        include_operations_automation_executions=options.include_operations_automation_executions,
        event_type=options.event_type,
        created_after=options.created_after,
        created_before=options.created_before,
        after_cursor=previous_cursor if incremental else None,
        limit=options.limit + 1,
        order=options.order,
    )
    partial = len(rows) > options.limit
    source_rows = rows[: options.limit]
    payload_rows = list(source_rows)
    candidate_high_water = audit_export_cursor_from_rows(source_rows)
    high_water_cursor = candidate_high_water if candidate_high_water and not partial else None
    payload = ndjson(payload_rows).encode("utf-8")
    content_sha256 = hashlib.sha256(payload).hexdigest()
    exported_at = utc_now()
    record_type_counts = _record_type_counts(payload_rows)
    if partial:
        payload_rows.append(
            {
                "schema_version": AUDIT_EXPORT_BATCH_SCHEMA_VERSION,
                "record_type": "export_control",
                "source": "audit_export_batch",
                "tenant_id": tenant_id,
                "created_at": exported_at.isoformat(),
                "partial": True,
                "detail": "Export reached limit; rerun with a narrower window or higher limit before advancing downstream watermarks.",
            }
        )
        payload = ndjson(payload_rows).encode("utf-8")
        content_sha256 = hashlib.sha256(payload).hexdigest()
        record_type_counts = _record_type_counts(payload_rows)
    stem = _batch_stem(tenant_id=tenant_id, exported_at=exported_at)
    output_path = resolved_root / f"{stem}.ndjson"
    manifest_path = resolved_root / f"{stem}.manifest.json"
    output_path_hash = audit_hash(str(output_path))
    manifest_path_hash = audit_hash(str(manifest_path))
    _write_atomic_bytes(output_path, payload)
    manifest = {
        "schema_version": AUDIT_EXPORT_BATCH_SCHEMA_VERSION,
        "batch_id": batch_id,
        "tenant_id": tenant_id,
        "started_at": started_at.isoformat(),
        "exported_at": exported_at.isoformat(),
        "output_file": output_path.name,
        "output_path_hash": output_path_hash,
        "manifest_file": manifest_path.name,
        "manifest_path_hash": manifest_path_hash,
        "record_count": len(payload_rows),
        "record_type_counts": record_type_counts,
        "bytes_written": len(payload),
        "content_sha256": content_sha256,
        "first_record_created_at": _first_record_created_at(source_rows),
        "latest_record_created_at": _latest_record_created_at(source_rows),
        "partial": partial,
        "incremental": incremental,
        "previous_cursor": previous_cursor.as_dict() if previous_cursor else None,
        "high_water_cursor": high_water_cursor.as_dict() if high_water_cursor else None,
        "cursor_advance_allowed": bool(high_water_cursor),
        "options": _options_summary(options),
        "lock_name": AUDIT_EXPORT_BATCH_LOCK_NAME,
        "worker_source": "worker",
    }
    _write_atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return AuditExportBatchReport(
        batch_id=batch_id,
        tenant_id=tenant_id,
        started_at=started_at,
        exported_at=exported_at,
        output_file=output_path.name,
        output_path_hash=output_path_hash,
        manifest_file=manifest_path.name,
        manifest_path_hash=manifest_path_hash,
        record_count=len(payload_rows),
        record_type_counts=record_type_counts,
        bytes_written=len(payload),
        content_sha256=content_sha256,
        first_record_created_at=manifest["first_record_created_at"],
        latest_record_created_at=manifest["latest_record_created_at"],
        partial=partial,
        incremental=incremental,
        previous_cursor=manifest["previous_cursor"],
        high_water_cursor=manifest["high_water_cursor"],
        cursor_advance_allowed=manifest["cursor_advance_allowed"],
    )


def _operation_summary(
    *,
    report: AuditExportBatchReport,
    options: AuditExportBatchOptions,
    source: str,
) -> dict[str, Any]:
    return {
        "schema_version": EVENT_STORE_OPERATION_SUMMARY_VERSION,
        "source": source,
        "export_schema_version": report.schema_version,
        "batch_id": report.batch_id,
        "started_at": report.started_at.isoformat(),
        "exported_at": report.exported_at.isoformat(),
        "output_file": report.output_file,
        "output_path_hash": report.output_path_hash,
        "manifest_file": report.manifest_file,
        "manifest_path_hash": report.manifest_path_hash,
        "record_count": report.record_count,
        "record_type_counts": report.record_type_counts,
        "bytes_written": report.bytes_written,
        "content_sha256": report.content_sha256,
        "first_record_created_at": report.first_record_created_at,
        "latest_record_created_at": report.latest_record_created_at,
        "partial": report.partial,
        "incremental": report.incremental,
        "previous_cursor": report.previous_cursor,
        "high_water_cursor": report.high_water_cursor,
        "cursor_advance_allowed": report.cursor_advance_allowed,
        "lock_name": AUDIT_EXPORT_BATCH_LOCK_NAME,
        "error_type": report.error_type,
        "options": _options_summary(options),
    }


def _options_summary(options: AuditExportBatchOptions) -> dict[str, Any]:
    return {
        "include_events": options.include_events,
        "include_tool_audit": options.include_tool_audit,
        "include_event_store_operations": options.include_event_store_operations,
        "include_operations_automation_executions": options.include_operations_automation_executions,
        "event_type": options.event_type,
        "created_after": options.created_after,
        "created_before": options.created_before,
        "limit": options.limit,
        "order": options.order,
        "incremental": options.incremental,
    }


def _latest_completed_export_cursor(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    options: AuditExportBatchOptions,
) -> AuditExportCursor | None:
    if not _incremental_enabled(options):
        return None
    records = event_store.list_event_store_operations(
        tenant_id=tenant_id,
        operation=AUDIT_EXPORT_BATCH_OPERATION,
        status="completed",
        limit=100,
        order="desc",
    )
    for record in records:
        summary = record.summary if isinstance(record.summary, dict) else {}
        if summary.get("partial"):
            continue
        if not summary.get("cursor_advance_allowed"):
            continue
        if not _previous_options_are_cursor_compatible(summary.get("options"), options):
            continue
        cursor = coerce_audit_export_cursor(summary.get("high_water_cursor"))
        if cursor:
            return cursor
    return None


def _incremental_enabled(options: AuditExportBatchOptions) -> bool:
    return bool(options.incremental and options.created_after is None)


def _previous_options_are_cursor_compatible(
    previous_options: Any,
    current_options: AuditExportBatchOptions,
) -> bool:
    if not isinstance(previous_options, dict):
        return False
    for key in (
        "include_events",
        "include_tool_audit",
        "include_event_store_operations",
        "include_operations_automation_executions",
        "event_type",
    ):
        if previous_options.get(key) != getattr(current_options, key):
            return False
    if previous_options.get("created_after") is not None:
        return False
    return True


def _lock_conflict_summary(exc: EventStoreOperationLockConflict) -> dict[str, Any]:
    active = exc.active_lock
    return {
        "lock_name": active.lock_name,
        "active_operation": active.operation,
        "active_owner_hash": audit_hash(active.owner_id),
        "active_acquired_at": active.acquired_at,
        "active_expires_at": active.expires_at,
    }


def _validate_options(options: AuditExportBatchOptions) -> None:
    if (
        not options.include_events
        and not options.include_tool_audit
        and not options.include_event_store_operations
        and not options.include_operations_automation_executions
    ):
        raise ValueError("At least one audit source must be included")
    if options.limit < 1:
        raise ValueError("limit must be >= 1")


def _batch_stem(*, tenant_id: str, exported_at: datetime) -> str:
    tenant = re.sub(r"[^A-Za-z0-9_.-]+", "-", tenant_id).strip("-")[:80] or "tenant"
    timestamp = exported_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"support-agent-audit-{tenant}-{timestamp}-{secrets.token_hex(4)}"


def _latest_record_created_at(rows: list[dict[str, Any]]) -> str | None:
    values = [str(row.get("created_at")) for row in rows if row.get("created_at")]
    return max(values) if values else None


def _first_record_created_at(rows: list[dict[str, Any]]) -> str | None:
    values = [str(row.get("created_at")) for row in rows if row.get("created_at")]
    return min(values) if values else None


def _record_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        record_type = str(row.get("record_type") or "unknown")[:80]
        counts[record_type] = counts.get(record_type, 0) + 1
    return counts


def _write_atomic_bytes(path: Path, payload: bytes) -> None:
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temp_path.write_bytes(payload)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_atomic_text(path: Path, payload: str) -> None:
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _ensure_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return _ensure_datetime(parsed)


def _safe_resolved_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_cursor_dict(value: Any) -> dict[str, str] | None:
    cursor = coerce_audit_export_cursor(value)
    return cursor.as_dict() if cursor else None


def _dict_of_ints(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, count in value.items():
        try:
            result[str(key)] = int(count)
        except (TypeError, ValueError):
            continue
    return result

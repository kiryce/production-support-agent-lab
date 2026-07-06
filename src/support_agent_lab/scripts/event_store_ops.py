from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import (
    EventStoreRetentionReport,
    SQLiteBackupReport,
    SQLiteEventStore,
    SQLiteRestoreDrillReport,
)


CLI_ACTOR_USER_ID = "event_store_cli"
EVENT_STORE_OPERATION_BACKUP = "backup"
EVENT_STORE_OPERATION_RESTORE_DRILL = "restore_drill"
EVENT_STORE_OPERATION_RETENTION_PREVIEW = "retention_preview"
EVENT_STORE_OPERATION_RETENTION_APPLY = "retention_apply"
EVENT_STORE_OPERATION_SUMMARY_VERSION = "event_store_operation_summary.v1"


def _load_event_store(database_url: str | None) -> SQLiteEventStore:
    url = database_url or Settings().app_database_url
    event_store = SQLiteEventStore.from_url(url)
    if event_store is None:
        raise RuntimeError("Only sqlite:/// APP_DATABASE_URL values are supported by this operator command")
    return event_store


def _audit_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"audit_export.v1:{value}".encode("utf-8")).hexdigest()[:32]


def _path_audit_summary(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {"file": None, "path_hash": None}
    return {
        "file": Path(path_value).name,
        "path_hash": _audit_hash(path_value),
    }


def _operation_error_summary(
    exc: Exception,
    *,
    path_values: Sequence[str | None] = (),
) -> dict[str, Any]:
    detail = str(exc)[:500]
    for path_value in path_values:
        if not path_value:
            continue
        candidates = {path_value, str(Path(path_value))}
        try:
            candidates.add(str(Path(path_value).resolve()))
        except OSError:
            pass
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate:
                detail = detail.replace(candidate, "[path]")
    return {
        "error_type": exc.__class__.__name__,
        "detail": detail,
    }


def _high_water_summary(high_water_mark: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        table_name: {
            "row_count": values.get("row_count"),
            "max_rowid": values.get("max_rowid"),
            **{
                key: value
                for key, value in values.items()
                if key.startswith("max_") and key != "max_rowid"
            },
        }
        for table_name, values in high_water_mark.items()
    }


def _retention_params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "include_events": args.include_events,
        "vacuum": args.vacuum,
        "event_retention_days": args.event_retention_days,
        "tool_audit_retention_days": args.tool_audit_retention_days,
        "idempotency_retention_days": args.idempotency_retention_days,
        "alert_delivery_retention_days": args.alert_delivery_retention_days,
    }


def _retention_tables_summary(report: EventStoreRetentionReport) -> list[dict[str, Any]]:
    return [
        {
            "table_name": table.table_name,
            "cutoff_at": table.cutoff_at.isoformat() if table.cutoff_at else None,
            "candidate_count": table.candidate_count,
            "deleted_count": table.deleted_count,
            "action": table.action,
            "reason": table.reason,
        }
        for table in report.tables
    ]


def _backup_operation_summary(
    *,
    report: SQLiteBackupReport,
    high_water_mark: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    backup_path = _path_audit_summary(report.backup_path)
    source_path = _path_audit_summary(report.source_path)
    return {
        "backup_file": backup_path["file"],
        "backup_path_hash": backup_path["path_hash"],
        "source_path_hash": source_path["path_hash"],
        "verified": report.verified,
        "verification_detail": report.verification_detail,
        "size_bytes": report.size_bytes,
        "page_count": report.page_count,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat(),
        "high_water_mark": _high_water_summary(high_water_mark),
    }


def _restore_drill_operation_summary(*, report: SQLiteRestoreDrillReport) -> dict[str, Any]:
    backup_path = _path_audit_summary(report.backup_path)
    restore_path = _path_audit_summary(report.restore_path)
    return {
        "backup_file": backup_path["file"],
        "backup_path_hash": backup_path["path_hash"],
        "restore_path_hash": restore_path["path_hash"],
        "restore_path_retained": report.restore_path_retained,
        "verified": report.verified,
        "health_check_passed": report.health_check_passed,
        "verification_detail": report.verification_detail,
        "size_bytes": report.size_bytes,
        "page_count": report.page_count,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat(),
        "table_counts": report.table_counts,
        "high_water_mark": _high_water_summary(report.high_water_mark),
    }


def _retention_operation_summary(
    *,
    report: EventStoreRetentionReport,
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dry_run": report.dry_run,
        "include_events": report.include_events,
        "vacuum_requested": report.vacuum_requested,
        "vacuum_performed": report.vacuum_performed,
        "params": params,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat(),
        "total_candidates": report.total_candidates,
        "total_deleted": report.total_deleted,
        "preview_token_issued": bool(report.preview_token),
        "tables": _retention_tables_summary(report),
    }


def _operation_from_args(args: argparse.Namespace) -> str:
    if args.command == "backup":
        return EVENT_STORE_OPERATION_BACKUP
    if args.command == "restore-drill":
        return EVENT_STORE_OPERATION_RESTORE_DRILL
    if args.apply:
        return EVENT_STORE_OPERATION_RETENTION_APPLY
    return EVENT_STORE_OPERATION_RETENTION_PREVIEW


def _tenant_id_from_args(args: argparse.Namespace, settings: Settings) -> str:
    return getattr(args, "tenant_id", settings.app_tenant_id)


def _failure_status(exc: Exception) -> str:
    if isinstance(exc, (FileExistsError, ValueError)):
        return "rejected"
    return "failed"


def _failure_operation_summary(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    path_values: list[str | None] = []
    if args.command == "backup":
        path_values.append(args.output)
        target_path = _path_audit_summary(args.output)
        summary: dict[str, Any] = {
            "command": args.command,
            "target_file": target_path["file"],
            "target_path_hash": target_path["path_hash"],
            "verify_requested": not args.no_verify,
        }
    elif args.command == "restore-drill":
        path_values.extend([args.backup, args.restore_output])
        backup_path = _path_audit_summary(args.backup)
        restore_path = _path_audit_summary(args.restore_output)
        summary = {
            "command": args.command,
            "backup_file": backup_path["file"],
            "backup_path_hash": backup_path["path_hash"],
            "restore_path_hash": restore_path["path_hash"],
        }
    else:
        summary = {
            "command": args.command,
            "dry_run": not args.apply,
            "params": _retention_params_from_args(args),
        }
    return {
        **summary,
        **_operation_error_summary(exc, path_values=path_values),
    }


def _append_operation_record(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    actor_user_id: str,
    operation: str,
    status: str,
    summary: dict[str, Any],
) -> None:
    event_store.append_event_store_operation(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id or CLI_ACTOR_USER_ID,
        operation=operation,
        status=status,
        summary={
            "schema_version": EVENT_STORE_OPERATION_SUMMARY_VERSION,
            "source": "cli",
            **summary,
        },
    )


def _completed_summary(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    args: argparse.Namespace,
    report: SQLiteBackupReport | SQLiteRestoreDrillReport | EventStoreRetentionReport,
) -> dict[str, Any]:
    if isinstance(report, SQLiteBackupReport):
        return _backup_operation_summary(
            report=report,
            high_water_mark=event_store.retention_high_water_mark(tenant_id=tenant_id),
        )
    if isinstance(report, SQLiteRestoreDrillReport):
        return _restore_drill_operation_summary(report=report)
    return _retention_operation_summary(
        report=report,
        params=_retention_params_from_args(args),
    )


def _production_local_apply_message() -> str:
    return (
        "Refusing direct production retention apply from CLI. Use the admin API/console guarded flow, "
        "or pass --unsafe-local-apply to explicitly bypass API tokens for a local emergency operation."
    )


def _production_guard_enabled(settings: Settings) -> bool:
    return settings.is_production or settings.app_require_production


def build_parser() -> argparse.ArgumentParser:
    settings = Settings()
    parser = argparse.ArgumentParser(
        description="Operate the SQLite event store used by the support agent service.",
    )
    parser.add_argument(
        "--database-url",
        help="SQLite URL to operate on. Defaults to APP_DATABASE_URL from the environment or .env.",
    )
    parser.add_argument(
        "--actor-user-id",
        default=CLI_ACTOR_USER_ID,
        help="Actor id to write into event-store operation ledger rows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create an online SQLite backup and verify it.")
    backup.add_argument("--output", required=True, help="Path to write the backup database.")
    backup.add_argument("--tenant-id", default=settings.app_tenant_id, help="Tenant id for ledger and high-water checks.")
    backup.add_argument("--overwrite", action="store_true", help="Replace the backup file if it already exists.")
    backup.add_argument("--no-verify", action="store_true", help="Skip quick_check verification after backup.")

    restore_drill = subparsers.add_parser(
        "restore-drill",
        help="Copy a backup to a scratch database and prove it can be opened, checked, and queried.",
    )
    restore_drill.add_argument("--backup", required=True, help="Backup database file to drill.")
    restore_drill.add_argument("--tenant-id", default=settings.app_tenant_id, help="Tenant id for high-water checks.")
    restore_drill.add_argument(
        "--restore-output",
        help="Optional path to retain the drilled restore copy. Defaults to a temporary file that is removed.",
    )
    restore_drill.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace --restore-output if it already exists.",
    )

    retention = subparsers.add_parser("retention", help="Preview or apply the configured retention policy.")
    retention.add_argument("--tenant-id", default=settings.app_tenant_id, help="Tenant id to operate on.")
    retention.add_argument("--apply", action="store_true", help="Delete matching rows. Without this flag, dry-run only.")
    retention.add_argument(
        "--unsafe-local-apply",
        action="store_true",
        help="Required to run retention --apply directly in production outside the guarded admin API flow.",
    )
    retention.add_argument("--include-events", action="store_true", help="Also delete old append-only event rows.")
    retention.add_argument("--vacuum", action="store_true", help="Run VACUUM after an applied deletion.")
    retention.add_argument("--event-retention-days", type=int, default=settings.app_event_retention_days)
    retention.add_argument("--tool-audit-retention-days", type=int, default=settings.app_tool_audit_retention_days)
    retention.add_argument("--idempotency-retention-days", type=int, default=settings.app_idempotency_retention_days)
    retention.add_argument(
        "--alert-delivery-retention-days",
        type=int,
        default=settings.app_alert_delivery_retention_days,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    event_store: SQLiteEventStore | None = None
    operation = _operation_from_args(args)
    tenant_id = _tenant_id_from_args(args, settings)
    try:
        event_store = _load_event_store(args.database_url)
        if (
            args.command == "retention"
            and args.apply
            and _production_guard_enabled(settings)
            and not args.unsafe_local_apply
        ):
            exc = RuntimeError(_production_local_apply_message())
            _append_operation_record(
                event_store=event_store,
                tenant_id=tenant_id,
                actor_user_id=args.actor_user_id,
                operation=operation,
                status="rejected",
                summary={
                    "dry_run": False,
                    "params": _retention_params_from_args(args),
                    "guard": "production_cli_direct_apply",
                    **_operation_error_summary(exc),
                },
            )
            print(f"event store operation rejected: {exc}", file=sys.stderr)
            return 2
        if args.command == "backup":
            report = event_store.backup_to(
                Path(args.output),
                overwrite=args.overwrite,
                verify=not args.no_verify,
            )
        elif args.command == "restore-drill":
            report = event_store.restore_drill(
                Path(args.backup),
                restore_path=Path(args.restore_output) if args.restore_output else None,
                overwrite=args.overwrite,
                tenant_id=args.tenant_id,
            )
        else:
            report = event_store.apply_retention_policy(
                tenant_id=args.tenant_id,
                dry_run=not args.apply,
                include_events=args.include_events,
                vacuum=args.vacuum,
                event_retention_days=args.event_retention_days,
                tool_audit_retention_days=args.tool_audit_retention_days,
                idempotency_retention_days=args.idempotency_retention_days,
                alert_delivery_retention_days=args.alert_delivery_retention_days,
            )
        _append_operation_record(
            event_store=event_store,
            tenant_id=tenant_id,
            actor_user_id=args.actor_user_id,
            operation=operation,
            status="completed",
            summary=_completed_summary(
                event_store=event_store,
                tenant_id=tenant_id,
                args=args,
                report=report,
            ),
        )
    except Exception as exc:
        if event_store is not None:
            try:
                _append_operation_record(
                    event_store=event_store,
                    tenant_id=tenant_id,
                    actor_user_id=args.actor_user_id,
                    operation=operation,
                    status=_failure_status(exc),
                    summary=_failure_operation_summary(args, exc),
                )
            except Exception as ledger_exc:
                print(
                    f"event store operation failed: {exc}; additionally failed to write operation ledger: {ledger_exc}",
                    file=sys.stderr,
                )
                return 1
        print(f"event store operation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

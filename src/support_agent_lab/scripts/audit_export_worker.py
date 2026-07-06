from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from collections.abc import Sequence

from support_agent_lab.audit.export_batch import (
    AuditExportBatchOptions,
    run_audit_export_batch,
    sanitize_audit_export_batch_report,
)
from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import SQLiteEventStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run durable sanitized audit export batches.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one export batch and exit. This is the default.")
    mode.add_argument("--interval-seconds", type=int, help="Run forever with this delay between export batches.")
    parser.add_argument("--database-url", help="SQLite APP_DATABASE_URL override.")
    parser.add_argument("--output-dir", help="Directory for NDJSON and manifest files.")
    parser.add_argument("--tenant-id", help="Tenant id to export. Defaults to APP_TENANT_ID.")
    parser.add_argument("--actor-user-id", default="audit_export_worker", help="Actor id recorded in the operation ledger.")
    parser.add_argument("--worker-id", help="Stable worker id used for operation locks.")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum exported rows per batch.")
    parser.add_argument("--order", choices=["asc", "desc"], default="asc")
    parser.add_argument("--event-type", help="Optional event type filter for event rows.")
    parser.add_argument("--created-after", help="Only export records created at or after this ISO timestamp.")
    parser.add_argument("--created-before", help="Only export records created at or before this ISO timestamp.")
    parser.add_argument("--exclude-events", action="store_true")
    parser.add_argument("--exclude-tool-audit", action="store_true")
    parser.add_argument("--exclude-event-store-operations", action="store_true")
    parser.add_argument("--exclude-operations-automation-executions", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit sanitized JSON summaries.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    try:
        event_store = _load_event_store(args.database_url or settings.app_database_url)
        _validate_worker_config(settings)
        _validate_args(args)
        options = AuditExportBatchOptions(
            include_events=not args.exclude_events,
            include_tool_audit=not args.exclude_tool_audit,
            include_event_store_operations=not args.exclude_event_store_operations,
            include_operations_automation_executions=not args.exclude_operations_automation_executions,
            event_type=args.event_type,
            created_after=args.created_after,
            created_before=args.created_before,
            limit=args.limit,
            order=args.order,
        )
    except Exception as exc:
        _emit_error(str(exc), json_output=args.json)
        return 2

    worker_id = args.worker_id or _default_worker_id()
    tenant_id = args.tenant_id or settings.app_tenant_id
    output_dir = args.output_dir or settings.app_audit_export_dir

    def run_once() -> int:
        report = run_audit_export_batch(
            event_store=event_store,
            tenant_id=tenant_id,
            output_dir=output_dir,
            actor_user_id=args.actor_user_id,
            owner_id=worker_id,
            options=options,
            lock_ttl_seconds=settings.app_event_store_operation_lock_ttl_seconds,
        )
        _emit_report(report, json_output=args.json)
        return 0 if report.status == "completed" else 1

    if not args.interval_seconds:
        return run_once()
    try:
        while True:
            run_once()
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0


def _load_event_store(database_url: str) -> SQLiteEventStore:
    event_store = SQLiteEventStore.from_url(database_url)
    if event_store is None:
        raise RuntimeError("audit export worker requires a sqlite:/// APP_DATABASE_URL")
    return event_store


def _validate_worker_config(settings: Settings) -> None:
    if settings.app_require_production and not settings.is_production:
        raise RuntimeError("APP_REQUIRE_PRODUCTION=true requires APP_ENV=production")


def _validate_args(args: argparse.Namespace) -> None:
    if args.interval_seconds is not None and args.interval_seconds < 1:
        raise RuntimeError("--interval-seconds must be >= 1")
    if args.limit < 1:
        raise RuntimeError("--limit must be >= 1")
    if (
        args.exclude_events
        and args.exclude_tool_audit
        and args.exclude_event_store_operations
        and args.exclude_operations_automation_executions
    ):
        raise RuntimeError("at least one audit source must be included")


def _emit_report(report, *, json_output: bool) -> None:
    summary = sanitize_audit_export_batch_report(report)
    if json_output:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return
    print(
        "audit export batch "
        f"status={summary['status']} "
        f"records={summary['record_count']} "
        f"bytes={summary['bytes_written']} "
        f"file={summary['output_file']} "
        f"partial={summary['partial']}"
    )


def _emit_error(message: str, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": message}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return
    print(f"audit export worker failed: {message}", file=sys.stderr)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


if __name__ == "__main__":
    raise SystemExit(main())

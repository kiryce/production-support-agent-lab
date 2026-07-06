from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from collections.abc import Sequence

from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.monitoring.monitor_review_service import (
    run_monitor_review_cycle,
    summarize_monitor_review_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the durable async monitor review worker.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one monitor review cycle and exit. This is the default.")
    mode.add_argument("--interval-seconds", type=int, help="Run forever with this delay between cycles.")
    parser.add_argument("--database-url", help="SQLite APP_DATABASE_URL override for persisted run events.")
    parser.add_argument("--limit", type=int, default=100, help="Completed agent runs to inspect per cycle.")
    parser.add_argument("--created-after", help="Only review completed run events created at or after this ISO timestamp.")
    parser.add_argument("--created-before", help="Only review completed run events created at or before this ISO timestamp.")
    parser.add_argument("--worker-id", help="Stable worker id used for heartbeat rows and operation locks.")
    parser.add_argument("--json", action="store_true", help="Emit sanitized JSON summaries.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    worker_id = args.worker_id or _default_worker_id()
    try:
        event_store = _load_event_store(args.database_url or settings.app_database_url)
        _validate_worker_config(settings, event_store)
        _validate_args(args)
    except Exception as exc:
        _emit_error(str(exc), json_output=args.json)
        return 2

    def run_once() -> None:
        report = run_monitor_review_cycle(
            settings=settings,
            event_store=event_store,
            limit=args.limit,
            worker_id=worker_id,
            created_after=args.created_after,
            created_before=args.created_before,
            record_worker_heartbeat=True,
        )
        _emit_report(report, worker_id=worker_id, json_output=args.json)

    if not args.interval_seconds:
        run_once()
        return 0
    try:
        while True:
            run_once()
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0


def _load_event_store(database_url: str) -> SQLiteEventStore:
    event_store = SQLiteEventStore.from_url(database_url)
    if event_store is None:
        raise RuntimeError("monitor review worker requires a sqlite:/// APP_DATABASE_URL")
    return event_store


def _validate_worker_config(settings: Settings, event_store: SQLiteEventStore) -> None:
    if settings.app_require_production and not settings.is_production:
        raise RuntimeError("APP_REQUIRE_PRODUCTION=true requires APP_ENV=production")
    if settings.is_production and event_store is None:
        raise RuntimeError("production monitor review worker requires a configured event store")


def _validate_args(args: argparse.Namespace) -> None:
    if args.interval_seconds is not None and args.interval_seconds < 1:
        raise RuntimeError("--interval-seconds must be >= 1")
    if args.limit < 1:
        raise RuntimeError("--limit must be >= 1")


def _emit_report(report, *, worker_id: str, json_output: bool) -> None:
    summary = summarize_monitor_review_report(report)
    summary["worker_id"] = worker_id
    if json_output:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return
    print(
        "monitor review cycle "
        f"worker={worker_id} "
        f"status={summary['cycle_status']} "
        f"inspected={summary['inspected_count']} "
        f"reviewed={summary['reviewed_count']} "
        f"existing={summary['skipped_existing_count']} "
        f"unreviewable={summary['skipped_unreviewable_count']} "
        f"failed={summary['failed_count']}"
    )


def _emit_error(message: str, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": message}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return
    print(f"monitor review worker failed: {message}", file=sys.stderr)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


if __name__ == "__main__":
    raise SystemExit(main())

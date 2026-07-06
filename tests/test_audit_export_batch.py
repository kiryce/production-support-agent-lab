import hashlib
import json
from datetime import timedelta

from support_agent_lab.audit.export_batch import (
    AUDIT_EXPORT_BATCH_LOCK_NAME,
    AUDIT_EXPORT_BATCH_OPERATION,
    AuditExportBatchOptions,
    run_audit_export_batch,
    summarize_audit_export_batches,
)
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.models import ToolStatus, utc_now
from support_agent_lab.scripts.audit_export_worker import main as worker_main
from support_agent_lab.tools.registry import ToolAuditRecord


def test_audit_export_batch_writes_sanitized_ndjson_manifest_and_ledger(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    _seed_audit_rows(event_store)
    output_dir = tmp_path / "exports"

    report = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=output_dir,
        actor_user_id="operator_secret_should_not_export",
        owner_id="worker_secret_should_not_export",
        options=AuditExportBatchOptions(limit=20),
    )

    assert report.status == "completed"
    export_path = output_dir / report.output_file
    manifest_path = output_dir / report.manifest_file
    assert export_path.exists()
    assert manifest_path.exists()
    payload = export_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
    ledger = event_store.list_event_store_operations(
        tenant_id="demo_tenant",
        operation=AUDIT_EXPORT_BATCH_OPERATION,
        limit=1,
    )[0]

    assert manifest["record_count"] == len(rows) == report.record_count
    assert manifest["record_type_counts"] == report.record_type_counts
    assert manifest["bytes_written"] == len(payload) == report.bytes_written
    assert manifest["content_sha256"] == hashlib.sha256(payload).hexdigest() == report.content_sha256
    assert manifest["output_file"] == report.output_file
    assert manifest["output_path_hash"] == report.output_path_hash
    assert manifest["manifest_file"] == report.manifest_file
    assert manifest["manifest_path_hash"] == report.manifest_path_hash
    assert manifest["lock_name"] == AUDIT_EXPORT_BATCH_LOCK_NAME
    assert manifest["incremental"] is True
    assert manifest["previous_cursor"] is None
    assert manifest["high_water_cursor"] == report.high_water_cursor
    assert manifest["cursor_advance_allowed"] is True
    assert report.high_water_cursor is not None
    assert {row["record_type"] for row in rows} == {
        "event",
        "tool_audit",
        "event_store_operation",
        "operations_automation_execution",
    }
    operation_row = next(row for row in rows if row["record_type"] == "event_store_operation")
    assert "detail" not in operation_row["operation_summary"]
    assert "owner_id" not in operation_row["operation_summary"]
    assert operation_row["operation_summary"]["detail_hash"]
    assert operation_row["operation_summary"]["owner_id_hash"]
    assert ledger.status == "completed"
    assert ledger.summary["output_file"] == report.output_file
    assert ledger.summary["output_path_hash"] == report.output_path_hash
    assert ledger.summary["high_water_cursor"] == report.high_water_cursor
    assert ledger.summary["cursor_advance_allowed"] is True
    assert "output_path" not in ledger.summary

    combined = payload.decode("utf-8") + manifest_path.read_text(encoding="utf-8") + json.dumps(ledger.summary)
    assert "4111" not in combined
    assert "A1001" not in combined
    assert "PRIVATE" not in combined
    assert "user_sensitive_export" not in combined
    assert "operator_sensitive_export" not in combined
    assert "operator_secret_should_not_export" not in combined
    assert "worker_secret_should_not_export" not in combined
    assert "summary_owner_secret" not in combined
    assert str(output_dir) not in combined
    assert not list(output_dir.glob("*.tmp"))
    assert not list(output_dir.glob(".*.tmp"))


def test_audit_export_batch_marks_partial_exports_with_control_row(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    event_store.append(
        tenant_id="demo_tenant",
        conversation_id="conv_partial",
        user_id="user_partial",
        run_id="run_partial",
        event_type="message.user",
        payload={"role": "user", "content": "PRIVATE partial content"},
    )
    event_store.append(
        tenant_id="demo_tenant",
        conversation_id="conv_partial_2",
        user_id="user_partial_2",
        run_id="run_partial_2",
        event_type="message.user",
        payload={"role": "user", "content": "PRIVATE partial content 2"},
    )

    report = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=AuditExportBatchOptions(
            include_tool_audit=False,
            include_event_store_operations=False,
            include_operations_automation_executions=False,
            limit=1,
        ),
    )

    export_path = tmp_path / "exports" / report.output_file
    manifest = json.loads((tmp_path / "exports" / report.manifest_file).read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]

    assert report.partial is True
    assert report.high_water_cursor is None
    assert report.cursor_advance_allowed is False
    assert manifest["partial"] is True
    assert manifest["high_water_cursor"] is None
    assert manifest["cursor_advance_allowed"] is False
    assert manifest["record_count"] == 2
    assert manifest["record_type_counts"]["event"] == 1
    assert manifest["record_type_counts"]["export_control"] == 1
    assert rows[-1]["record_type"] == "export_control"
    assert rows[-1]["partial"] is True


def test_audit_export_batch_uses_source_sequence_cursor_for_incremental_runs(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    created_at = "2026-07-06T10:00:00+00:00"
    _append_tool_audit(event_store, "audit_cursor_a", created_at)
    _append_tool_audit(event_store, "audit_cursor_b", created_at)
    options = AuditExportBatchOptions(
        include_events=False,
        include_event_store_operations=False,
        include_operations_automation_executions=False,
        limit=20,
    )

    first = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=options,
    )
    _append_tool_audit(event_store, "audit_cursor_0", created_at)
    second = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=options,
    )

    rows = _read_export_rows(tmp_path / "exports" / second.output_file)

    assert first.high_water_cursor == {
        "created_at": created_at,
        "record_type": "tool_audit",
        "id": "audit_cursor_b",
        "source_sequence": 2,
    }
    assert first.source_high_water_cursors["tool_audit"] == first.high_water_cursor
    assert second.previous_cursor == first.high_water_cursor
    assert second.high_water_cursor == {
        "created_at": created_at,
        "record_type": "tool_audit",
        "id": "audit_cursor_0",
        "source_sequence": 3,
    }
    assert second.source_high_water_cursors["tool_audit"] == second.high_water_cursor
    assert [row["id"] for row in rows] == ["audit_cursor_0"]
    assert second.partial is False


def test_audit_export_batch_created_before_window_does_not_reuse_incremental_cursor(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    created_at = "2026-07-06T10:00:00+00:00"
    _append_tool_audit(event_store, "audit_window_a", created_at)
    _append_tool_audit(event_store, "audit_window_b", created_at)
    base_options = AuditExportBatchOptions(
        include_events=False,
        include_event_store_operations=False,
        include_operations_automation_executions=False,
        limit=20,
    )
    run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=base_options,
    )

    window = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=AuditExportBatchOptions(
            include_events=False,
            include_event_store_operations=False,
            include_operations_automation_executions=False,
            created_before=created_at,
            limit=20,
        ),
    )

    rows = _read_export_rows(tmp_path / "exports" / window.output_file)

    assert window.incremental is False
    assert window.previous_cursor is None
    assert [row["id"] for row in rows] == ["audit_window_a", "audit_window_b"]


def test_audit_export_batch_does_not_advance_cursor_from_partial_batch(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    created_at = "2026-07-06T10:00:00+00:00"
    _append_tool_audit(event_store, "audit_partial_a", created_at)
    _append_tool_audit(event_store, "audit_partial_b", created_at)
    partial_options = AuditExportBatchOptions(
        include_events=False,
        include_event_store_operations=False,
        include_operations_automation_executions=False,
        limit=1,
    )

    partial = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=partial_options,
    )
    full = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=AuditExportBatchOptions(
            include_events=False,
            include_event_store_operations=False,
            include_operations_automation_executions=False,
            limit=20,
        ),
    )

    rows = _read_export_rows(tmp_path / "exports" / full.output_file)

    assert partial.partial is True
    assert partial.high_water_cursor is None
    assert partial.cursor_advance_allowed is False
    assert full.previous_cursor is None
    assert [row["id"] for row in rows] == ["audit_partial_a", "audit_partial_b"]


def test_audit_export_batch_rejects_when_maintenance_lock_is_held(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    output_dir = tmp_path / "exports"
    lock = event_store.acquire_event_store_operation_lock(
        tenant_id="demo_tenant",
        lock_name=AUDIT_EXPORT_BATCH_LOCK_NAME,
        operation="retention_apply",
        owner_id="secret_lock_owner",
    )
    try:
        report = run_audit_export_batch(
            event_store=event_store,
            tenant_id="demo_tenant",
            output_dir=output_dir,
            owner_id="audit_worker",
        )
    finally:
        event_store.release_event_store_operation_lock(lock)

    ledger = event_store.list_event_store_operations(
        tenant_id="demo_tenant",
        operation=AUDIT_EXPORT_BATCH_OPERATION,
        limit=1,
    )[0]
    summary_text = json.dumps(ledger.summary, ensure_ascii=False, sort_keys=True)

    assert report.status == "rejected"
    assert report.operation_id == ledger.id
    assert ledger.status == "rejected"
    assert ledger.summary["active_operation"] == "retention_apply"
    assert ledger.summary["active_owner_hash"]
    assert "secret_lock_owner" not in summary_text
    assert not output_dir.exists()


def test_audit_export_batch_summary_reports_fresh_failed_and_stale_states(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    _seed_audit_rows(event_store)
    report = run_audit_export_batch(
        event_store=event_store,
        tenant_id="demo_tenant",
        output_dir=tmp_path / "exports",
        options=AuditExportBatchOptions(limit=20),
    )

    fresh = summarize_audit_export_batches(
        event_store=event_store,
        tenant_id="demo_tenant",
        stale_after_seconds=3600,
        now=report.exported_at + timedelta(minutes=1),
    )
    stale = summarize_audit_export_batches(
        event_store=event_store,
        tenant_id="demo_tenant",
        stale_after_seconds=1,
        now=report.exported_at + timedelta(minutes=2),
    )

    assert fresh.status == "fresh"
    assert fresh.last_record_count == report.record_count
    assert fresh.last_output_file == report.output_file
    assert fresh.last_manifest_file == report.manifest_file
    assert fresh.last_high_water_cursor == report.high_water_cursor
    assert fresh.last_cursor_advance_allowed is True
    assert stale.status == "stale"


def test_audit_export_worker_cli_outputs_sanitized_json(tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "events.db"
    event_store = SQLiteEventStore(db_path)
    _seed_audit_rows(event_store)
    output_dir = tmp_path / "exports"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_REQUIRE_PRODUCTION", "false")
    monkeypatch.setenv("APP_TENANT_ID", "demo_tenant")

    exit_code = worker_main(
        [
            "--once",
            "--json",
            "--database-url",
            f"sqlite:///{db_path}",
            "--output-dir",
            str(output_dir),
            "--worker-id",
            "worker_cli_private",
            "--actor-user-id",
            "actor_cli_private",
            "--limit",
            "20",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["output_file"].endswith(".ndjson")
    assert payload["manifest_file"].endswith(".manifest.json")
    assert payload["cursor_advance_allowed"] is True
    assert payload["high_water_cursor"]
    assert str(output_dir) not in captured.out
    assert "4111" not in captured.out
    assert "A1001" not in captured.out
    assert "worker_cli_private" not in captured.out
    assert "actor_cli_private" not in captured.out


def _seed_audit_rows(event_store: SQLiteEventStore) -> None:
    created_at = utc_now().isoformat()
    event_store.append(
        tenant_id="demo_tenant",
        conversation_id="conv_sensitive_export",
        user_id="user_sensitive_export",
        run_id="run_sensitive_export",
        event_type="message.user",
        payload={
            "role": "user",
            "content": "My card is 4111 and order A1001 should not leave the audit boundary.",
            "metadata": {"source": "web", "unsafe": "PRIVATE metadata"},
        },
    )
    event_store.append_tool_audit(
        ToolAuditRecord(
            id="audit_export_tool",
            tenant_id="demo_tenant",
            actor_user_id="operator_sensitive_export",
            request_id="req_sensitive_export",
            trace_id="run_sensitive_export",
            tool_name="order.get",
            argument_hash="argument_hash_only",
            status=ToolStatus.failed,
            latency_ms=500,
            error_code="TIMEOUT",
            created_at=created_at,
        )
    )
    event_store.append_event_store_operation(
        tenant_id="demo_tenant",
        actor_user_id="operator_sensitive_operation",
        operation="backup",
        status="completed",
        summary={
            "schema_version": "event_store_operation_summary.v1",
            "backup_file": "support-agent-lab-demo.db",
            "backup_path_hash": "hash_only",
            "detail": "PRIVATE operation detail should not export",
            "owner_id": "summary_owner_secret",
            "verified": True,
        },
        created_at=created_at,
    )
    event_store.append_operations_automation_execution(
        tenant_id="demo_tenant",
        actor_user_id="operator_sensitive_automation",
        action_id="ops_run_retrieval_PRIVATE",
        action_kind="run_retrieval_diagnostics",
        title="Run retrieval diagnostics",
        status="completed",
        safe_to_auto_execute=True,
        command_method="POST",
        command_path="/api/v1/admin/knowledge/search",
        command_query={},
        command_body_keys=["limit", "query", "snippet_chars"],
        command_body_hash="body_hash_only",
        command_fingerprint="fingerprint_only",
        result_summary="3 retrieval chunk(s) selected for diagnostics.",
        source="console",
        created_at=created_at,
    )


def _append_tool_audit(event_store: SQLiteEventStore, record_id: str, created_at: str) -> None:
    event_store.append_tool_audit(
        ToolAuditRecord(
            id=record_id,
            tenant_id="demo_tenant",
            actor_user_id="operator_cursor_sensitive",
            request_id=f"req_{record_id}",
            trace_id=f"run_{record_id}",
            tool_name="order.get",
            argument_hash=f"argument_hash_{record_id}",
            status=ToolStatus.success,
            latency_ms=25,
            error_code=None,
            created_at=created_at,
        )
    )


def _read_export_rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

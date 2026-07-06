import json

from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.scripts.event_store_ops import main as event_store_ops_main


def _db_url(path) -> str:
    return f"sqlite:///{path}"


def test_production_retention_apply_requires_unsafe_flag_and_writes_rejection(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_REQUIRE_PRODUCTION", "false")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_prod")
    db_path = tmp_path / "events.db"

    exit_code = event_store_ops_main(
        [
            "--database-url",
            _db_url(db_path),
            "--actor-user-id",
            "ops_user",
            "retention",
            "--tenant-id",
            "tenant_prod",
            "--apply",
        ]
    )
    captured = capsys.readouterr()
    event_store = SQLiteEventStore(db_path)
    records = event_store.list_event_store_operations(
        tenant_id="tenant_prod",
        operation="retention_apply",
    )

    assert exit_code == 2
    assert "Refusing direct production retention apply" in captured.err
    assert len(records) == 1
    assert records[0].actor_user_id == "ops_user"
    assert records[0].status == "rejected"
    assert records[0].summary["schema_version"] == "event_store_operation_summary.v1"
    assert records[0].summary["source"] == "cli"
    assert records[0].summary["guard"] == "production_cli_direct_apply"
    assert records[0].summary["params"]["include_events"] is False


def test_cli_backup_writes_completed_operation_ledger_with_safe_paths(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_cli")
    db_path = tmp_path / "events.db"
    backup_path = tmp_path / "backups" / "events.backup.db"
    SQLiteEventStore(db_path)

    exit_code = event_store_ops_main(
        [
            "--database-url",
            _db_url(db_path),
            "--actor-user-id",
            "release_bot",
            "backup",
            "--tenant-id",
            "tenant_cli",
            "--output",
            str(backup_path),
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    event_store = SQLiteEventStore(db_path)
    records = event_store.list_event_store_operations(
        tenant_id="tenant_cli",
        operation="backup",
    )
    summary_json = json.dumps(records[0].summary, ensure_ascii=False, sort_keys=True)

    assert exit_code == 0
    assert report["verified"] is True
    assert backup_path.exists()
    assert len(records) == 1
    assert records[0].actor_user_id == "release_bot"
    assert records[0].status == "completed"
    assert records[0].summary["source"] == "cli"
    assert records[0].summary["backup_file"] == "events.backup.db"
    assert records[0].summary["backup_path_hash"]
    assert records[0].summary["source_path_hash"]
    assert str(tmp_path) not in summary_json


def test_cli_backup_failure_ledger_redacts_local_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_cli")
    db_path = tmp_path / "events.db"
    backup_path = tmp_path / "backups" / "events.backup.db"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_bytes(b"existing backup")
    SQLiteEventStore(db_path)

    exit_code = event_store_ops_main(
        [
            "--database-url",
            _db_url(db_path),
            "backup",
            "--tenant-id",
            "tenant_cli",
            "--output",
            str(backup_path),
        ]
    )
    capsys.readouterr()
    event_store = SQLiteEventStore(db_path)
    records = event_store.list_event_store_operations(
        tenant_id="tenant_cli",
        operation="backup",
    )
    summary_json = json.dumps(records[0].summary, ensure_ascii=False, sort_keys=True)

    assert exit_code == 1
    assert len(records) == 1
    assert records[0].status == "rejected"
    assert records[0].summary["target_file"] == "events.backup.db"
    assert records[0].summary["detail"] == "Backup already exists: [path]"
    assert str(tmp_path) not in summary_json


def test_production_retention_apply_can_be_explicitly_unsafe_and_is_ledgered(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_REQUIRE_PRODUCTION", "false")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_prod")
    db_path = tmp_path / "events.db"

    exit_code = event_store_ops_main(
        [
            "--database-url",
            _db_url(db_path),
            "retention",
            "--tenant-id",
            "tenant_prod",
            "--apply",
            "--unsafe-local-apply",
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    event_store = SQLiteEventStore(db_path)
    records = event_store.list_event_store_operations(
        tenant_id="tenant_prod",
        operation="retention_apply",
    )

    assert exit_code == 0
    assert report["dry_run"] is False
    assert len(records) == 1
    assert records[0].actor_user_id == "event_store_cli"
    assert records[0].status == "completed"
    assert records[0].summary["source"] == "cli"
    assert records[0].summary["total_deleted"] == 0

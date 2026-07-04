import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from support_agent_lab.memory.event_store import (
    ALERT_DELIVERY_ATTEMPTED_EVENT_TYPE,
    ALERT_DELIVERY_ENQUEUED_EVENT_TYPE,
    AlertDeliveryLockLostError,
    SQLiteEventStore,
)
from support_agent_lab.models import AlertDeliveryStatus, MonitorAlertStatus, utc_now
from support_agent_lab.monitoring.alert_dispatcher import (
    alert_delivery_payload_from_record,
    build_alert_delivery_record,
    dispatch_alert_deliveries,
    enqueue_alert_deliveries,
    hash_alert_destination,
    post_alert_delivery_webhook,
    sign_alert_webhook_payload,
    summarize_alert_deliveries,
)
from support_agent_lab.monitoring.monitor import MonitorAlert


WEBHOOK_SECRET = "webhook-signing-secret-with-32-byte-minimum"


def test_alert_delivery_outbox_deduplicates_and_tracks_attempts(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    alert = _alert(severity="P1")
    destination_hash = hash_alert_destination("https://hooks.internal.test/alerts")
    record = build_alert_delivery_record(
        tenant_id="demo_tenant",
        alert=alert,
        destination_hash=destination_hash,
    )

    first, first_created = event_store.enqueue_alert_delivery(record)
    second, second_created = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=alert,
            destination_hash=destination_hash,
        )
    )
    failed = event_store.record_alert_delivery_attempt(
        first.id,
        status=AlertDeliveryStatus.failed,
        response_status_code=500,
        last_error="HTTP_500",
    )
    sent = event_store.record_alert_delivery_attempt(
        first.id,
        status=AlertDeliveryStatus.sent,
        response_status_code=202,
    )

    records = event_store.list_alert_delivery_records(tenant_id="demo_tenant")
    enqueue_events = event_store.list_events(event_type=ALERT_DELIVERY_ENQUEUED_EVENT_TYPE)
    attempt_events = event_store.list_events(event_type=ALERT_DELIVERY_ATTEMPTED_EVENT_TYPE)

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert len(records) == 1
    assert failed.status == AlertDeliveryStatus.failed
    assert failed.attempt_count == 1
    assert sent.status == AlertDeliveryStatus.sent
    assert sent.attempt_count == 2
    assert sent.delivered_at is not None
    assert sent.locked_by is None
    assert sent.locked_until is None
    assert len(enqueue_events) == 1
    assert len(attempt_events) == 2


def test_alert_delivery_claims_due_rows_once_and_recovers_expired_locks(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    first, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:order:TIMEOUT"),
            destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
        )
    )

    claimed = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-a",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
    )
    duplicate_claim = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-b",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
    )
    recovered = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-c",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
        due_at=utc_now() + timedelta(seconds=60),
    )

    assert [record.id for record in claimed] == [first.id]
    assert claimed[0].status == AlertDeliveryStatus.in_progress
    assert claimed[0].locked_by == "worker-a"
    assert claimed[0].attempt_count == 0
    assert duplicate_claim == []
    assert [record.id for record in recovered] == [first.id]
    assert recovered[0].locked_by == "worker-c"
    with pytest.raises(AlertDeliveryLockLostError):
        event_store.record_alert_delivery_attempt(
            first.id,
            status=AlertDeliveryStatus.failed,
            last_error="stale worker",
            worker_id="worker-a",
        )
    locked = event_store.list_alert_delivery_records(tenant_id="demo_tenant")[0]
    assert locked.locked_by == "worker-c"
    assert locked.attempt_count == 0


def test_alert_delivery_outbox_migrates_existing_sqlite_tables(tmp_path):
    database_path = tmp_path / "events.db"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            create table alert_delivery_outbox (
              id text primary key,
              tenant_id text not null,
              alert_key text not null,
              severity text not null,
              channel text not null,
              destination_hash text not null,
              status text not null,
              alert_first_seen_at text not null,
              alert_last_seen_at text not null,
              alert_count integer not null,
              reason text not null,
              sample_event_ids_json text not null,
              sample_run_ids_json text not null,
              payload_hash text not null,
              attempt_count integer not null default 0,
              last_attempt_at text,
              delivered_at text,
              response_status_code integer,
              last_error text,
              created_at text not null,
              updated_at text not null,
              unique (tenant_id, alert_key, alert_last_seen_at, destination_hash)
            )
            """
        )

    event_store = SQLiteEventStore(database_path)
    with sqlite3.connect(database_path) as conn:
        columns = {row[1] for row in conn.execute("pragma table_info(alert_delivery_outbox)").fetchall()}

    assert {"next_attempt_at", "dead_lettered_at", "locked_until", "locked_by"} <= columns
    event_store.health_check()


def test_enqueue_alert_deliveries_filters_severity_status_and_retries_failed_with_mock_transport(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(202, json={"ok": True})

    enqueue_report = enqueue_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        alerts=[
            _alert(severity="P1", key="agent:order:TIMEOUT"),
            _alert(severity="P2", key="agent:order:QUALITY"),
            _alert(severity="P0", key="agent:billing:PII", status=MonitorAlertStatus.silenced),
        ],
        webhook_url="https://hooks.internal.test/alerts",
        min_severity="P1",
    )
    dispatch_report = dispatch_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        max_attempts=3,
        limit=10,
        timeout_ms=1000,
        transport=httpx.MockTransport(handler),
    )

    records = event_store.list_alert_delivery_records(tenant_id="demo_tenant")
    summary = summarize_alert_deliveries(records, webhook_enabled=True)

    assert enqueue_report.enqueued_count == 1
    assert enqueue_report.skipped_count == 2
    assert dispatch_report.attempted_count == 1
    assert dispatch_report.sent_count == 1
    assert calls[0]["alert_key"] == "agent:order:TIMEOUT"
    assert summary.status == "ok"
    assert summary.last_success_at is not None


def test_alert_dispatcher_skips_delivery_when_worker_loses_lock(tmp_path, monkeypatch):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    enqueue_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        alerts=[_alert(severity="P1", key="agent:order:TIMEOUT")],
        webhook_url="https://hooks.internal.test/alerts",
        min_severity="P1",
    )

    def lose_lock(*args, **kwargs):
        raise AlertDeliveryLockLostError("lost lock")

    monkeypatch.setattr(event_store, "record_alert_delivery_attempt", lose_lock)
    report = dispatch_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        max_attempts=3,
        limit=10,
        timeout_ms=1000,
        worker_id="worker-a",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})),
    )

    assert report.claimed_count == 1
    assert report.attempted_count == 1
    assert report.skipped_count == 1
    assert report.sent_count == 0
    assert report.failed_count == 0
    assert report.deliveries == []


def test_alert_dispatcher_respects_backoff_and_dead_letters_after_max_attempts(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(503, json={"error": "down"})

    enqueue_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        alerts=[_alert(severity="P1", key="agent:order:TIMEOUT")],
        webhook_url="https://hooks.internal.test/alerts",
        min_severity="P1",
    )
    first_attempt = dispatch_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        max_attempts=2,
        limit=10,
        timeout_ms=1000,
        backoff_base_seconds=60,
        backoff_max_seconds=300,
        claim_lease_seconds=30,
        worker_id="worker-a",
        transport=httpx.MockTransport(handler),
    )
    immediate_retry = dispatch_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        max_attempts=2,
        limit=10,
        timeout_ms=1000,
        backoff_base_seconds=60,
        backoff_max_seconds=300,
        claim_lease_seconds=30,
        worker_id="worker-b",
        transport=httpx.MockTransport(handler),
    )
    due_record = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-c",
        limit=10,
        lease_seconds=30,
        max_attempts=2,
        due_at=utc_now() + timedelta(seconds=90),
    )[0]
    dead = event_store.record_alert_delivery_attempt(
        due_record.id,
        status=AlertDeliveryStatus.failed,
        response_status_code=503,
        last_error="HTTP_503",
        max_attempts=2,
        backoff_seconds=120,
    )
    dead_claim = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-d",
        limit=10,
        lease_seconds=30,
        max_attempts=2,
        due_at=utc_now() + timedelta(minutes=10),
    )
    summary = summarize_alert_deliveries(
        event_store.list_alert_delivery_records(tenant_id="demo_tenant"),
        webhook_enabled=True,
    )

    assert first_attempt.claimed_count == 1
    assert first_attempt.failed_count == 1
    assert first_attempt.deliveries[0].status == AlertDeliveryStatus.failed
    assert first_attempt.deliveries[0].next_attempt_at is not None
    assert immediate_retry.claimed_count == 0
    assert len(calls) == 1
    assert dead.status == AlertDeliveryStatus.dead
    assert dead.dead_lettered_at is not None
    assert dead.locked_by is None
    assert dead_claim == []
    assert summary.status == "failed"
    assert summary.dead_count == 1


def test_alert_delivery_webhook_sends_signed_sanitized_payload():
    seen: dict[str, object] = {}
    record = build_alert_delivery_record(
        tenant_id="demo_tenant",
        alert=_alert(severity="P0"),
        destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    status, response_status_code, last_error = post_alert_delivery_webhook(
        record=record,
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        timeout_ms=1000,
        transport=httpx.MockTransport(handler),
    )

    headers = seen["headers"]
    body = seen["body"]
    assert status == AlertDeliveryStatus.sent
    assert response_status_code == 204
    assert last_error is None
    assert body == alert_delivery_payload_from_record(record)
    assert set(body) == {
        "type",
        "delivery_id",
        "tenant_id",
        "alert_key",
        "severity",
        "reason",
        "alert_count",
        "alert_first_seen_at",
        "alert_last_seen_at",
        "sample_event_ids",
        "sample_run_ids",
    }
    assert "content" not in json.dumps(body)
    assert "arguments" not in json.dumps(body)
    expected_signature = sign_alert_webhook_payload(
        secret=WEBHOOK_SECRET,
        delivery_id=record.id,
        tenant_id=record.tenant_id,
        alert_key=record.alert_key,
        timestamp=headers["X-PSA-Timestamp"],
        body_hash=headers["X-PSA-Body-SHA256"],
    )
    assert headers["X-PSA-Signature"] == expected_signature


def test_alert_delivery_webhook_maps_http_failure_to_retryable_record_state():
    record = build_alert_delivery_record(
        tenant_id="demo_tenant",
        alert=_alert(severity="P1"),
        destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    status, response_status_code, last_error = post_alert_delivery_webhook(
        record=record,
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        timeout_ms=1000,
        transport=httpx.MockTransport(handler),
    )

    assert status == AlertDeliveryStatus.failed
    assert response_status_code == 503
    assert last_error == "HTTP_503"


def _alert(
    *,
    severity: str = "P1",
    key: str = "agent:general:PROMPT_INJECTION_ATTEMPT",
    status: MonitorAlertStatus = MonitorAlertStatus.open,
) -> MonitorAlert:
    now = utc_now()
    return MonitorAlert(
        severity=severity,
        key=key,
        count=2,
        reason="PROMPT_INJECTION_ATTEMPT clustered across 2 event(s)",
        first_seen_at=now,
        last_seen_at=now,
        sample_event_ids=["mon_1", "mon_2"],
        sample_run_ids=["run_1", "run_2"],
        status=status,
    )

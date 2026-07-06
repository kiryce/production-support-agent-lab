import hashlib
import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from support_agent_lab.memory.event_store import (
    ALERT_DELIVERY_CLOSED_EVENT_TYPE,
    ALERT_DELIVERY_ATTEMPTED_EVENT_TYPE,
    ALERT_DELIVERY_ENQUEUED_EVENT_TYPE,
    ALERT_DELIVERY_REQUEUED_EVENT_TYPE,
    AlertDeliveryLockLostError,
    SQLiteEventStore,
)
from support_agent_lab.models import AlertDeliveryStatus, MonitorAlertStatus, utc_now
from support_agent_lab.monitoring.alert_dispatcher import (
    AlertWebhookSignatureError,
    alert_delivery_payload_from_record,
    build_alert_delivery_record,
    canonical_json_bytes,
    dispatch_alert_deliveries,
    enqueue_alert_deliveries,
    hash_alert_destination,
    post_alert_delivery_webhook,
    sign_alert_webhook_payload,
    summarize_alert_deliveries,
    verify_alert_webhook_signature,
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


def test_alert_delivery_lock_refresh_extends_current_worker_lease(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    first, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:order:TIMEOUT"),
            destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
        )
    )
    base = utc_now()
    claimed = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-a",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
        due_at=base,
    )[0]

    refreshed = event_store.refresh_alert_delivery_lock(
        claimed.id,
        tenant_id="demo_tenant",
        worker_id="worker-a",
        lease_seconds=60,
        now=base + timedelta(seconds=20),
    )
    original_expiry_claim = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-b",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
        due_at=base + timedelta(seconds=40),
    )
    recovered = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-c",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
        due_at=base + timedelta(seconds=90),
    )[0]

    assert claimed.id == first.id
    assert refreshed.locked_by == "worker-a"
    assert refreshed.locked_until is not None
    assert claimed.locked_until is not None
    assert refreshed.locked_until > claimed.locked_until
    assert original_expiry_claim == []
    assert recovered.locked_by == "worker-c"
    with pytest.raises(AlertDeliveryLockLostError):
        event_store.refresh_alert_delivery_lock(
            claimed.id,
            tenant_id="demo_tenant",
            worker_id="worker-a",
            lease_seconds=60,
        )


def test_alert_delivery_metric_summary_counts_expired_in_progress_as_due(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    first, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P0", key="agent:order:TIMEOUT"),
            destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
        )
    )
    event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="worker-a",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
        due_at=utc_now() - timedelta(seconds=60),
    )

    summary = event_store.summarize_alert_delivery_records(tenant_id="demo_tenant")

    assert summary.total_count == 1
    assert summary.due_count == 1
    assert summary.counts_by_status[AlertDeliveryStatus.in_progress.value] == 1
    assert summary.counts_by_severity["P0"] == 1
    assert summary.oldest_actionable_at == first.created_at


def test_alert_dispatcher_heartbeat_summarizes_active_and_stale_workers(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    base_time = utc_now()

    running = event_store.record_alert_dispatcher_heartbeat(
        tenant_id="demo_tenant",
        worker_id="dispatcher-private-1",
        status="running",
        last_cycle_started_at=base_time,
        now=base_time,
    )
    completed = event_store.record_alert_dispatcher_heartbeat(
        tenant_id="demo_tenant",
        worker_id="dispatcher-private-1",
        status="idle",
        cycle_status="success",
        last_cycle_started_at=base_time,
        last_cycle_completed_at=base_time + timedelta(seconds=2),
        enqueued_count=1,
        claimed_count=1,
        sent_count=1,
        now=base_time + timedelta(seconds=2),
    )
    active = event_store.summarize_alert_dispatcher_heartbeats(
        tenant_id="demo_tenant",
        stale_after_seconds=60,
        now=base_time + timedelta(seconds=30),
    )
    stale = event_store.summarize_alert_dispatcher_heartbeats(
        tenant_id="demo_tenant",
        stale_after_seconds=60,
        now=base_time + timedelta(minutes=5),
    )

    assert running.status == "running"
    assert completed.cycle_count == 1
    assert completed.sent_count == 1
    assert active.status == "active"
    assert active.active_worker_count == 1
    assert active.stale_worker_count == 0
    assert active.last_success_at == base_time + timedelta(seconds=2)
    assert stale.status == "stale"
    assert stale.active_worker_count == 0
    assert stale.stale_worker_count == 1


def test_alert_delivery_summary_degrades_when_dispatcher_heartbeat_is_missing_or_stale(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    missing = event_store.summarize_alert_dispatcher_heartbeats(
        tenant_id="demo_tenant",
        stale_after_seconds=60,
    )
    stale_time = utc_now() - timedelta(minutes=5)
    event_store.record_alert_dispatcher_heartbeat(
        tenant_id="demo_tenant",
        worker_id="dispatcher-private-1",
        status="idle",
        cycle_status="success",
        last_cycle_completed_at=stale_time,
        now=stale_time,
    )
    stale = event_store.summarize_alert_dispatcher_heartbeats(
        tenant_id="demo_tenant",
        stale_after_seconds=60,
    )

    missing_summary = summarize_alert_deliveries([], webhook_enabled=True, dispatcher_heartbeat=missing)
    stale_summary = summarize_alert_deliveries([], webhook_enabled=True, dispatcher_heartbeat=stale)

    assert missing_summary.status == "degraded"
    assert missing_summary.dispatcher_status == "missing"
    assert stale_summary.status == "degraded"
    assert stale_summary.dispatcher_status == "stale"


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
        heartbeat_table = conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'alert_dispatcher_heartbeats'"
        ).fetchone()

    assert {
        "next_attempt_at",
        "dead_lettered_at",
        "locked_until",
        "locked_by",
        "operator_action",
        "operator_action_at",
        "operator_action_by",
        "operator_action_note",
    } <= columns
    assert heartbeat_table is not None
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


def test_alert_dispatcher_does_not_post_when_worker_loses_lock_before_send(tmp_path, monkeypatch):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    enqueue_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        alerts=[_alert(severity="P1", key="agent:order:TIMEOUT")],
        webhook_url="https://hooks.internal.test/alerts",
        min_severity="P1",
    )
    calls: list[httpx.Request] = []

    def lose_lock(*args, **kwargs):
        raise AlertDeliveryLockLostError("lost lock")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(event_store, "refresh_alert_delivery_lock", lose_lock)
    report = dispatch_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        max_attempts=3,
        limit=10,
        timeout_ms=1000,
        worker_id="worker-a",
        transport=httpx.MockTransport(handler),
    )

    assert report.claimed_count == 1
    assert report.attempted_count == 0
    assert report.skipped_count == 1
    assert report.sent_count == 0
    assert calls == []


def test_alert_dispatcher_revalidates_each_claimed_delivery_before_posting(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    enqueue_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        alerts=[
            _alert(severity="P1", key="agent:order:TIMEOUT"),
            _alert(severity="P1", key="agent:billing:HTTP_503"),
        ],
        webhook_url="https://hooks.internal.test/alerts",
        min_severity="P1",
    )
    calls: list[dict] = []
    stolen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            stolen.extend(
                event_store.claim_alert_delivery_records(
                    tenant_id="demo_tenant",
                    worker_id="worker-b",
                    limit=10,
                    lease_seconds=30,
                    max_attempts=3,
                    due_at=utc_now() + timedelta(seconds=5),
                )
            )
        return httpx.Response(200, json={"ok": True})

    report = dispatch_alert_deliveries(
        event_store=event_store,
        tenant_id="demo_tenant",
        webhook_url="https://hooks.internal.test/alerts",
        webhook_secret=WEBHOOK_SECRET,
        max_attempts=3,
        limit=2,
        timeout_ms=1000,
        claim_lease_seconds=1,
        worker_id="worker-a",
        transport=httpx.MockTransport(handler),
    )
    records = {
        record.alert_key: record
        for record in event_store.list_alert_delivery_records(tenant_id="demo_tenant", order="asc")
    }

    assert report.claimed_count == 2
    assert report.attempted_count == 1
    assert report.sent_count == 1
    assert report.skipped_count == 1
    assert [call["alert_key"] for call in calls] == ["agent:order:TIMEOUT"]
    assert [record.alert_key for record in stolen] == ["agent:billing:HTTP_503"]
    assert records["agent:order:TIMEOUT"].status == AlertDeliveryStatus.sent
    assert records["agent:billing:HTTP_503"].status == AlertDeliveryStatus.in_progress
    assert records["agent:billing:HTTP_503"].locked_by == "worker-b"
    assert records["agent:billing:HTTP_503"].attempt_count == 0


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


def test_alert_delivery_dead_letter_can_be_closed_or_requeued_by_operator(tmp_path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    close_target, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:order:TIMEOUT"),
            destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
        )
    )
    requeue_target, _ = event_store.enqueue_alert_delivery(
        build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:billing:HTTP_503"),
            destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
        )
    )
    close_dead = event_store.record_alert_delivery_attempt(
        close_target.id,
        status=AlertDeliveryStatus.failed,
        response_status_code=503,
        last_error="HTTP_503",
        max_attempts=1,
        backoff_seconds=60,
    )
    requeue_dead = event_store.record_alert_delivery_attempt(
        requeue_target.id,
        status=AlertDeliveryStatus.failed,
        response_status_code=500,
        last_error="HTTP_500",
        max_attempts=1,
        backoff_seconds=60,
    )
    closed = event_store.close_alert_delivery(
        close_dead.id,
        tenant_id="demo_tenant",
        actor_user_id="oncall",
        note="Downstream incident already tracked.",
    )
    closed_summary = summarize_alert_deliveries(
        event_store.list_alert_delivery_records(tenant_id="demo_tenant"),
        webhook_enabled=True,
    )
    requeued = event_store.requeue_alert_delivery(
        requeue_dead.id,
        tenant_id="demo_tenant",
        actor_user_id="oncall",
        note="Webhook restored.",
    )
    claimed = event_store.claim_alert_delivery_records(
        tenant_id="demo_tenant",
        worker_id="dispatcher-a",
        limit=10,
        lease_seconds=30,
        max_attempts=3,
    )
    action_events = event_store.list_events(
        tenant_id="demo_tenant",
        event_type=ALERT_DELIVERY_REQUEUED_EVENT_TYPE,
    )
    close_events = event_store.list_events(
        tenant_id="demo_tenant",
        event_type=ALERT_DELIVERY_CLOSED_EVENT_TYPE,
    )

    assert close_dead.status == AlertDeliveryStatus.dead
    assert requeue_dead.status == AlertDeliveryStatus.dead
    assert closed.status == AlertDeliveryStatus.closed
    assert closed.operator_action == "closed"
    assert closed.operator_action_by == "oncall"
    assert closed.operator_action_note == "Downstream incident already tracked."
    assert closed.last_error == "HTTP_503"
    assert closed_summary.status == "failed"
    assert closed_summary.dead_count == 1
    assert closed_summary.closed_count == 1
    assert requeued.status == AlertDeliveryStatus.pending
    assert requeued.attempt_count == 0
    assert requeued.dead_lettered_at is None
    assert requeued.last_error is None
    assert [record.id for record in claimed] == [requeue_dead.id]
    assert action_events[0].payload["operator_action"] == "requeued"
    assert close_events[0].payload["operator_action"] == "closed"


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


def test_alert_webhook_signature_verifier_accepts_sender_contract_and_rejects_tampering():
    record = build_alert_delivery_record(
        tenant_id="demo_tenant",
        alert=_alert(severity="P0", key="agent:order:TIMEOUT"),
        destination_hash=hash_alert_destination("https://hooks.internal.test/alerts"),
    )
    payload = alert_delivery_payload_from_record(record)
    body = canonical_json_bytes(payload)
    body_hash = hashlib.sha256(body).hexdigest()
    timestamp = "1000"
    headers = {
        "X-PSA-Delivery-ID": record.id,
        "X-PSA-Tenant-ID": record.tenant_id,
        "X-PSA-Alert-Key": record.alert_key,
        "X-PSA-Timestamp": timestamp,
        "X-PSA-Body-SHA256": body_hash,
        "X-PSA-Signature": sign_alert_webhook_payload(
            secret=WEBHOOK_SECRET,
            delivery_id=record.id,
            tenant_id=record.tenant_id,
            alert_key=record.alert_key,
            timestamp=timestamp,
            body_hash=body_hash,
        ),
    }

    verified = verify_alert_webhook_signature(
        secret=WEBHOOK_SECRET,
        headers=headers,
        body=body,
        max_age_seconds=300,
        expected_tenant_id="demo_tenant",
        now_epoch_seconds=1000,
    )

    assert verified.delivery_id == record.id
    assert verified.payload["severity"] == "P0"
    with pytest.raises(AlertWebhookSignatureError, match="expired"):
        verify_alert_webhook_signature(
            secret=WEBHOOK_SECRET,
            headers=headers,
            body=body,
            max_age_seconds=300,
            expected_tenant_id="demo_tenant",
            now_epoch_seconds=2000,
        )
    tampered_body = canonical_json_bytes({**payload, "alert_key": "agent:other:TIMEOUT"})
    with pytest.raises(AlertWebhookSignatureError, match="does not match"):
        verify_alert_webhook_signature(
            secret=WEBHOOK_SECRET,
            headers=headers,
            body=tampered_body,
            max_age_seconds=300,
            expected_tenant_id="demo_tenant",
            now_epoch_seconds=1000,
        )
    mismatched_payload = canonical_json_bytes({**payload, "delivery_id": "deliv_other"})
    mismatched_hash = hashlib.sha256(mismatched_payload).hexdigest()
    mismatched_headers = {
        **headers,
        "X-PSA-Body-SHA256": mismatched_hash,
        "X-PSA-Signature": sign_alert_webhook_payload(
            secret=WEBHOOK_SECRET,
            delivery_id=record.id,
            tenant_id=record.tenant_id,
            alert_key=record.alert_key,
            timestamp=timestamp,
            body_hash=mismatched_hash,
        ),
    }
    with pytest.raises(AlertWebhookSignatureError, match="payload delivery_id"):
        verify_alert_webhook_signature(
            secret=WEBHOOK_SECRET,
            headers=mismatched_headers,
            body=mismatched_payload,
            max_age_seconds=300,
            expected_tenant_id="demo_tenant",
            now_epoch_seconds=1000,
        )
    with pytest.raises(AlertWebhookSignatureError, match="tenant"):
        verify_alert_webhook_signature(
            secret=WEBHOOK_SECRET,
            headers=headers,
            body=body,
            max_age_seconds=300,
            expected_tenant_id="other_tenant",
            now_epoch_seconds=1000,
        )


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

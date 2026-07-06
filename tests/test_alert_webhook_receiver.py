import hashlib
import json
import time

from fastapi.testclient import TestClient

from support_agent_lab.api.main import app, get_container
from support_agent_lab.bootstrap import create_container
from support_agent_lab.config import get_settings
from support_agent_lab.models import MonitorAlertStatus, utc_now
from support_agent_lab.monitoring.alert_dispatcher import (
    alert_delivery_payload_from_record,
    build_alert_delivery_record,
    canonical_json_bytes,
    hash_alert_destination,
    sign_alert_webhook_payload,
)
from support_agent_lab.monitoring.monitor import MonitorAlert


WEBHOOK_SECRET = "webhook-signing-secret-with-32-byte-minimum"
WEBHOOK_PATH = "/api/v1/webhooks/monitor/alerts"


def test_signed_alert_webhook_receiver_records_receipts_without_actor_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{tmp_path / 'events.db'}")
    monkeypatch.setenv("APP_REQUEST_SIGNATURE_REQUIRED", "true")
    monkeypatch.setenv("APP_MONITOR_ALERT_WEBHOOK_RECEIVER_ENABLED", "true")
    monkeypatch.setenv("APP_MONITOR_ALERT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    app_container = create_container()
    app.dependency_overrides[get_container] = lambda: app_container
    try:
        client = TestClient(app)
        record = build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:order:TIMEOUT"),
            destination_hash=hash_alert_destination("http://testserver/api/v1/webhooks/monitor/alerts"),
        )
        body, headers = _signed_body_and_headers(record)

        first = client.post(WEBHOOK_PATH, content=body, headers=headers)
        duplicate = client.post(WEBHOOK_PATH, content=body, headers=headers)
        listed = client.get(
            "/api/v1/admin/monitor/alert-webhook-receipts",
            headers={"X-Demo-Role": "admin"},
        )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["duplicate_count"] == 0
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["duplicate_count"] == 1
    assert listed.status_code == 401
    receipts = app_container.event_store.list_alert_webhook_receipts(tenant_id="demo_tenant")
    assert receipts[0].delivery_id == record.id
    assert receipts[0].alert_key == "agent:order:TIMEOUT"
    assert receipts[0].duplicate_count == 1
    response_text = json.dumps(first.json())
    assert "PRIVATE" not in response_text
    assert "sample_event_ids" not in response_text
    assert "X-PSA-Signature" not in response_text


def test_alert_webhook_receiver_rejects_tampering_and_conflicting_replays(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{tmp_path / 'events.db'}")
    monkeypatch.setenv("APP_MONITOR_ALERT_WEBHOOK_RECEIVER_ENABLED", "true")
    monkeypatch.setenv("APP_MONITOR_ALERT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    app_container = create_container()
    app.dependency_overrides[get_container] = lambda: app_container
    try:
        client = TestClient(app)
        record = build_alert_delivery_record(
            tenant_id="demo_tenant",
            alert=_alert(severity="P1", key="agent:billing:HTTP_503"),
            destination_hash=hash_alert_destination("http://testserver/api/v1/webhooks/monitor/alerts"),
        )
        body, headers = _signed_body_and_headers(record)
        tampered = canonical_json_bytes({**alert_delivery_payload_from_record(record), "alert_count": 99})

        bad_hash = client.post(WEBHOOK_PATH, content=tampered, headers=headers)
        accepted = client.post(WEBHOOK_PATH, content=body, headers=headers)
        listed = client.get(
            "/api/v1/admin/monitor/alert-webhook-receipts",
            headers={"X-Demo-Role": "admin"},
        )
        changed_payload = canonical_json_bytes(
            {**alert_delivery_payload_from_record(record), "severity": "P0"}
        )
        changed_hash = hashlib.sha256(changed_payload).hexdigest()
        changed_headers = {
            **headers,
            "X-PSA-Body-SHA256": changed_hash,
            "X-PSA-Signature": sign_alert_webhook_payload(
                secret=WEBHOOK_SECRET,
                delivery_id=record.id,
                tenant_id=record.tenant_id,
                alert_key=record.alert_key,
                timestamp=headers["X-PSA-Timestamp"],
                body_hash=changed_hash,
            ),
        }
        conflict = client.post(WEBHOOK_PATH, content=changed_payload, headers=changed_headers)
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert bad_hash.status_code == 401
    assert accepted.status_code == 200
    assert listed.status_code == 200
    assert listed.json()[0]["delivery_id"] == record.id
    assert "PRIVATE reason" not in json.dumps(listed.json())
    assert "sample_event_ids" not in json.dumps(listed.json())
    assert "X-PSA-Signature" not in json.dumps(listed.json())
    assert conflict.status_code == 409
    assert app_container.event_store.list_alert_webhook_receipts(tenant_id="demo_tenant")[0].duplicate_count == 0


def _signed_body_and_headers(record):
    payload = alert_delivery_payload_from_record(record)
    body = canonical_json_bytes(payload)
    body_hash = hashlib.sha256(body).hexdigest()
    timestamp = str(int(time.time()))
    signature = sign_alert_webhook_payload(
        secret=WEBHOOK_SECRET,
        delivery_id=record.id,
        tenant_id=record.tenant_id,
        alert_key=record.alert_key,
        timestamp=timestamp,
        body_hash=body_hash,
    )
    return body, {
        "Content-Type": "application/json",
        "User-Agent": "receiver-test",
        "X-PSA-Delivery-ID": record.id,
        "X-PSA-Tenant-ID": record.tenant_id,
        "X-PSA-Alert-Key": record.alert_key,
        "X-PSA-Timestamp": timestamp,
        "X-PSA-Body-SHA256": body_hash,
        "X-PSA-Signature": signature,
    }


def _alert(
    *,
    severity: str,
    key: str,
) -> MonitorAlert:
    now = utc_now()
    return MonitorAlert(
        severity=severity,
        key=key,
        count=2,
        reason="PRIVATE reason must not be stored by receiver",
        first_seen_at=now,
        last_seen_at=now,
        sample_event_ids=["mon_private_1", "mon_private_2"],
        sample_run_ids=["run_private_1", "run_private_2"],
        status=MonitorAlertStatus.open,
    )

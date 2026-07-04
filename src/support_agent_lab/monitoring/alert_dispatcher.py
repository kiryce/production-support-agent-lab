from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from support_agent_lab.memory.event_store import AlertDeliveryLockLostError, SQLiteEventStore
from support_agent_lab.models import AlertDeliveryRecord, AlertDeliveryStatus, new_id, utc_now
from support_agent_lab.monitoring.monitor import MonitorAlert


AlertDeliveryHealthStatus = Literal["ok", "queued", "degraded", "failed", "disabled", "unknown"]

ACTIVE_ALERT_STATUSES = {"open", "acknowledged", "investigating"}
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class AlertDeliverySummary(BaseModel):
    status: AlertDeliveryHealthStatus
    webhook_enabled: bool
    pending_count: int
    in_progress_count: int = 0
    failed_count: int
    dead_count: int = 0
    closed_count: int = 0
    oldest_pending_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class AlertDispatchReport(BaseModel):
    webhook_enabled: bool
    enqueued_count: int = 0
    existing_count: int = 0
    skipped_count: int = 0
    claimed_count: int = 0
    attempted_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    dead_count: int = 0
    deliveries: list[AlertDeliveryRecord] = Field(default_factory=list)


def enqueue_alert_deliveries(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    alerts: list[MonitorAlert],
    webhook_url: str | None,
    min_severity: str = "P1",
) -> AlertDispatchReport:
    if not webhook_url:
        return AlertDispatchReport(webhook_enabled=False, skipped_count=len(alerts))
    destination_hash = hash_alert_destination(webhook_url)
    report = AlertDispatchReport(webhook_enabled=True)
    for alert in alerts:
        if not should_enqueue_alert(alert, min_severity=min_severity):
            report.skipped_count += 1
            continue
        record = build_alert_delivery_record(
            tenant_id=tenant_id,
            alert=alert,
            destination_hash=destination_hash,
        )
        persisted, created = event_store.enqueue_alert_delivery(record)
        report.deliveries.append(persisted)
        if created:
            report.enqueued_count += 1
        else:
            report.existing_count += 1
    return report


def dispatch_alert_deliveries(
    *,
    event_store: SQLiteEventStore,
    tenant_id: str,
    webhook_url: str | None,
    webhook_secret: str | None,
    max_attempts: int,
    limit: int,
    timeout_ms: int,
    backoff_base_seconds: int = 60,
    backoff_max_seconds: int = 900,
    claim_lease_seconds: int = 120,
    worker_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> AlertDispatchReport:
    if not webhook_url:
        return AlertDispatchReport(webhook_enabled=False)
    worker_id = worker_id or new_id("dispatcher")
    candidates = event_store.claim_alert_delivery_records(
        tenant_id=tenant_id,
        limit=limit,
        worker_id=worker_id,
        lease_seconds=claim_lease_seconds,
        max_attempts=max_attempts,
    )
    report = AlertDispatchReport(webhook_enabled=True, claimed_count=len(candidates))
    for record in candidates:
        status, response_status_code, last_error = post_alert_delivery_webhook(
            record=record,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            timeout_ms=timeout_ms,
            transport=transport,
        )
        report.attempted_count += 1
        try:
            updated = event_store.record_alert_delivery_attempt(
                record.id,
                status=status,
                response_status_code=response_status_code,
                last_error=last_error,
                max_attempts=max_attempts,
                backoff_seconds=delivery_backoff_seconds(
                    attempt_count=record.attempt_count + 1,
                    base_seconds=backoff_base_seconds,
                    max_seconds=backoff_max_seconds,
                )
                if status == AlertDeliveryStatus.failed
                else None,
                worker_id=worker_id,
            )
        except AlertDeliveryLockLostError:
            report.skipped_count += 1
            continue
        if updated.status == AlertDeliveryStatus.sent:
            report.sent_count += 1
        elif updated.status == AlertDeliveryStatus.dead:
            report.dead_count += 1
        else:
            report.failed_count += 1
        report.deliveries.append(updated)
    return report


def summarize_alert_deliveries(
    records: list[AlertDeliveryRecord],
    *,
    webhook_enabled: bool,
    backlog_threshold: int = 10,
) -> AlertDeliverySummary:
    if not webhook_enabled:
        return AlertDeliverySummary(
            status="disabled",
            webhook_enabled=False,
            pending_count=0,
            in_progress_count=0,
            failed_count=0,
            dead_count=0,
            closed_count=0,
            next_attempt_at=None,
        )
    pending = [record for record in records if record.status == AlertDeliveryStatus.pending]
    in_progress = [record for record in records if record.status == AlertDeliveryStatus.in_progress]
    failed = [record for record in records if record.status == AlertDeliveryStatus.failed]
    dead = [record for record in records if record.status == AlertDeliveryStatus.dead]
    closed = [record for record in records if record.status == AlertDeliveryStatus.closed]
    last_attempts = [record.last_attempt_at for record in records if record.last_attempt_at]
    next_attempts = [record.next_attempt_at for record in records if record.next_attempt_at]
    successes = [record.delivered_at for record in records if record.delivered_at]
    errors = [record.last_error for record in records if record.last_error]
    status: AlertDeliveryHealthStatus = "ok"
    if failed or dead:
        status = "failed"
    elif len(pending) + len(in_progress) >= backlog_threshold:
        status = "degraded"
    elif pending or in_progress:
        status = "queued"
    return AlertDeliverySummary(
        status=status,
        webhook_enabled=True,
        pending_count=len(pending),
        in_progress_count=len(in_progress),
        failed_count=len(failed),
        dead_count=len(dead),
        closed_count=len(closed),
        oldest_pending_at=min((record.created_at for record in [*pending, *in_progress]), default=None),
        next_attempt_at=min(next_attempts) if next_attempts else None,
        last_attempt_at=max(last_attempts) if last_attempts else None,
        last_success_at=max(successes) if successes else None,
        last_error=errors[0] if errors else None,
    )


def should_enqueue_alert(alert: MonitorAlert, *, min_severity: str = "P1") -> bool:
    if SEVERITY_RANK.get(alert.severity, 99) > SEVERITY_RANK.get(min_severity, 99):
        return False
    status = alert.status.value if hasattr(alert.status, "value") else str(alert.status)
    return status in ACTIVE_ALERT_STATUSES


def build_alert_delivery_record(
    *,
    tenant_id: str,
    alert: MonitorAlert,
    destination_hash: str,
) -> AlertDeliveryRecord:
    delivery_id = new_id("deliv")
    now = utc_now()
    payload = alert_delivery_payload(
        delivery_id=delivery_id,
        tenant_id=tenant_id,
        alert_key=alert.key,
        severity=alert.severity,
        reason=alert.reason,
        alert_count=alert.count,
        alert_first_seen_at=alert.first_seen_at,
        alert_last_seen_at=alert.last_seen_at,
        sample_event_ids=alert.sample_event_ids,
        sample_run_ids=alert.sample_run_ids,
    )
    return AlertDeliveryRecord(
        id=delivery_id,
        tenant_id=tenant_id,
        alert_key=alert.key,
        severity=alert.severity,
        destination_hash=destination_hash,
        alert_first_seen_at=alert.first_seen_at,
        alert_last_seen_at=alert.last_seen_at,
        alert_count=alert.count,
        reason=alert.reason,
        sample_event_ids=alert.sample_event_ids,
        sample_run_ids=alert.sample_run_ids,
        payload_hash=hash_json_payload(payload),
        created_at=now,
        updated_at=now,
    )


def post_alert_delivery_webhook(
    *,
    record: AlertDeliveryRecord,
    webhook_url: str,
    webhook_secret: str | None,
    timeout_ms: int,
    transport: httpx.BaseTransport | None = None,
) -> tuple[AlertDeliveryStatus, int | None, str | None]:
    payload = alert_delivery_payload_from_record(record)
    body = canonical_json_bytes(payload)
    timestamp = str(int(utc_now().timestamp()))
    body_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "production-support-agent-lab/alert-dispatcher",
        "X-PSA-Delivery-ID": record.id,
        "X-PSA-Tenant-ID": record.tenant_id,
        "X-PSA-Alert-Key": record.alert_key,
        "X-PSA-Timestamp": timestamp,
        "X-PSA-Body-SHA256": body_hash,
    }
    if webhook_secret:
        headers["X-PSA-Signature"] = sign_alert_webhook_payload(
            secret=webhook_secret,
            delivery_id=record.id,
            tenant_id=record.tenant_id,
            alert_key=record.alert_key,
            timestamp=timestamp,
            body_hash=body_hash,
        )
    try:
        with httpx.Client(timeout=timeout_ms / 1000, transport=transport) as client:
            response = client.post(webhook_url, content=body, headers=headers)
    except httpx.TimeoutException:
        return AlertDeliveryStatus.failed, None, "TIMEOUT"
    except httpx.HTTPError as exc:
        return AlertDeliveryStatus.failed, None, exc.__class__.__name__
    if 200 <= response.status_code < 300:
        return AlertDeliveryStatus.sent, response.status_code, None
    return AlertDeliveryStatus.failed, response.status_code, f"HTTP_{response.status_code}"


def alert_delivery_payload_from_record(record: AlertDeliveryRecord) -> dict[str, object]:
    return alert_delivery_payload(
        delivery_id=record.id,
        tenant_id=record.tenant_id,
        alert_key=record.alert_key,
        severity=record.severity,
        reason=record.reason,
        alert_count=record.alert_count,
        alert_first_seen_at=record.alert_first_seen_at,
        alert_last_seen_at=record.alert_last_seen_at,
        sample_event_ids=record.sample_event_ids,
        sample_run_ids=record.sample_run_ids,
    )


def alert_delivery_payload(
    *,
    delivery_id: str,
    tenant_id: str,
    alert_key: str,
    severity: str,
    reason: str,
    alert_count: int,
    alert_first_seen_at: datetime,
    alert_last_seen_at: datetime,
    sample_event_ids: list[str],
    sample_run_ids: list[str],
) -> dict[str, object]:
    return {
        "type": "monitor.alert",
        "delivery_id": delivery_id,
        "tenant_id": tenant_id,
        "alert_key": alert_key,
        "severity": severity,
        "reason": reason,
        "alert_count": alert_count,
        "alert_first_seen_at": alert_first_seen_at.isoformat(),
        "alert_last_seen_at": alert_last_seen_at.isoformat(),
        "sample_event_ids": sample_event_ids[:3],
        "sample_run_ids": sample_run_ids[:3],
    }


def sign_alert_webhook_payload(
    *,
    secret: str,
    delivery_id: str,
    tenant_id: str,
    alert_key: str,
    timestamp: str,
    body_hash: str,
) -> str:
    message = "\n".join(["v1", delivery_id, tenant_id, alert_key, timestamp, body_hash])
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def hash_alert_destination(webhook_url: str) -> str:
    return hashlib.sha256(webhook_url.encode("utf-8")).hexdigest()


def hash_json_payload(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def delivery_backoff_seconds(*, attempt_count: int, base_seconds: int, max_seconds: int) -> int:
    if attempt_count <= 1:
        return min(base_seconds, max_seconds)
    return min(base_seconds * (2 ** (attempt_count - 1)), max_seconds)


def canonical_json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

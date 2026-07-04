from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from support_agent_lab.models import MonitorAlertStatus, MonitorAlertTriageEvent, MonitorEvent, utc_now
from support_agent_lab.monitoring.monitor import summarize_monitor_events


ACTIVE_MONITOR_ALERT_STATUSES = {
    MonitorAlertStatus.open,
    MonitorAlertStatus.acknowledged,
    MonitorAlertStatus.investigating,
}
RESOLVED_MONITOR_ALERT_STATUSES = {
    MonitorAlertStatus.resolved,
    MonitorAlertStatus.silenced,
}
MONITOR_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
MONITOR_TRIAGE_HEALTH_STATUSES = ("ok", "degraded", "critical")
MONITOR_ALERT_SEVERITIES = ("P0", "P1", "P2", "P3")


class MonitorTriageMetricsWindow(BaseModel):
    conversation_id: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int
    order: Literal["asc", "desc"]
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class MonitorTriageMetricsResponse(BaseModel):
    source: Literal["event_store", "live"]
    generated_at: datetime
    window: MonitorTriageMetricsWindow
    total_events: int
    healthy_events: int
    alerted_events: int
    alert_rate: float
    grounded_rate: float
    policy_compliance_rate: float
    human_review_rate: float
    high_risk_events: int
    critical_events: int
    ungrounded_events: int
    policy_violations: int
    human_review_events: int
    pii_leak_events: int
    by_risk_level: dict[str, int]
    by_intent: dict[str, int]
    by_failure_type: dict[str, int]
    by_alert_failure_type: dict[str, int]
    alert_count: int
    active_alert_count: int
    resolved_alert_count: int
    silenced_alert_count: int
    assigned_alert_count: int
    untriaged_alert_count: int
    unassigned_active_alert_count: int
    new_events_since_triage_count: int
    stale_active_alert_count: int
    stale_threshold_seconds: int
    by_severity: dict[str, int]
    active_by_severity: dict[str, int]
    by_status: dict[str, int]
    worst_active_severity: Literal["P0", "P1", "P2", "P3"] | None = None
    health_status: Literal["ok", "degraded", "critical"]
    mtta_seconds: int | None = None
    mttr_seconds: int | None = None
    oldest_active_alert_at: datetime | None = None
    latest_triage_at: datetime | None = None


def monitor_triage_metrics_response(
    *,
    source: Literal["event_store", "live"],
    events: list[MonitorEvent],
    triage_events: list[MonitorAlertTriageEvent],
    conversation_id: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    limit: int,
    order: Literal["asc", "desc"],
    stale_after: timedelta,
) -> MonitorTriageMetricsResponse:
    generated_at = utc_now()
    summary = summarize_monitor_events(events, triage_events=triage_events)
    timestamps = [event.timestamp for event in events]
    alerted_events = [event for event in events if monitor_event_alerted(event)]
    healthy_events = len(events) - len(alerted_events)
    alert_keys = {alert.key for alert in summary.alerts}
    relevant_triage_events = [
        event
        for event in sorted(triage_events, key=lambda item: item.created_at)
        if event.alert_key in alert_keys
    ]
    triage_by_key: dict[str, list[MonitorAlertTriageEvent]] = {}
    for event in relevant_triage_events:
        triage_by_key.setdefault(event.alert_key, []).append(event)

    active_alerts = [
        alert
        for alert in summary.alerts
        if monitor_status(alert.status) in ACTIVE_MONITOR_ALERT_STATUSES
    ]
    by_status = {status.value: 0 for status in MonitorAlertStatus}
    for alert in summary.alerts:
        by_status[monitor_status(alert.status).value] += 1
    by_severity = {severity: 0 for severity in MONITOR_SEVERITY_RANK}
    for alert in summary.alerts:
        by_severity[alert.severity] += 1
    active_by_severity = {severity: 0 for severity in MONITOR_SEVERITY_RANK}
    for alert in active_alerts:
        active_by_severity[alert.severity] += 1

    stale_alerts = [
        alert
        for alert in active_alerts
        if generated_at - alert.first_seen_at >= stale_after
    ]
    response_deltas: list[int] = []
    resolution_deltas: list[int] = []
    alerts_by_key = {alert.key: alert for alert in summary.alerts}
    for alert_key, events_for_key in triage_by_key.items():
        alert = alerts_by_key.get(alert_key)
        if not alert:
            continue
        first_triage = events_for_key[0]
        response_deltas.append(duration_seconds(alert.first_seen_at, first_triage.created_at))
        resolution = next(
            (
                event
                for event in events_for_key
                if event.status in RESOLVED_MONITOR_ALERT_STATUSES
            ),
            None,
        )
        if resolution and alert.last_seen_at <= resolution.created_at:
            resolution_deltas.append(duration_seconds(alert.first_seen_at, resolution.created_at))

    active_with_new_p0 = any(
        alert.severity == "P0"
        and (monitor_status(alert.status) in ACTIVE_MONITOR_ALERT_STATUSES or alert.new_events_since_triage)
        for alert in summary.alerts
    )
    has_active_or_new = bool(active_alerts or any(alert.new_events_since_triage for alert in summary.alerts))
    health_status: Literal["ok", "degraded", "critical"] = (
        "critical" if active_with_new_p0 else "degraded" if has_active_or_new else "ok"
    )
    worst_active = min(
        (alert.severity for alert in active_alerts),
        key=lambda severity: MONITOR_SEVERITY_RANK.get(severity, 9),
        default=None,
    )

    return MonitorTriageMetricsResponse(
        source=source,
        generated_at=generated_at,
        window=MonitorTriageMetricsWindow(
            conversation_id=conversation_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            order=order,
            first_seen_at=min(timestamps) if timestamps else None,
            last_seen_at=max(timestamps) if timestamps else None,
        ),
        total_events=len(events),
        healthy_events=healthy_events,
        alerted_events=len(alerted_events),
        alert_rate=round(len(alerted_events) / len(events), 4) if events else 0.0,
        grounded_rate=summary.grounded_rate,
        policy_compliance_rate=summary.policy_compliance_rate,
        human_review_rate=summary.human_review_rate,
        high_risk_events=sum(1 for event in events if event.risk_level.value in {"high", "critical"}),
        critical_events=sum(1 for event in events if event.risk_level.value == "critical"),
        ungrounded_events=sum(1 for event in events if not event.grounded),
        policy_violations=sum(1 for event in events if not event.policy_compliant),
        human_review_events=sum(1 for event in events if event.needs_human_review),
        pii_leak_events=sum(1 for event in events if event.pii_leak),
        by_risk_level=summary.by_risk_level,
        by_intent=summary.by_intent,
        by_failure_type=summary.by_failure_type,
        by_alert_failure_type=dict(
            Counter(label for event in alerted_events for label in monitor_failure_labels(event))
        ),
        alert_count=len(summary.alerts),
        active_alert_count=len(active_alerts),
        resolved_alert_count=by_status[MonitorAlertStatus.resolved.value],
        silenced_alert_count=by_status[MonitorAlertStatus.silenced.value],
        assigned_alert_count=sum(1 for alert in summary.alerts if alert.assignee_user_id),
        untriaged_alert_count=sum(1 for alert in summary.alerts if alert.last_triage_event_id is None),
        unassigned_active_alert_count=sum(1 for alert in active_alerts if not alert.assignee_user_id),
        new_events_since_triage_count=sum(1 for alert in summary.alerts if alert.new_events_since_triage),
        stale_active_alert_count=len(stale_alerts),
        stale_threshold_seconds=int(stale_after.total_seconds()),
        by_severity=by_severity,
        active_by_severity=active_by_severity,
        by_status=by_status,
        worst_active_severity=worst_active,
        health_status=health_status,
        mtta_seconds=average_seconds(response_deltas),
        mttr_seconds=average_seconds(resolution_deltas),
        oldest_active_alert_at=min((alert.first_seen_at for alert in active_alerts), default=None),
        latest_triage_at=max((event.created_at for event in relevant_triage_events), default=None),
    )


def monitor_status(status: MonitorAlertStatus | str) -> MonitorAlertStatus:
    return status if isinstance(status, MonitorAlertStatus) else MonitorAlertStatus(status)


def monitor_event_alerted(event: MonitorEvent) -> bool:
    return bool(
        event.failure_types
        or not event.grounded
        or not event.policy_compliant
        or event.needs_human_review
        or event.pii_leak
    )


def monitor_failure_labels(event: MonitorEvent) -> list[str]:
    if event.failure_types:
        return event.failure_types
    if monitor_event_alerted(event):
        return ["QUALITY_REVIEW"]
    return ["none"]


def duration_seconds(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def average_seconds(values: list[int]) -> int | None:
    if not values:
        return None
    return round(sum(values) / len(values))

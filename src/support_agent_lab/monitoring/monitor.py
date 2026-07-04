from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from support_agent_lab.models import (
    AgentResponse,
    MonitorAlertStatus,
    MonitorAlertTriageEvent,
    MonitorEvent,
    RiskLevel,
    ToolStatus,
)


class MonitorAlert(BaseModel):
    severity: Literal["P0", "P1", "P2", "P3"]
    key: str
    count: int
    reason: str
    first_seen_at: datetime
    last_seen_at: datetime
    sample_event_ids: list[str] = Field(default_factory=list)
    sample_run_ids: list[str] = Field(default_factory=list)
    status: MonitorAlertStatus = MonitorAlertStatus.open
    assignee_user_id: str | None = None
    last_triage_event_id: str | None = None
    last_triage_at: datetime | None = None
    last_triage_note: str | None = None
    new_events_since_triage: bool = False


class MonitorSummary(BaseModel):
    total_events: int
    by_risk_level: dict[str, int]
    by_intent: dict[str, int]
    by_failure_type: dict[str, int]
    grounded_rate: float
    policy_compliance_rate: float
    human_review_rate: float
    alerts: list[MonitorAlert]


class OnlineMonitorAgent:
    def __init__(self) -> None:
        self.events: list[MonitorEvent] = []

    def review(self, response: AgentResponse) -> MonitorEvent:
        trace = response.trace
        assert trace.intent is not None
        policy_failures = [finding for finding in trace.policy_findings if finding.should_block or finding.should_escalate]
        tool_failures = [tool for tool in trace.tool_results if tool.status == ToolStatus.failed]
        has_citations = bool(response.citations)
        needs_review = bool(policy_failures or tool_failures or response.handoff_required)
        pii_leak = any(finding.code == "PII_IN_OUTPUT" for finding in trace.policy_findings)
        risk = RiskLevel.low
        if any(finding.risk_level == RiskLevel.critical for finding in trace.policy_findings):
            risk = RiskLevel.critical
        elif any(finding.risk_level == RiskLevel.high for finding in trace.policy_findings):
            risk = RiskLevel.high
        elif needs_review:
            risk = RiskLevel.medium
        event = MonitorEvent(
            conversation_id=trace.conversation_id,
            run_id=trace.id,
            agent_version=trace.agent_version,
            user_intent=trace.intent.primary,
            risk_level=risk,
            grounded=has_citations or trace.intent.primary.value in {"complaint", "account_security"},
            policy_compliant=not policy_failures,
            pii_leak=pii_leak,
            needs_human_review=needs_review,
            failure_types=[*(finding.code for finding in policy_failures), *(tool.error_code or "TOOL_FAILED" for tool in tool_failures)],
            summary=self._summarize(response),
        )
        if event.failure_types or not event.grounded or not event.policy_compliant or event.needs_human_review:
            event.alert_key = monitor_alert_key(event)
        self.events.append(event)
        return event

    def summarize(self) -> MonitorSummary:
        return summarize_monitor_events(self.events)

    def _summarize(self, response: AgentResponse) -> str:
        intent = response.trace.intent.primary.value if response.trace.intent else "unknown"
        tools = ", ".join(tool.name for tool in response.trace.tool_results) or "no tools"
        return f"intent={intent}; tools={tools}; handoff={response.handoff_required}"


def summarize_monitor_events(
    events: Sequence[MonitorEvent],
    triage_events: Sequence[MonitorAlertTriageEvent] | None = None,
) -> MonitorSummary:
    total = len(events)
    if total == 0:
        return MonitorSummary(
            total_events=0,
            by_risk_level={},
            by_intent={},
            by_failure_type={},
            grounded_rate=1.0,
            policy_compliance_rate=1.0,
            human_review_rate=0.0,
            alerts=[],
        )

    risk_counts = Counter(event.risk_level.value for event in events)
    intent_counts = Counter(event.user_intent.value for event in events)
    failure_counts = Counter(
        failure for event in events for failure in event.failure_types
    )
    alerts_by_key: dict[str, list[MonitorEvent]] = defaultdict(list)
    for event in events:
        if not event.failure_types and event.grounded and event.policy_compliant and not event.needs_human_review:
            continue
        key = event.alert_key or monitor_alert_key(event)
        alerts_by_key[key].append(event)

    alerts = [
        _build_alert(key, grouped)
        for key, grouped in alerts_by_key.items()
    ]
    if triage_events:
        apply_monitor_triage(alerts, triage_events)
    alerts.sort(key=lambda alert: (_severity_rank(alert.severity), -alert.count, alert.key))
    return MonitorSummary(
        total_events=total,
        by_risk_level=dict(risk_counts),
        by_intent=dict(intent_counts),
        by_failure_type=dict(failure_counts),
        grounded_rate=round(sum(1 for event in events if event.grounded) / total, 4),
        policy_compliance_rate=round(sum(1 for event in events if event.policy_compliant) / total, 4),
        human_review_rate=round(sum(1 for event in events if event.needs_human_review) / total, 4),
        alerts=alerts,
    )


def apply_monitor_triage(
    alerts: list[MonitorAlert],
    triage_events: Sequence[MonitorAlertTriageEvent],
) -> None:
    alerts_by_key = {alert.key: alert for alert in alerts}
    for event in sorted(triage_events, key=lambda item: item.created_at):
        alert = alerts_by_key.get(event.alert_key)
        if not alert:
            continue
        if event.status is not None:
            alert.status = event.status
        if event.assignee_user_id is not None:
            alert.assignee_user_id = event.assignee_user_id
        if event.note:
            alert.last_triage_note = event.note
        alert.last_triage_event_id = event.id
        alert.last_triage_at = event.created_at
        has_newer_events = alert.last_seen_at > event.created_at
        alert.new_events_since_triage = has_newer_events
        if has_newer_events and alert.status == MonitorAlertStatus.resolved:
            alert.status = MonitorAlertStatus.open


def monitor_alert_key(event: MonitorEvent) -> str:
    failure_key = "+".join(sorted(event.failure_types)) or "QUALITY_REVIEW"
    return f"{event.agent_version}:{event.user_intent.value}:{failure_key}"


def _build_alert(key: str, events: list[MonitorEvent]) -> MonitorAlert:
    severity = _severity_for(events)
    failures = Counter(failure for event in events for failure in event.failure_types)
    top_failure = failures.most_common(1)[0][0] if failures else "QUALITY_REVIEW"
    reason = f"{top_failure} clustered across {len(events)} event(s)"
    return MonitorAlert(
        severity=severity,
        key=key,
        count=len(events),
        reason=reason,
        first_seen_at=min(event.timestamp for event in events),
        last_seen_at=max(event.timestamp for event in events),
        sample_event_ids=[event.id for event in events[:3]],
        sample_run_ids=[event.run_id for event in events[:3]],
    )


def _severity_for(events: list[MonitorEvent]) -> Literal["P0", "P1", "P2", "P3"]:
    if any(event.pii_leak or event.risk_level == RiskLevel.critical for event in events):
        return "P0"
    if any(
        event.risk_level == RiskLevel.high or not event.policy_compliant
        for event in events
    ):
        return "P1"
    if any(event.needs_human_review or not event.grounded for event in events):
        return "P2"
    return "P3"


def _severity_rank(severity: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(severity, 9)

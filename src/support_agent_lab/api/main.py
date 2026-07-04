from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import inspect
import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, get_args

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from support_agent_lab.api.auth import (
    RequestActor,
    get_request_actor,
    require_admin,
    require_same_user,
    require_scope,
)
from support_agent_lab.bootstrap import AppContainer, create_container
from support_agent_lab.api.readiness import ReadinessResponse, check_readiness
from support_agent_lab.api.request_signature import (
    RequestSignatureError,
    read_body_and_restore,
    request_signature_required,
    reserve_request_nonce,
    verify_request_signature,
)
from support_agent_lab.memory.event_store import StoredEvent
from support_agent_lab.memory.replay import MemoryReplayResult, replay_conversation_memory
from support_agent_lab.models import (
    AgentResponse,
    AgentRunSearchItem,
    AgentRunSearchResponse,
    AgentRunTrace,
    EvalCase,
    EvalGateCaseSummary,
    EvalGateRecord,
    EvalReport,
    EvalToolFault,
    Message,
    MonitorAlertStatus,
    MonitorAlertTriageEvent,
    MonitorEvent,
    RetrievalTrace,
    ToolFaultErrorCode,
    ToolStatus,
    new_id,
    utc_now,
)
from support_agent_lab.monitoring.monitor import (
    MonitorAlert,
    MonitorSummary,
    monitor_alert_key,
    summarize_monitor_events,
)
from support_agent_lab.tools.registry import ToolAuditRecord, ToolAuditSummary
from support_agent_lab.config import get_settings


class CreateSessionRequest(BaseModel):
    user_id: str | None = None


class CreateSessionResponse(BaseModel):
    conversation_id: str
    user_id: str


class ChatMessageRequest(BaseModel):
    conversation_id: str
    user_id: str | None = None
    content: str = Field(min_length=1, max_length=5000)


class ChatMessageResponse(BaseModel):
    message: Message
    trace_id: str
    handoff_required: bool
    citations: list[dict]


class TriageMonitorAlertRequest(BaseModel):
    status: MonitorAlertStatus | None = None
    assignee_user_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1000)


class IncidentRunBundle(BaseModel):
    run: AgentRunTrace
    run_source: str
    monitor_events: list[MonitorEvent]
    tool_audit_records: list[ToolAuditRecord]
    memory_replay: MemoryReplayResult | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=4, ge=1, le=20)
    snippet_chars: int = Field(default=500, ge=80, le=1200)


class KnowledgeSearchHit(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    score: float
    source_uri: str
    content_snippet: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    rewritten_queries: list[str]
    selected_sources: list[str]
    candidates_by_stage: dict[str, int]
    dropped_candidates: list[str]
    selected_context: list[KnowledgeSearchHit]


class MonitorDrilldownStats(BaseModel):
    total_events: int
    matching_events: int
    alerted_events: int
    high_risk_events: int
    ungrounded_events: int
    policy_violations: int
    human_review_events: int
    pii_leak_events: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class MonitorDrilldownBucket(BaseModel):
    key: str
    count: int
    rate: float
    latest_at: datetime | None = None
    sample_run_ids: list[str]


class MonitorDrilldownResponse(BaseModel):
    source: str
    summary: MonitorSummary
    active_alert: MonitorAlert | None
    stats: MonitorDrilldownStats
    events: list[MonitorEvent]
    failure_buckets: list[MonitorDrilldownBucket]
    intent_buckets: list[MonitorDrilldownBucket]
    risk_buckets: list[MonitorDrilldownBucket]


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
    by_status: dict[str, int]
    worst_active_severity: Literal["P0", "P1", "P2", "P3"] | None = None
    health_status: Literal["ok", "degraded", "critical"]
    mtta_seconds: int | None = None
    mttr_seconds: int | None = None
    oldest_active_alert_at: datetime | None = None
    latest_triage_at: datetime | None = None


class RegressionDraftRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    monitor_event_id: str | None = Field(default=None, max_length=128)
    failure_type: str | None = Field(default=None, max_length=100)
    source: str = Field(default="event_store", pattern="^(live|event_store)$")
    limit: int = Field(default=500, ge=1, le=1000)


class RunGoldenEvalRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=128)
    alert_key: str | None = Field(default=None, max_length=256)
    trigger: Literal["api", "console"] = "api"


class RegressionDraftSource(BaseModel):
    run_id: str
    run_source: str
    monitor_source: str
    monitor_event_ids: list[str] = Field(default_factory=list)
    conversation_id: str
    alert_key: str | None = None


class RegressionDraftResponse(BaseModel):
    target_file: str
    draft_type: str = "eval_case"
    draft: dict[str, Any]
    draft_json: str
    source: RegressionDraftSource
    redactions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


container = create_container()


def get_container() -> AppContainer:
    return container


def _knowledge_search_response(
    trace: RetrievalTrace,
    *,
    snippet_chars: int,
) -> KnowledgeSearchResponse:
    return KnowledgeSearchResponse(
        query=trace.query,
        rewritten_queries=trace.rewritten_queries,
        selected_sources=trace.selected_sources,
        candidates_by_stage=trace.candidates_by_stage,
        dropped_candidates=trace.dropped_candidates,
        selected_context=[
            KnowledgeSearchHit(
                document_id=hit.document_id,
                chunk_id=hit.chunk_id,
                title=hit.title,
                score=hit.score,
                source_uri=hit.source_uri,
                content_snippet=_snippet(hit.content, snippet_chars),
            )
            for hit in trace.selected_context
        ],
    )


def _snippet(value: str, max_chars: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 3]}..."


def _monitor_drilldown_response(
    *,
    source: str,
    events: list[MonitorEvent],
    triage_events: list[MonitorAlertTriageEvent],
    alert_key: str | None,
    intent: str | None,
    risk_level: str | None,
    failure_type: str | None,
    needs_human_review: bool | None,
    grounded: bool | None,
    policy_compliant: bool | None,
    include_healthy: bool,
    limit: int,
    order: str,
) -> MonitorDrilldownResponse:
    summary = summarize_monitor_events(events, triage_events=triage_events)
    active_alert = next((alert for alert in summary.alerts if alert.key == alert_key), None) if alert_key else None
    filtered = [
        event
        for event in events
        if _monitor_event_matches(
            event,
            alert_key=alert_key,
            intent=intent,
            risk_level=risk_level,
            failure_type=failure_type,
            needs_human_review=needs_human_review,
            grounded=grounded,
            policy_compliant=policy_compliant,
            include_healthy=include_healthy,
        )
    ]
    filtered.sort(key=lambda event: event.timestamp, reverse=order == "desc")
    returned_events = filtered[:limit]
    return MonitorDrilldownResponse(
        source=source,
        summary=summary,
        active_alert=active_alert,
        stats=_monitor_drilldown_stats(events, filtered),
        events=returned_events,
        failure_buckets=_monitor_buckets(filtered, lambda event: _monitor_failure_labels(event)),
        intent_buckets=_monitor_buckets(filtered, lambda event: [event.user_intent.value]),
        risk_buckets=_monitor_buckets(filtered, lambda event: [event.risk_level.value]),
    )


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


def _monitor_triage_metrics_response(
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
    alerted_events = [event for event in events if _monitor_event_alerted(event)]
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
        if _monitor_status(alert.status) in ACTIVE_MONITOR_ALERT_STATUSES
    ]
    by_status = {status.value: 0 for status in MonitorAlertStatus}
    for alert in summary.alerts:
        by_status[_monitor_status(alert.status).value] += 1
    by_severity = {severity: 0 for severity in MONITOR_SEVERITY_RANK}
    for alert in summary.alerts:
        by_severity[alert.severity] += 1

    now = generated_at
    stale_alerts = [
        alert
        for alert in active_alerts
        if now - alert.first_seen_at >= stale_after
    ]
    response_deltas: list[int] = []
    resolution_deltas: list[int] = []
    alerts_by_key = {alert.key: alert for alert in summary.alerts}
    for alert_key, events_for_key in triage_by_key.items():
        alert = alerts_by_key.get(alert_key)
        if not alert:
            continue
        first_triage = events_for_key[0]
        response_deltas.append(_duration_seconds(alert.first_seen_at, first_triage.created_at))
        resolution = next(
            (
                event
                for event in events_for_key
                if event.status in RESOLVED_MONITOR_ALERT_STATUSES
            ),
            None,
        )
        if resolution and alert.last_seen_at <= resolution.created_at:
            resolution_deltas.append(_duration_seconds(alert.first_seen_at, resolution.created_at))

    active_with_new_p0 = any(
        alert.severity == "P0" and (_monitor_status(alert.status) in ACTIVE_MONITOR_ALERT_STATUSES or alert.new_events_since_triage)
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
            Counter(label for event in alerted_events for label in _monitor_failure_labels(event))
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
        by_status=by_status,
        worst_active_severity=worst_active,
        health_status=health_status,
        mtta_seconds=_average_seconds(response_deltas),
        mttr_seconds=_average_seconds(resolution_deltas),
        oldest_active_alert_at=min((alert.first_seen_at for alert in active_alerts), default=None),
        latest_triage_at=max((event.created_at for event in relevant_triage_events), default=None),
    )


def _monitor_status(status: MonitorAlertStatus | str) -> MonitorAlertStatus:
    return status if isinstance(status, MonitorAlertStatus) else MonitorAlertStatus(status)


def _duration_seconds(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def _average_seconds(values: list[int]) -> int | None:
    if not values:
        return None
    return round(sum(values) / len(values))


def _monitor_event_matches(
    event: MonitorEvent,
    *,
    alert_key: str | None,
    intent: str | None,
    risk_level: str | None,
    failure_type: str | None,
    needs_human_review: bool | None,
    grounded: bool | None,
    policy_compliant: bool | None,
    include_healthy: bool,
) -> bool:
    if alert_key and (event.alert_key or monitor_alert_key(event)) != alert_key:
        return False
    if intent and event.user_intent.value != intent:
        return False
    if risk_level and event.risk_level.value != risk_level:
        return False
    if failure_type and failure_type not in _monitor_failure_labels(event):
        return False
    if needs_human_review is not None and event.needs_human_review != needs_human_review:
        return False
    if grounded is not None and event.grounded != grounded:
        return False
    if policy_compliant is not None and event.policy_compliant != policy_compliant:
        return False
    return include_healthy or _monitor_event_alerted(event)


def _monitor_event_alerted(event: MonitorEvent) -> bool:
    return bool(
        event.failure_types
        or not event.grounded
        or not event.policy_compliant
        or event.needs_human_review
        or event.pii_leak
    )


def _monitor_failure_labels(event: MonitorEvent) -> list[str]:
    if event.failure_types:
        return event.failure_types
    if _monitor_event_alerted(event):
        return ["QUALITY_REVIEW"]
    return ["none"]


def _monitor_drilldown_stats(
    all_events: list[MonitorEvent],
    matching_events: list[MonitorEvent],
) -> MonitorDrilldownStats:
    timestamps = [event.timestamp for event in matching_events]
    return MonitorDrilldownStats(
        total_events=len(all_events),
        matching_events=len(matching_events),
        alerted_events=sum(1 for event in matching_events if _monitor_event_alerted(event)),
        high_risk_events=sum(1 for event in matching_events if event.risk_level.value in {"high", "critical"}),
        ungrounded_events=sum(1 for event in matching_events if not event.grounded),
        policy_violations=sum(1 for event in matching_events if not event.policy_compliant),
        human_review_events=sum(1 for event in matching_events if event.needs_human_review),
        pii_leak_events=sum(1 for event in matching_events if event.pii_leak),
        first_seen_at=min(timestamps) if timestamps else None,
        last_seen_at=max(timestamps) if timestamps else None,
    )


def _monitor_buckets(
    events: list[MonitorEvent],
    labels_for: Callable[[MonitorEvent], list[str]],
) -> list[MonitorDrilldownBucket]:
    labels: list[tuple[str, MonitorEvent]] = []
    for event in events:
        for label in labels_for(event):
            labels.append((label, event))
    counts = Counter(label for label, _event in labels)
    total = len(events) or 1
    buckets: list[MonitorDrilldownBucket] = []
    for label, count in counts.most_common():
        label_events = [event for item_label, event in labels if item_label == label]
        label_events.sort(key=lambda event: event.timestamp, reverse=True)
        sample_run_ids = list(dict.fromkeys(event.run_id for event in label_events if event.run_id))[:3]
        buckets.append(
            MonitorDrilldownBucket(
                key=label,
                count=count,
                rate=round(count / total, 4),
                latest_at=label_events[0].timestamp if label_events else None,
                sample_run_ids=sample_run_ids,
            )
        )
    return buckets


SECURITY_FAILURE_LABELS = {
    "PROMPT_INJECTION_ATTEMPT",
    "PII_IN_INPUT",
    "PII_IN_OUTPUT",
    "FORBIDDEN",
    "UNAUTHORIZED",
}
INJECTABLE_TOOL_FAULT_CODES = {
    "RATE_LIMITED",
    "TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "UPSTREAM_ERROR",
    "INTERNAL_ERROR",
}
VALID_TOOL_FAULT_CODES = set(get_args(ToolFaultErrorCode))
REDACTION_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]", "email"),
    (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "[REDACTED_PHONE]", "phone"),
    (re.compile(r"\b\d{8,}\b"), "[REDACTED_NUMBER]", "long_number"),
]


def _regression_draft_response(
    *,
    run: AgentRunTrace,
    run_source: str,
    monitor_source: str,
    monitor_event: MonitorEvent | None,
    monitor_events: list[MonitorEvent],
    messages: list[Message],
    requested_failure_type: str | None,
) -> RegressionDraftResponse:
    warnings: list[str] = []
    redactions: list[str] = []
    turns, turn_redactions = _regression_turns(run, messages)
    redactions.extend(turn_redactions)
    if not messages:
        warnings.append("No message events were available; review the synthetic turn before committing.")
    failure_labels = _regression_failure_labels(run, monitor_event, requested_failure_type)
    target_file = _regression_target_file(run, failure_labels)
    expected = _regression_expected(run, monitor_event, target_file)
    if not expected:
        warnings.append("Draft has no strong expected assertions; add intent, route, tool, policy, or answer checks.")
    tool_faults = _regression_tool_faults(run)
    if tool_faults:
        warnings.append("Tool faults are injected before real handlers; review that the failure should be simulated offline.")
    if "PII_IN_OUTPUT" in failure_labels:
        warnings.append("PII_IN_OUTPUT is observed by the online monitor; add answer-level checks before committing.")

    scenario, scenario_redactions = _redact_eval_text(
        "Regression draft from monitor event "
        f"{monitor_event.id if monitor_event else 'none'} for run {run.id}. "
        f"{monitor_event.summary if monitor_event else 'No monitor event was selected.'}"
    )
    redactions.extend(scenario_redactions)
    draft: dict[str, Any] = {
        "case_id": _regression_case_id(run, failure_labels),
        "scenario": scenario,
        "locale": "zh-CN",
        "user_id": run.user_id,
        "turns": turns,
        "expected": expected,
        "tags": _regression_tags(run, monitor_event, failure_labels),
    }
    if tool_faults:
        draft["tool_faults"] = [fault.model_dump(mode="json", exclude_defaults=True) for fault in tool_faults]

    case = EvalCase.model_validate(draft)
    clean_draft = case.model_dump(mode="json", exclude_defaults=True)
    draft_json = json.dumps(clean_draft, ensure_ascii=False, indent=2)
    selected_monitor_event_ids = [monitor_event.id] if monitor_event else [event.id for event in monitor_events[:3]]
    return RegressionDraftResponse(
        target_file=target_file,
        draft=clean_draft,
        draft_json=draft_json,
        source=RegressionDraftSource(
            run_id=run.id,
            run_source=run_source,
            monitor_source=monitor_source,
            monitor_event_ids=selected_monitor_event_ids,
            conversation_id=run.conversation_id,
            alert_key=monitor_event.alert_key if monitor_event else None,
        ),
        redactions=sorted(set(redactions)),
        warnings=warnings,
    )


def _eval_gate_record(
    *,
    report: EvalReport,
    suite_id: str,
    suite_path: str,
    tenant_id: str,
    environment: str,
    actor_user_id: str | None,
    trigger: Literal["api", "console"],
    started_at: datetime,
    completed_at: datetime,
    run_id: str | None,
    alert_key: str | None,
) -> EvalGateRecord:
    case_results = [
        EvalGateCaseSummary(
            case_id=result.case_id,
            passed=result.passed,
            score=result.score,
            failures=result.failures,
            observed_intent=_enum_value(result.observed_intent),
            observed_route=_enum_value(result.observed_route) if result.observed_route else None,
            observed_error_codes=result.observed_error_codes,
            observed_policy_codes=result.observed_policy_codes,
        )
        for result in report.results
    ]
    failed_case_ids = [result.case_id for result in case_results if not result.passed]
    return EvalGateRecord(
        tenant_id=tenant_id,
        gate_name="golden",
        runner="agent",
        suite_id=suite_id,
        suite_path=suite_path,
        environment=environment,
        actor_user_id=actor_user_id,
        trigger=trigger,
        status="passed" if report.passed == report.total else "failed",
        total=report.total,
        passed=report.passed,
        score=report.score,
        failed_case_ids=failed_case_ids,
        case_results=case_results,
        run_id=run_id,
        alert_key=alert_key,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(started_at, completed_at),
        created_at=completed_at,
        metadata={"report_shape": "case_summary_only"},
    )


def _eval_gate_error_record(
    *,
    suite_id: str,
    suite_path: str,
    tenant_id: str,
    environment: str,
    actor_user_id: str | None,
    trigger: Literal["api", "console"],
    started_at: datetime,
    completed_at: datetime,
    run_id: str | None,
    alert_key: str | None,
    error: Exception,
) -> EvalGateRecord:
    return EvalGateRecord(
        tenant_id=tenant_id,
        gate_name="golden",
        runner="agent",
        suite_id=suite_id,
        suite_path=suite_path,
        environment=environment,
        actor_user_id=actor_user_id,
        trigger=trigger,
        status="error",
        error_message=_short_error_message(error),
        run_id=run_id,
        alert_key=alert_key,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(started_at, completed_at),
        created_at=completed_at,
    )


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _short_error_message(error: Exception) -> str:
    return str(error)[:500] or error.__class__.__name__


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _regression_turns(run: AgentRunTrace, messages: list[Message]) -> tuple[list[dict[str, str]], list[str]]:
    completed_at = run.completed_at
    user_messages = [
        message
        for message in messages
        if message.role.value == "user" and (completed_at is None or message.created_at <= completed_at)
    ]
    if not user_messages:
        return [
            {
                "role": "user",
                "content": f"Review support incident {run.id}.",
            }
        ], []
    turns: list[dict[str, str]] = []
    redactions: list[str] = []
    for message in user_messages[-4:]:
        content, turn_redactions = _redact_eval_text(message.content)
        redactions.extend(turn_redactions)
        turns.append({"role": "user", "content": content})
    return turns, redactions


def _regression_expected(
    run: AgentRunTrace,
    monitor_event: MonitorEvent | None,
    target_file: str,
) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    if run.intent:
        expected["intent"] = run.intent.primary.value
        if run.intent.entities:
            expected["required_entities"] = dict(run.intent.entities)
    if run.route:
        expected["route_target"] = run.route.target.value
        expected["route_needs_human"] = run.route.needs_human

    successful_tools = _unique(
        tool.name
        for tool in run.tool_results
        if tool.status == ToolStatus.success
    )
    failed_error_codes = _unique(
        tool.error_code
        for tool in run.tool_results
        if tool.status != ToolStatus.success and tool.error_code
    )
    if successful_tools:
        expected["required_tools"] = successful_tools
    if failed_error_codes:
        expected["required_error_codes"] = failed_error_codes

    policy_codes = _unique(finding.code for finding in run.policy_findings)
    if policy_codes:
        expected["required_policy_codes"] = policy_codes

    if monitor_event and monitor_event.needs_human_review:
        expected["escalation"] = True
    elif run.route:
        expected["escalation"] = run.route.needs_human

    if target_file == "examples/evals/golden_core.json" and run.retrieval:
        policy_refs = _unique(hit.document_id for hit in run.retrieval.selected_context)
        if policy_refs:
            expected["policy_refs"] = policy_refs
    return expected


def _regression_tool_faults(run: AgentRunTrace) -> list[EvalToolFault]:
    faults: list[EvalToolFault] = []
    for tool in run.tool_results:
        if tool.status == ToolStatus.success or not tool.error_code:
            continue
        if tool.error_code not in INJECTABLE_TOOL_FAULT_CODES or tool.error_code not in VALID_TOOL_FAULT_CODES:
            continue
        faults.append(
            EvalToolFault(
                tool_name=tool.name,
                error_code=tool.error_code,
                message=f"Injected from monitor regression draft for {tool.name}.",
                retryable=tool.retryable,
            )
        )
    return faults


def _regression_failure_labels(
    run: AgentRunTrace,
    monitor_event: MonitorEvent | None,
    requested_failure_type: str | None,
) -> list[str]:
    labels: list[str] = []
    if requested_failure_type:
        labels.append(requested_failure_type)
    if monitor_event:
        labels.extend(_monitor_failure_labels(monitor_event))
    labels.extend(tool.error_code for tool in run.tool_results if tool.error_code)
    labels.extend(finding.code for finding in run.policy_findings)
    return [label for label in _unique(labels) if label != "none"]


def _regression_target_file(run: AgentRunTrace, failure_labels: list[str]) -> str:
    label_set = set(failure_labels)
    if label_set & SECURITY_FAILURE_LABELS or run.policy_findings:
        return "examples/evals/security_regression.json"
    if any(tool.error_code for tool in run.tool_results):
        return "examples/evals/tool_failure_regression.json"
    if run.route and (run.route.needs_human or run.route.target.value == "human"):
        return "examples/evals/routing_regression.json"
    return "examples/evals/golden_core.json"


def _regression_case_id(run: AgentRunTrace, failure_labels: list[str]) -> str:
    intent = _safe_eval_token(run.intent.primary.value if run.intent else "unknown")
    failure = _safe_eval_token(failure_labels[0] if failure_labels else run.status)
    suffix = _safe_eval_token(run.id)[-10:] or "run"
    return f"draft_{intent}_{failure}_{suffix}"[:120].rstrip("_")


def _regression_tags(
    run: AgentRunTrace,
    monitor_event: MonitorEvent | None,
    failure_labels: list[str],
) -> list[str]:
    values = [
        "regression",
        "monitor",
        "draft",
        f"run_{_safe_eval_token(run.id)}",
    ]
    if monitor_event:
        values.append(f"event_{_safe_eval_token(monitor_event.id)}")
        if monitor_event.alert_key:
            values.append(f"alert_{_safe_eval_token(monitor_event.alert_key)}")
    values.extend(_safe_eval_token(label) for label in failure_labels)
    return [value for value in _unique(values) if value]


def _redact_eval_text(value: str) -> tuple[str, list[str]]:
    redactions: list[str] = []
    redacted = value
    for pattern, replacement, label in REDACTION_PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            redactions.append(label)
    return redacted, redactions


def _safe_eval_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return token[:64]


def _unique(values) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value is None or value in result:
            continue
        result.append(value)
    return result


def create_app() -> FastAPI:
    app = FastAPI(
        title="Production Support Agent Lab",
        version="0.1.0",
        description="A production-shaped customer support agent for learning agent engineering.",
    )

    @app.middleware("http")
    async def production_request_signature_middleware(request: Request, call_next):
        settings = get_settings()
        if request_signature_required(settings, request.url.path):
            body = await read_body_and_restore(request)
            try:
                verified = verify_request_signature(settings=settings, request=request, body=body)
                reserve_request_nonce(settings, verified)
            except RequestSignatureError as exc:
                return JSONResponse(status_code=401, content={"detail": str(exc)})
        return await call_next(request)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ready")
    async def ready(
        deps: Annotated[AppContainer, Depends(get_container)],
        deep: Annotated[bool | None, Query()] = None,
    ) -> ReadinessResponse:
        report = await check_readiness(deps, deep=deep)
        if report.status != "ok":
            return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
        return report

    @app.post("/api/v1/chat/sessions")
    def create_session(
        body: CreateSessionRequest,
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> CreateSessionResponse:
        require_same_user(body.user_id, actor)
        return CreateSessionResponse(conversation_id=new_id("conv"), user_id=actor.user_id)

    @app.post("/api/v1/chat/messages")
    async def chat(
        body: ChatMessageRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> ChatMessageResponse:
        require_same_user(body.user_id, actor)
        existing = deps.memory.states.get(body.conversation_id)
        if existing:
            require_same_user(existing.user_id, actor)
        try:
            response = await deps.orchestrator.handle_message(
                conversation_id=body.conversation_id,
                user_id=actor.user_id,
                text=body.content,
                actor_scopes=actor.scopes,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ChatMessageResponse(
            message=response.message,
            trace_id=response.trace.id,
            handoff_required=response.handoff_required,
            citations=[hit.model_dump(mode="json") for hit in response.citations],
        )

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    def list_messages(
        conversation_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> list[Message]:
        if conversation_id not in deps.memory.states:
            try:
                hydrate = deps.orchestrator.hydrate_memory_from_events(conversation_id, actor.user_id)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if hydrate["hydrate_status"] in {"no_event_store", "not_found"}:
                raise HTTPException(status_code=404, detail="Conversation not found")
        state = deps.memory.states[conversation_id]
        require_same_user(state.user_id, actor)
        return state.messages

    @app.get("/api/v1/agent/runs/{run_id}")
    def get_run(
        run_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ):
        run = deps.orchestrator.runs.get(run_id)
        if run is None and deps.event_store:
            run = deps.event_store.get_agent_run_trace(
                run_id,
                tenant_id=deps.settings.app_tenant_id,
            )
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.user_id != actor.user_id:
            require_admin(actor)
            require_scope(actor, "events:read")
        return run

    @app.get("/api/v1/admin/tools")
    def list_tools(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ):
        require_admin(actor)
        require_scope(actor, "admin:read")
        return deps.tools.registry.list_tools()

    @app.get("/api/v1/admin/tools/audit")
    def list_tool_audit_records(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        tool_name: Annotated[str | None, Query()] = None,
        actor_user_id: Annotated[str | None, Query()] = None,
        trace_id: Annotated[str | None, Query()] = None,
        request_id: Annotated[str | None, Query()] = None,
        status: Annotated[str | None, Query(pattern="^(success|failed|skipped)$")] = None,
        error_code: Annotated[str | None, Query()] = None,
        replayed: Annotated[bool | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    ) -> list[ToolAuditRecord]:
        require_admin(actor)
        require_scope(actor, "audit:read")
        if not deps.event_store:
            return []
        return deps.event_store.list_tool_audit_records(
            tenant_id=deps.settings.app_tenant_id,
            tool_name=tool_name,
            actor_user_id=actor_user_id,
            trace_id=trace_id,
            request_id=request_id,
            status=status,
            error_code=error_code,
            replayed=replayed,
            created_after=created_after.isoformat() if created_after else None,
            created_before=created_before.isoformat() if created_before else None,
            limit=limit,
            order=order,
        )

    @app.get("/api/v1/admin/tools/audit/summary")
    def summarize_tool_audit_records(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        tool_name: Annotated[str | None, Query()] = None,
        actor_user_id: Annotated[str | None, Query()] = None,
        trace_id: Annotated[str | None, Query()] = None,
        request_id: Annotated[str | None, Query()] = None,
        status: Annotated[str | None, Query(pattern="^(success|failed|skipped)$")] = None,
        error_code: Annotated[str | None, Query()] = None,
        replayed: Annotated[bool | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
    ) -> ToolAuditSummary:
        require_admin(actor)
        require_scope(actor, "audit:read")
        if not deps.event_store:
            return ToolAuditSummary(
                total_calls=0,
                failed_calls=0,
                replayed_calls=0,
                failure_rate=0.0,
                average_latency_ms=None,
                max_latency_ms=None,
                window_start=None,
                window_end=None,
                top_error_codes=[],
                tools=[],
            )
        return deps.event_store.summarize_tool_audit_records(
            tenant_id=deps.settings.app_tenant_id,
            tool_name=tool_name,
            actor_user_id=actor_user_id,
            trace_id=trace_id,
            request_id=request_id,
            status=status,
            error_code=error_code,
            replayed=replayed,
            created_after=created_after.isoformat() if created_after else None,
            created_before=created_before.isoformat() if created_before else None,
        )

    @app.post("/api/v1/admin/knowledge/search")
    async def search_knowledge(
        body: KnowledgeSearchRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> KnowledgeSearchResponse:
        require_admin(actor)
        require_scope(actor, "knowledge:diagnose")
        search_result = deps.knowledge.search(body.query, limit=body.limit)
        trace = await search_result if inspect.isawaitable(search_result) else search_result
        return _knowledge_search_response(trace, snippet_chars=body.snippet_chars)

    @app.get("/api/v1/admin/monitor/events")
    def monitor_events(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[str, Query(pattern="^(live|event_store)$")] = "live",
        conversation_id: Annotated[str | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    ) -> list[MonitorEvent]:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            return deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=conversation_id,
                created_after=created_after.isoformat() if created_after else None,
                created_before=created_before.isoformat() if created_before else None,
                limit=limit,
                order=order,
            )
        events = deps.monitor.events
        if conversation_id:
            events = [event for event in events if event.conversation_id == conversation_id]
        if created_after:
            events = [event for event in events if event.timestamp >= created_after]
        if created_before:
            events = [event for event in events if event.timestamp <= created_before]
        events = sorted(events, key=lambda event: event.timestamp, reverse=order == "desc")
        return events[:limit]

    @app.get("/api/v1/admin/monitor/drilldown")
    def monitor_drilldown(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[str, Query(pattern="^(live|event_store)$")] = "live",
        alert_key: Annotated[str | None, Query(max_length=256)] = None,
        intent: Annotated[str | None, Query(max_length=64)] = None,
        risk_level: Annotated[str | None, Query(pattern="^(low|medium|high|critical)$")] = None,
        failure_type: Annotated[str | None, Query(max_length=100)] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        needs_human_review: Annotated[bool | None, Query()] = None,
        grounded: Annotated[bool | None, Query()] = None,
        policy_compliant: Annotated[bool | None, Query()] = None,
        include_healthy: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    ) -> MonitorDrilldownResponse:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        triage_events: list[MonitorAlertTriageEvent] = []
        if source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            events = deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                created_after=created_after.isoformat() if created_after else None,
                created_before=created_before.isoformat() if created_before else None,
                limit=500,
                order=order,
            )
            triage_events = deps.event_store.list_monitor_alert_triage_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=500,
            )
        else:
            events = deps.monitor.events
            if created_after:
                events = [event for event in events if event.timestamp >= created_after]
            if created_before:
                events = [event for event in events if event.timestamp <= created_before]
            events = sorted(events, key=lambda event: event.timestamp, reverse=order == "desc")[:500]
        return _monitor_drilldown_response(
            source=source,
            events=events,
            triage_events=triage_events,
            alert_key=alert_key,
            intent=intent,
            risk_level=risk_level,
            failure_type=failure_type,
            needs_human_review=needs_human_review,
            grounded=grounded,
            policy_compliant=policy_compliant,
            include_healthy=include_healthy,
            limit=limit,
            order=order,
        )

    @app.get("/api/v1/admin/incidents/runs/{run_id}")
    def incident_run_bundle(
        run_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        include_memory: Annotated[bool, Query()] = True,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> IncidentRunBundle:
        require_admin(actor)
        require_scope(actor, "events:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        if include_memory:
            require_scope(actor, "memory:replay")

        run_source = "live"
        run = deps.orchestrator.runs.get(run_id)
        if run is None and deps.event_store:
            run = deps.event_store.get_agent_run_trace(
                run_id,
                tenant_id=deps.settings.app_tenant_id,
                limit=limit,
            )
            run_source = "event_store" if run else run_source
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        monitor_source = (
            deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                run_id=run_id,
                limit=limit,
            )
            if deps.event_store
            else deps.monitor.events[:limit]
        )
        monitor_events = [event for event in monitor_source if event.run_id == run_id]
        tool_audit_records = (
            deps.event_store.list_tool_audit_records(
                tenant_id=deps.settings.app_tenant_id,
                trace_id=run_id,
                limit=limit,
            )
            if deps.event_store
            else []
        )
        memory_replay = None
        if include_memory and deps.event_store:
            events = deps.event_store.list_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=run.conversation_id,
                limit=limit,
            )
            if events:
                try:
                    memory_replay = replay_conversation_memory(events)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

        return IncidentRunBundle(
            run=run,
            run_source=run_source,
            monitor_events=monitor_events,
            tool_audit_records=tool_audit_records,
            memory_replay=memory_replay,
        )

    @app.get("/api/v1/admin/runs")
    def search_agent_runs(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        user_id: Annotated[str | None, Query(max_length=128)] = None,
        conversation_id: Annotated[str | None, Query(max_length=128)] = None,
        intent: Annotated[str | None, Query(max_length=64)] = None,
        route: Annotated[str | None, Query(max_length=64)] = None,
        status: Annotated[str | None, Query(pattern="^(running|completed|failed)$")] = None,
        error_code: Annotated[str | None, Query(max_length=64)] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        from_: Annotated[datetime | None, Query(alias="from")] = None,
        to_: Annotated[datetime | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    ) -> AgentRunSearchResponse:
        require_admin(actor)
        require_scope(actor, "events:read")
        if not deps.event_store:
            return AgentRunSearchResponse(items=[], total=0, limit=limit, offset=offset, has_more=False)
        runs, total = deps.event_store.search_agent_run_traces(
            tenant_id=deps.settings.app_tenant_id,
            query=q,
            user_id=user_id,
            conversation_id=conversation_id,
            intent=intent,
            route=route,
            status=status,
            error_code=error_code,
            created_after=(created_after or from_).isoformat() if (created_after or from_) else None,
            created_before=(created_before or to_).isoformat() if (created_before or to_) else None,
            limit=limit,
            offset=offset,
            order=order,
        )
        return AgentRunSearchResponse(
            items=[_agent_run_search_item(run) for run in runs],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(runs) < total,
        )

    @app.get("/api/v1/admin/monitor/summary")
    def monitor_summary(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[str, Query(pattern="^(live|event_store)$")] = "live",
        conversation_id: Annotated[str | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 500,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    ) -> MonitorSummary:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            events = deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=conversation_id,
                created_after=created_after.isoformat() if created_after else None,
                created_before=created_before.isoformat() if created_before else None,
                limit=limit,
                order=order,
            )
            triage_events = deps.event_store.list_monitor_alert_triage_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=limit,
            )
            return summarize_monitor_events(events, triage_events=triage_events)
        events = deps.monitor.events
        if conversation_id:
            events = [event for event in events if event.conversation_id == conversation_id]
        if created_after:
            events = [event for event in events if event.timestamp >= created_after]
        if created_before:
            events = [event for event in events if event.timestamp <= created_before]
        events = sorted(events, key=lambda event: event.timestamp, reverse=order == "desc")
        return summarize_monitor_events(events[:limit])

    @app.get("/api/v1/admin/monitor/triage/metrics")
    def monitor_triage_metrics(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[str, Query(pattern="^(live|event_store)$")] = "live",
        conversation_id: Annotated[str | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 500,
        order: Annotated[Literal["asc", "desc"], Query()] = "desc",
        stale_after_minutes: Annotated[int, Query(ge=1, le=1440)] = 60,
    ) -> MonitorTriageMetricsResponse:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        triage_events: list[MonitorAlertTriageEvent] = []
        if source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            events = deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=conversation_id,
                created_after=created_after.isoformat() if created_after else None,
                created_before=created_before.isoformat() if created_before else None,
                limit=limit,
                order=order,
            )
            triage_events = deps.event_store.list_monitor_alert_triage_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=500,
            )
        else:
            events = deps.monitor.events
            if conversation_id:
                events = [event for event in events if event.conversation_id == conversation_id]
            if created_after:
                events = [event for event in events if event.timestamp >= created_after]
            if created_before:
                events = [event for event in events if event.timestamp <= created_before]
            events = sorted(events, key=lambda event: event.timestamp, reverse=order == "desc")[:limit]
        return _monitor_triage_metrics_response(
            source=source,
            events=events,
            triage_events=triage_events,
            conversation_id=conversation_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            order=order,
            stale_after=timedelta(minutes=stale_after_minutes),
        )

    @app.get("/api/v1/admin/monitor/alerts/{alert_key}/triage")
    def monitor_alert_triage_events(
        alert_key: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[MonitorAlertTriageEvent]:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        return deps.event_store.list_monitor_alert_triage_events(
            tenant_id=deps.settings.app_tenant_id,
            alert_key=alert_key,
            limit=limit,
        )

    @app.post("/api/v1/admin/monitor/alerts/{alert_key}/triage")
    def triage_monitor_alert(
        alert_key: str,
        body: TriageMonitorAlertRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> MonitorAlertTriageEvent:
        require_admin(actor)
        require_scope(actor, "monitor:write")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        note = body.note.strip()
        if body.status is None and body.assignee_user_id is None and not note:
            raise HTTPException(status_code=400, detail="At least one triage field is required")
        events = deps.event_store.list_monitor_events(
            tenant_id=deps.settings.app_tenant_id,
            limit=500,
        )
        summary = summarize_monitor_events(events)
        if alert_key not in {alert.key for alert in summary.alerts}:
            raise HTTPException(status_code=404, detail="Monitor alert not found")
        triage_event = MonitorAlertTriageEvent(
            alert_key=alert_key,
            status=body.status,
            assignee_user_id=body.assignee_user_id,
            actor_user_id=actor.user_id,
            note=note,
        )
        deps.event_store.append_monitor_alert_triage(
            triage_event,
            tenant_id=deps.settings.app_tenant_id,
        )
        return triage_event

    @app.post("/api/v1/admin/evals/regression-drafts")
    def create_regression_draft(
        body: RegressionDraftRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> RegressionDraftResponse:
        require_admin(actor)
        require_scope(actor, "events:read")
        require_scope(actor, "monitor:read")

        run_source = body.source
        monitor_source = body.source
        if body.source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            run = deps.event_store.get_agent_run_trace(
                body.run_id,
                tenant_id=deps.settings.app_tenant_id,
                limit=body.limit,
            )
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            monitor_events = deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                run_id=body.run_id,
                limit=body.limit,
                order="desc",
            )
            stored_events = deps.event_store.list_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=run.conversation_id,
                limit=body.limit,
                order="asc",
            )
            messages = [
                Message.model_validate(event.payload)
                for event in stored_events
                if event.event_type in {"message.user", "message.assistant"}
            ]
        else:
            run = deps.orchestrator.runs.get(body.run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            monitor_events = [event for event in deps.monitor.events if event.run_id == body.run_id]
            state = deps.memory.states.get(run.conversation_id)
            messages = list(state.messages) if state else []

        monitor_event = None
        if body.monitor_event_id:
            monitor_event = next((event for event in monitor_events if event.id == body.monitor_event_id), None)
            if monitor_event is None:
                raise HTTPException(status_code=404, detail="Monitor event not found for run")
        elif monitor_events:
            monitor_event = monitor_events[0]

        return _regression_draft_response(
            run=run,
            run_source=run_source,
            monitor_source=monitor_source,
            monitor_event=monitor_event,
            monitor_events=monitor_events,
            messages=messages,
            requested_failure_type=body.failure_type,
        )

    @app.post("/api/v1/admin/evals/golden")
    async def run_golden_eval(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        body: RunGoldenEvalRequest | None = None,
    ) -> EvalReport:
        require_admin(actor)
        require_scope(actor, "eval:run")
        if deps.settings.is_production:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The bundled golden eval uses lab fixtures and is disabled in production. "
                    "Run offline evals in CI or a staging sandbox instead."
                ),
            )
        if not deps.event_store:
            raise HTTPException(status_code=503, detail="Event store is required for eval gate audit records")
        from support_agent_lab.evals.runner import load_cases, run_cases

        suite_path = "examples/evals/golden_core.json"
        request_body = body or RunGoldenEvalRequest()
        started_at = utc_now()
        try:
            cases = load_cases(suite_path)
            report = await run_cases(cases, deps.orchestrator)
        except Exception as exc:
            completed_at = utc_now()
            deps.event_store.append_eval_gate_record(
                _eval_gate_error_record(
                    suite_id="golden_core",
                    suite_path=suite_path,
                    tenant_id=deps.settings.app_tenant_id,
                    environment=deps.settings.app_env,
                    actor_user_id=actor.user_id,
                    trigger=request_body.trigger,
                    started_at=started_at,
                    completed_at=completed_at,
                    run_id=request_body.run_id,
                    alert_key=request_body.alert_key,
                    error=exc,
                ),
                tenant_id=deps.settings.app_tenant_id,
            )
            raise HTTPException(status_code=500, detail="Eval gate runner failed; audit record was persisted") from exc
        completed_at = utc_now()
        record = _eval_gate_record(
            report=report,
            suite_id="golden_core",
            suite_path=suite_path,
            tenant_id=deps.settings.app_tenant_id,
            environment=deps.settings.app_env,
            actor_user_id=actor.user_id,
            trigger=request_body.trigger,
            started_at=started_at,
            completed_at=completed_at,
            run_id=request_body.run_id,
            alert_key=request_body.alert_key,
        )
        deps.event_store.append_eval_gate_record(
            record,
            tenant_id=deps.settings.app_tenant_id,
        )
        return report

    @app.get("/api/v1/admin/evals/gates")
    def list_eval_gate_records(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        run_id: Annotated[str | None, Query(max_length=128)] = None,
        alert_key: Annotated[str | None, Query(max_length=256)] = None,
        gate_name: Annotated[str | None, Query(max_length=80)] = None,
        runner: Annotated[Literal["agent", "monitor", "retrieval"] | None, Query()] = None,
        status: Annotated[Literal["passed", "failed", "error"] | None, Query()] = None,
        actor_user_id: Annotated[str | None, Query(max_length=128)] = None,
        created_after: Annotated[str | None, Query(max_length=64)] = None,
        created_before: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    ) -> list[EvalGateRecord]:
        require_admin(actor)
        require_scope(actor, "eval:read")
        if not deps.event_store:
            return []
        return deps.event_store.list_eval_gate_records(
            tenant_id=deps.settings.app_tenant_id,
            run_id=run_id,
            alert_key=alert_key,
            gate_name=gate_name,
            runner=runner,
            status=status,
            actor_user_id=actor_user_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            order=order,
        )

    @app.get("/api/v1/admin/events")
    def list_events(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        conversation_id: Annotated[str | None, Query()] = None,
        event_type: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[StoredEvent]:
        require_admin(actor)
        require_scope(actor, "events:read")
        if not deps.event_store:
            return []
        return deps.event_store.list_events(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            event_type=event_type,
            limit=limit,
        )

    @app.get("/api/v1/admin/conversations/{conversation_id}/memory/replay")
    def replay_memory(
        conversation_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> MemoryReplayResult:
        require_admin(actor)
        require_scope(actor, "memory:replay")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        events = deps.event_store.list_events(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        if not events:
            raise HTTPException(status_code=404, detail="Conversation events not found")
        try:
            return replay_conversation_memory(events)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def _agent_run_search_item(run: AgentRunTrace) -> AgentRunSearchItem:
    duration_ms = None
    if run.completed_at:
        duration_ms = max(0, int((run.completed_at - run.created_at).total_seconds() * 1000))
    error_codes = [
        code
        for code in dict.fromkeys(tool.error_code for tool in run.tool_results if tool.error_code)
    ]
    policy_codes = [code for code in dict.fromkeys(finding.code for finding in run.policy_findings)]
    needs_human = bool(
        (run.route.needs_human if run.route else False)
        or any(finding.should_escalate for finding in run.policy_findings)
    )
    return AgentRunSearchItem(
        id=run.id,
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        agent_version=run.agent_version,
        intent=run.intent.primary if run.intent else None,
        route=run.route.target if run.route else None,
        status=run.status,
        created_at=run.created_at,
        completed_at=run.completed_at,
        duration_ms=duration_ms,
        tool_count=len(run.tool_results),
        failed_tool_count=sum(1 for tool in run.tool_results if tool.status == "failed"),
        tool_error_codes=error_codes,
        policy_codes=policy_codes,
        citation_count=len(run.retrieval.selected_context) if run.retrieval else 0,
        llm_call_count=len(run.llm_calls),
        needs_human=needs_human,
    )


app = create_app()

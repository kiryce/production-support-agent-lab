from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import hashlib
import inspect
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal, get_args

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from support_agent_lab.api.auth import (
    RequestActor,
    get_request_actor,
    require_admin,
    require_same_user,
    require_scope,
)
from support_agent_lab.bootstrap import AppContainer, create_container, create_eval_container
from support_agent_lab.api.readiness import ReadinessResponse, check_readiness
from support_agent_lab.api.request_signature import (
    RequestSignatureError,
    read_body_and_restore,
    request_signature_required,
    reserve_request_nonce,
    verify_request_signature,
)
from support_agent_lab.api.rate_limit import InMemoryRateLimiter, rate_limit_key, should_rate_limit
from support_agent_lab.api.metrics import InMemoryHTTPMetrics, PROMETHEUS_CONTENT_TYPE, render_prometheus_metrics
from support_agent_lab.evals.suites import STAGING_EVAL_SUITES
from support_agent_lab.memory.event_store import (
    EventStoreRetentionReport,
    FeedbackSummary,
    SQLiteBackupReport,
    StoredEvent,
)
from support_agent_lab.memory.knowledge_call import call_knowledge_search
from support_agent_lab.memory.replay import MemoryReplayResult, replay_conversation_memory
from support_agent_lab.models import (
    AgentFeedback,
    AgentResponse,
    AgentRunSearchItem,
    AgentRunSearchResponse,
    AgentRunTrace,
    AlertDeliveryRecord,
    AlertDeliveryStatus,
    EvalCase,
    EvalGateCaseSummary,
    EvalGateRecord,
    EvalReport,
    EvalToolFault,
    FeedbackRating,
    Message,
    MonitorAlertStatus,
    MonitorAlertTriageEvent,
    MonitorEvent,
    RetrievalContext,
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
from support_agent_lab.monitoring.triage import (
    MonitorTriageMetricsResponse,
    monitor_event_alerted,
    monitor_failure_labels,
    monitor_triage_metrics_response,
)
from support_agent_lab.monitoring.alert_dispatcher import (
    AlertDeliverySummary,
    AlertDispatchReport,
    dispatch_alert_deliveries,
    enqueue_alert_deliveries,
    summarize_alert_deliveries,
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


class AgentFeedbackRequest(BaseModel):
    rating: FeedbackRating
    reasons: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=1000)
    source: Literal["user", "operator", "qa"] = "user"


class TriageMonitorAlertRequest(BaseModel):
    status: MonitorAlertStatus | None = None
    assignee_user_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1000)


class AlertDeliveryOperatorActionRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class EventStoreRetentionRequest(BaseModel):
    dry_run: bool = True
    include_events: bool = False
    vacuum: bool = False
    event_retention_days: int | None = Field(default=None, ge=30, le=3650)
    tool_audit_retention_days: int | None = Field(default=None, ge=30, le=3650)
    idempotency_retention_days: int | None = Field(default=None, ge=1, le=3650)
    alert_delivery_retention_days: int | None = Field(default=None, ge=7, le=3650)


class EventStoreBackupRequest(BaseModel):
    label: str = Field(default="", max_length=80)
    overwrite: bool = False
    verify: bool = True


class IncidentRunBundle(BaseModel):
    run: AgentRunTrace
    run_source: str
    monitor_events: list[MonitorEvent]
    tool_audit_records: list[ToolAuditRecord]
    memory_replay: MemoryReplayResult | None = None


class IncidentBriefResponse(BaseModel):
    schema_version: str = "incident_brief.v1"
    generated_at: datetime
    title: str
    risk_label: str
    summary: str
    run_id: str
    conversation_id: str
    run_source: str
    alert_key: str | None = None
    recommended_actions: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)
    redactions: list[str] = Field(default_factory=list)
    markdown: str


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


class PromotionGateThresholds(BaseModel):
    max_active_p0p1_alerts: int
    max_active_alerts: int
    max_tool_failure_rate: float
    max_feedback_negative_rate: float
    max_eval_age_hours: int
    min_tool_calls: int
    min_feedback_count: int


class PromotionGateCheck(BaseModel):
    name: str
    status: Literal["passed", "warn", "blocked"]
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class PromotionGateResponse(BaseModel):
    status: Literal["passed", "warn", "blocked"]
    generated_at: datetime
    environment: str
    source: Literal["event_store", "live"]
    window_hours: int
    thresholds: PromotionGateThresholds
    checks: list[PromotionGateCheck]
    readiness: ReadinessResponse
    monitor: MonitorTriageMetricsResponse
    tool_audit: ToolAuditSummary
    feedback: FeedbackSummary
    latest_eval_gate: EvalGateRecord | None = None


class OperationsAutomationCommand(BaseModel):
    method: Literal["GET", "POST"]
    path: str
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)


class OperationsAutomationAction(BaseModel):
    id: str
    kind: Literal[
        "dispatch_alert_deliveries",
        "configure_alert_webhook",
        "assign_triage_owner",
        "retriage_recurring_alert",
        "investigate_stale_alert",
        "requeue_dead_delivery",
        "generate_incident_brief",
        "create_regression_draft",
        "block_promotion",
        "review_promotion_gate",
        "run_staging_eval",
        "inspect_tool_audit",
        "review_feedback",
        "run_retrieval_diagnostics",
        "no_action_required",
    ]
    priority: Literal["P0", "P1", "P2", "P3"]
    title: str
    detail: str
    safe_to_auto_execute: bool = False
    required_scopes: list[str] = Field(default_factory=list)
    command: OperationsAutomationCommand | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class OperationsAutomationPlan(BaseModel):
    schema_version: str = "ops_automation.v1"
    generated_at: datetime
    environment: str
    source: Literal["event_store", "live"]
    window_hours: int
    health_status: Literal["ok", "degraded", "critical", "unknown"]
    action_count: int
    auto_executable_count: int
    actions: list[OperationsAutomationAction]
    evidence: dict[str, Any] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)


class SloObjectiveResult(BaseModel):
    name: str
    status: Literal["met", "at_risk", "breached", "no_data"]
    target_type: Literal["minimum", "maximum", "freshness", "state"]
    target: dict[str, Any] = Field(default_factory=dict)
    observed: dict[str, Any] = Field(default_factory=dict)
    error_budget_remaining: float | None = None
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class SloReportResponse(BaseModel):
    schema_version: str = "slo_report.v1"
    generated_at: datetime
    environment: str
    source: Literal["event_store", "live"]
    window_hours: int
    status: Literal["healthy", "watch", "breached", "unknown"]
    objective_count: int
    met_count: int
    at_risk_count: int
    breached_count: int
    no_data_count: int
    objectives: list[SloObjectiveResult]
    evidence: dict[str, Any] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)


PROMOTION_DECISION_EVENT_TYPE = "release.promotion.decision"
AUDIT_EXPORT_MEDIA_TYPE = "application/x-ndjson"


class PromotionDecisionRequest(BaseModel):
    target_version: str = Field(min_length=1, max_length=128)
    decision: Literal["approved", "rejected", "deferred"]
    note: str = Field(min_length=1, max_length=1000)
    override_blocked: bool = False
    override_reason: str = Field(default="", max_length=500)
    source: Literal["event_store", "live"] = "event_store"
    deep: bool = False
    window_hours: int = Field(default=24, ge=1, le=168)
    max_active_p0p1_alerts: int = Field(default=0, ge=0, le=100)
    max_active_alerts: int = Field(default=10, ge=0, le=1000)
    max_tool_failure_rate: float = Field(default=0.05, ge=0, le=1)
    max_feedback_negative_rate: float = Field(default=0.4, ge=0, le=1)
    max_eval_age_hours: int = Field(default=24, ge=1, le=720)
    min_tool_calls: int = Field(default=1, ge=0, le=10000)
    min_feedback_count: int = Field(default=5, ge=0, le=10000)


class PromotionDecisionRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("release"))
    tenant_id: str
    environment: str
    target_version: str
    decision: Literal["approved", "rejected", "deferred"]
    gate_status: Literal["passed", "warn", "blocked"]
    gate: PromotionGateResponse
    note: str
    override_blocked: bool = False
    override_reason: str = ""
    actor_user_id: str
    created_at: datetime = Field(default_factory=utc_now)


class RegressionDraftRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    monitor_event_id: str | None = Field(default=None, max_length=128)
    feedback_id: str | None = Field(default=None, max_length=128)
    failure_type: str | None = Field(default=None, max_length=100)
    source: str = Field(default="event_store", pattern="^(live|event_store)$")
    limit: int = Field(default=500, ge=1, le=1000)


class RunGoldenEvalRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=128)
    alert_key: str | None = Field(default=None, max_length=256)
    trigger: Literal["api", "console"] = "api"


class EvalGateRunResponse(BaseModel):
    gate_name: str
    gate_run_id: str
    status: Literal["passed", "failed", "error"]
    total: int
    passed: int
    score: float
    failed_gate_ids: list[str] = Field(default_factory=list)
    records: list[EvalGateRecord]
    run_id: str | None = None
    alert_key: str | None = None
    started_at: datetime
    completed_at: datetime
    duration_ms: int


class RegressionDraftSource(BaseModel):
    run_id: str
    run_source: str
    monitor_source: str
    monitor_event_ids: list[str] = Field(default_factory=list)
    feedback_id: str | None = None
    feedback_rating: FeedbackRating | None = None
    feedback_reasons: list[str] = Field(default_factory=list)
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
        failure_buckets=_monitor_buckets(filtered, lambda event: monitor_failure_labels(event)),
        intent_buckets=_monitor_buckets(filtered, lambda event: [event.user_intent.value]),
        risk_buckets=_monitor_buckets(filtered, lambda event: [event.risk_level.value]),
    )


async def _promotion_gate_response(
    *,
    deps: AppContainer,
    source: Literal["event_store", "live"],
    deep: bool,
    window_hours: int,
    max_active_p0p1_alerts: int,
    max_active_alerts: int,
    max_tool_failure_rate: float,
    max_feedback_negative_rate: float,
    max_eval_age_hours: int,
    min_tool_calls: int,
    min_feedback_count: int,
) -> PromotionGateResponse:
    generated_at = utc_now()
    created_after = generated_at - timedelta(hours=window_hours)
    readiness = await check_readiness(deps, deep=deep)
    monitor = _load_monitor_triage_metrics(
        deps=deps,
        source=source,
        created_after=created_after,
        limit=500,
    )
    tool_audit = _load_tool_audit_summary(
        deps=deps,
        created_after=created_after,
    )
    feedback = _load_feedback_summary(deps=deps, created_after=created_after)
    latest_eval_gate = _latest_promotion_eval_gate(deps)
    thresholds = PromotionGateThresholds(
        max_active_p0p1_alerts=max_active_p0p1_alerts,
        max_active_alerts=max_active_alerts,
        max_tool_failure_rate=max_tool_failure_rate,
        max_feedback_negative_rate=max_feedback_negative_rate,
        max_eval_age_hours=max_eval_age_hours,
        min_tool_calls=min_tool_calls,
        min_feedback_count=min_feedback_count,
    )
    checks = [
        _promotion_readiness_check(readiness),
        _promotion_alert_check(monitor, max_active_p0p1_alerts, max_active_alerts),
        _promotion_tool_audit_check(tool_audit, max_tool_failure_rate, min_tool_calls),
        _promotion_feedback_check(feedback, max_feedback_negative_rate, min_feedback_count),
        _promotion_eval_gate_check(latest_eval_gate, generated_at, max_eval_age_hours),
    ]
    return PromotionGateResponse(
        status=_promotion_status(checks),
        generated_at=generated_at,
        environment=deps.settings.app_env,
        source=monitor.source,
        window_hours=window_hours,
        thresholds=thresholds,
        checks=checks,
        readiness=readiness,
        monitor=monitor,
        tool_audit=tool_audit,
        feedback=feedback,
        latest_eval_gate=latest_eval_gate,
    )


OPS_AUTOMATION_GUARDRAILS = [
    "This endpoint is read-only; callers must execute returned commands explicitly.",
    "Actions marked safe_to_auto_execute avoid destructive state changes, but still require scoped credentials.",
    "Triage owner assignment, delivery requeue, release approval, and production eval execution require human review.",
    "Generated commands never include message content, tool arguments, tool payloads, memory facts, or feedback comments.",
]
OPS_ACTIVE_ALERT_STATUSES = {"open", "acknowledged", "investigating"}
OPS_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


SLO_REPORT_GUARDRAILS = [
    "This endpoint is read-only and returns aggregate service objectives only.",
    "It does not include message content, tool arguments, tool payloads, retrieval bodies, memory facts, or feedback comments.",
    "No-data objectives are treated as watch-level risk unless every objective lacks data.",
    "Use this report for operational review; release approval still requires the promotion gate and append-only decision audit.",
]


async def _slo_report_response(
    *,
    deps: AppContainer,
    source: Literal["event_store", "live"],
    deep: bool,
    window_hours: int,
    min_grounded_rate: float,
    min_policy_compliance_rate: float,
    max_human_review_rate: float,
    max_active_p0p1_alerts: int,
    max_tool_failure_rate: float,
    max_feedback_negative_rate: float,
    max_eval_age_hours: int,
    max_mtta_seconds: int,
    max_alert_delivery_dead_count: int,
    min_tool_calls: int,
    min_feedback_count: int,
) -> SloReportResponse:
    promotion = await _promotion_gate_response(
        deps=deps,
        source=source,
        deep=deep,
        window_hours=window_hours,
        max_active_p0p1_alerts=max_active_p0p1_alerts,
        max_active_alerts=10,
        max_tool_failure_rate=max_tool_failure_rate,
        max_feedback_negative_rate=max_feedback_negative_rate,
        max_eval_age_hours=max_eval_age_hours,
        min_tool_calls=min_tool_calls,
        min_feedback_count=min_feedback_count,
    )
    delivery_summary = _safe_monitor_alert_delivery_summary(deps, limit=200)
    dead_delivery_records = _list_ops_delivery_records(
        deps,
        status=AlertDeliveryStatus.dead,
        limit=max_alert_delivery_dead_count + 1,
    )
    active_p0p1_alerts = (promotion.monitor.active_by_severity.get("P0", 0) or 0) + (
        promotion.monitor.active_by_severity.get("P1", 0) or 0
    )
    eval_age_hours = _datetime_age_hours(
        promotion.latest_eval_gate.completed_at if promotion.latest_eval_gate else None,
        promotion.generated_at,
    )
    objectives = [
        _slo_minimum_rate(
            name="grounded_rate",
            observed_rate=promotion.monitor.grounded_rate,
            target_rate=min_grounded_rate,
            sample_count=promotion.monitor.total_events,
            detail_label="Grounded answer rate",
            evidence={
                "total_events": promotion.monitor.total_events,
                "ungrounded_events": promotion.monitor.ungrounded_events,
            },
        ),
        _slo_minimum_rate(
            name="policy_compliance_rate",
            observed_rate=promotion.monitor.policy_compliance_rate,
            target_rate=min_policy_compliance_rate,
            sample_count=promotion.monitor.total_events,
            detail_label="Policy-compliant monitor event rate",
            evidence={
                "total_events": promotion.monitor.total_events,
                "policy_violations": promotion.monitor.policy_violations,
                "pii_leak_events": promotion.monitor.pii_leak_events,
            },
        ),
        _slo_maximum_rate(
            name="human_review_rate",
            observed_rate=promotion.monitor.human_review_rate,
            target_rate=max_human_review_rate,
            sample_count=promotion.monitor.total_events,
            min_sample_count=1,
            detail_label="Human-review pressure",
            evidence={
                "total_events": promotion.monitor.total_events,
                "human_review_events": promotion.monitor.human_review_events,
            },
        ),
        _slo_maximum_count(
            name="active_p0p1_alerts",
            observed_count=active_p0p1_alerts,
            target_count=max_active_p0p1_alerts,
            detail_label="Active P0/P1 monitor alerts",
            evidence={
                "active_alert_count": promotion.monitor.active_alert_count,
                "active_by_severity": promotion.monitor.active_by_severity,
                "new_events_since_triage_count": promotion.monitor.new_events_since_triage_count,
            },
        ),
        _slo_maximum_rate(
            name="tool_failure_rate",
            observed_rate=promotion.tool_audit.failure_rate,
            target_rate=max_tool_failure_rate,
            sample_count=promotion.tool_audit.total_calls,
            min_sample_count=min_tool_calls,
            detail_label="Audited tool failure rate",
            evidence={
                "total_calls": promotion.tool_audit.total_calls,
                "failed_calls": promotion.tool_audit.failed_calls,
                "top_error_codes": [item.error_code for item in promotion.tool_audit.top_error_codes[:5]],
            },
        ),
        _slo_maximum_rate(
            name="feedback_negative_rate",
            observed_rate=promotion.feedback.negative_rate,
            target_rate=max_feedback_negative_rate,
            sample_count=promotion.feedback.total_count,
            min_sample_count=min_feedback_count,
            detail_label="Negative response-feedback rate",
            evidence={
                "total_count": promotion.feedback.total_count,
                "negative_count": promotion.feedback.negative_count,
                "top_reasons": [
                    reason.model_dump(mode="json") for reason in promotion.feedback.counts_by_reason[:5]
                ],
            },
        ),
        _slo_fresh_eval_gate(
            record=promotion.latest_eval_gate,
            age_hours=eval_age_hours,
            max_eval_age_hours=max_eval_age_hours,
        ),
        _slo_triage_response(
            monitor=promotion.monitor,
            max_mtta_seconds=max_mtta_seconds,
        ),
        _slo_alert_delivery(
            summary=delivery_summary,
            active_p0p1_alerts=active_p0p1_alerts,
            max_dead_count=max_alert_delivery_dead_count,
            observed_dead_count=len(dead_delivery_records),
        ),
    ]
    counts = Counter(objective.status for objective in objectives)
    return SloReportResponse(
        generated_at=promotion.generated_at,
        environment=deps.settings.app_env,
        source=promotion.source,
        window_hours=window_hours,
        status=_slo_report_status(objectives),
        objective_count=len(objectives),
        met_count=counts.get("met", 0),
        at_risk_count=counts.get("at_risk", 0),
        breached_count=counts.get("breached", 0),
        no_data_count=counts.get("no_data", 0),
        objectives=objectives,
        evidence={
            "readiness_status": promotion.readiness.status,
            "monitor_health_status": promotion.monitor.health_status,
            "promotion_gate_status": promotion.status,
            "alert_delivery_status": delivery_summary.status if delivery_summary else None,
            "latest_eval_gate_id": promotion.latest_eval_gate.id if promotion.latest_eval_gate else None,
        },
        guardrails=SLO_REPORT_GUARDRAILS,
    )


def _slo_minimum_rate(
    *,
    name: str,
    observed_rate: float,
    target_rate: float,
    sample_count: int,
    detail_label: str,
    evidence: dict[str, Any],
) -> SloObjectiveResult:
    target = {"min_rate": target_rate}
    observed = {"rate": observed_rate, "sample_count": sample_count}
    if sample_count <= 0:
        return SloObjectiveResult(
            name=name,
            status="no_data",
            target_type="minimum",
            target=target,
            observed=observed,
            detail=f"{detail_label} has no monitor samples in the window.",
            evidence=evidence,
        )
    budget = max(1.0 - target_rate, 0.000001)
    consumed = max(0.0, 1.0 - observed_rate)
    remaining = _bounded_budget((budget - consumed) / budget)
    status = _slo_rate_status(observed_rate >= target_rate, remaining)
    return SloObjectiveResult(
        name=name,
        status=status,
        target_type="minimum",
        target=target,
        observed=observed,
        error_budget_remaining=remaining,
        detail=f"{detail_label} is {observed_rate:.1%}; target is at least {target_rate:.1%}.",
        evidence=evidence,
    )


def _slo_maximum_rate(
    *,
    name: str,
    observed_rate: float,
    target_rate: float,
    sample_count: int,
    min_sample_count: int,
    detail_label: str,
    evidence: dict[str, Any],
) -> SloObjectiveResult:
    target = {"max_rate": target_rate, "min_sample_count": min_sample_count}
    observed = {"rate": observed_rate, "sample_count": sample_count}
    if sample_count < min_sample_count:
        return SloObjectiveResult(
            name=name,
            status="no_data",
            target_type="maximum",
            target=target,
            observed=observed,
            detail=(
                f"{detail_label} has {sample_count} sample(s), below the minimum "
                f"{min_sample_count} needed for an SLO decision."
            ),
            evidence=evidence,
        )
    if target_rate <= 0:
        remaining = 1.0 if observed_rate <= 0 else 0.0
    else:
        remaining = _bounded_budget((target_rate - observed_rate) / target_rate)
    status = _slo_rate_status(observed_rate <= target_rate, remaining)
    return SloObjectiveResult(
        name=name,
        status=status,
        target_type="maximum",
        target=target,
        observed=observed,
        error_budget_remaining=remaining,
        detail=f"{detail_label} is {observed_rate:.1%}; target is at most {target_rate:.1%}.",
        evidence=evidence,
    )


def _slo_maximum_count(
    *,
    name: str,
    observed_count: int,
    target_count: int,
    detail_label: str,
    evidence: dict[str, Any],
) -> SloObjectiveResult:
    target = {"max_count": target_count}
    observed = {"count": observed_count}
    if target_count <= 0:
        remaining = 1.0 if observed_count <= 0 else 0.0
    else:
        remaining = _bounded_budget((target_count - observed_count) / target_count)
    status = _slo_rate_status(observed_count <= target_count, remaining)
    return SloObjectiveResult(
        name=name,
        status=status,
        target_type="maximum",
        target=target,
        observed=observed,
        error_budget_remaining=remaining,
        detail=f"{detail_label}: {observed_count}; target is at most {target_count}.",
        evidence=evidence,
    )


def _slo_fresh_eval_gate(
    *,
    record: EvalGateRecord | None,
    age_hours: float | None,
    max_eval_age_hours: int,
) -> SloObjectiveResult:
    target = {"status": "passed", "max_age_hours": max_eval_age_hours}
    observed = {
        "gate_id": record.id if record else None,
        "status": record.status if record else None,
        "age_hours": age_hours,
    }
    if record is None:
        return SloObjectiveResult(
            name="staging_eval_gate_freshness",
            status="no_data",
            target_type="freshness",
            target=target,
            observed=observed,
            detail="No aggregate staging eval gate record is available.",
        )
    status: Literal["met", "at_risk", "breached", "no_data"] = "met"
    if record.status != "passed":
        status = "breached"
    elif age_hours is not None and age_hours > max_eval_age_hours:
        status = "at_risk"
    return SloObjectiveResult(
        name="staging_eval_gate_freshness",
        status=status,
        target_type="freshness",
        target=target,
        observed=observed,
        error_budget_remaining=0.0 if status == "breached" else 1.0 if status == "met" else 0.25,
        detail=(
            f"Latest aggregate staging eval gate is {record.status}; "
            f"age is {age_hours if age_hours is not None else 'unknown'} hour(s)."
        ),
        evidence={"suite_id": record.suite_id, "passed": record.passed, "total": record.total},
    )


def _slo_triage_response(
    *,
    monitor: MonitorTriageMetricsResponse,
    max_mtta_seconds: int,
) -> SloObjectiveResult:
    target = {"max_mtta_seconds": max_mtta_seconds}
    observed = {
        "mtta_seconds": monitor.mtta_seconds,
        "unassigned_active_alert_count": monitor.unassigned_active_alert_count,
        "active_alert_count": monitor.active_alert_count,
    }
    if monitor.active_alert_count == 0:
        return SloObjectiveResult(
            name="triage_response_time",
            status="met",
            target_type="maximum",
            target=target,
            observed=observed,
            error_budget_remaining=1.0,
            detail="No active alerts need triage response in the window.",
            evidence=observed,
        )
    if monitor.mtta_seconds is None:
        return SloObjectiveResult(
            name="triage_response_time",
            status="breached" if monitor.unassigned_active_alert_count else "no_data",
            target_type="maximum",
            target=target,
            observed=observed,
            error_budget_remaining=0.0 if monitor.unassigned_active_alert_count else None,
            detail="Active alerts have no measured first triage response yet.",
            evidence=observed,
        )
    remaining = _bounded_budget((max_mtta_seconds - monitor.mtta_seconds) / max(max_mtta_seconds, 1))
    status = _slo_rate_status(monitor.mtta_seconds <= max_mtta_seconds, remaining)
    return SloObjectiveResult(
        name="triage_response_time",
        status=status,
        target_type="maximum",
        target=target,
        observed=observed,
        error_budget_remaining=remaining,
        detail=f"MTTA is {monitor.mtta_seconds}s; target is at most {max_mtta_seconds}s.",
        evidence=observed,
    )


def _slo_alert_delivery(
    *,
    summary: AlertDeliverySummary | None,
    active_p0p1_alerts: int,
    max_dead_count: int,
    observed_dead_count: int,
) -> SloObjectiveResult:
    target = {"max_dead_count": max_dead_count, "terminal_statuses": ["ok", "disabled_without_active_p0p1"]}
    if summary is None:
        return SloObjectiveResult(
            name="alert_delivery_health",
            status="no_data",
            target_type="state",
            target=target,
            detail="Alert delivery outbox is not configured.",
        )
    observed = _ops_delivery_summary_evidence(summary)
    observed["active_p0p1_alert_count"] = active_p0p1_alerts
    observed["dead_count"] = observed_dead_count
    status: Literal["met", "at_risk", "breached", "no_data"] = "met"
    if observed_dead_count > max_dead_count or summary.status == "failed":
        status = "breached"
    elif summary.status in {"queued", "degraded"} or (summary.status == "disabled" and active_p0p1_alerts):
        status = "at_risk"
    elif summary.status == "disabled":
        status = "no_data"
    return SloObjectiveResult(
        name="alert_delivery_health",
        status=status,
        target_type="state",
        target=target,
        observed=observed,
        error_budget_remaining=1.0 if status == "met" else 0.25 if status == "at_risk" else 0.0,
        detail=(
            f"Alert delivery is {summary.status}; {observed_dead_count} dead-letter row(s), "
            f"{summary.pending_count} pending row(s)."
        ),
        evidence=observed,
    )


def _slo_rate_status(
    within_target: bool,
    error_budget_remaining: float,
) -> Literal["met", "at_risk", "breached"]:
    if not within_target:
        return "breached"
    if error_budget_remaining < 0.25:
        return "at_risk"
    return "met"


def _bounded_budget(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _slo_report_status(objectives: list[SloObjectiveResult]) -> Literal["healthy", "watch", "breached", "unknown"]:
    if not objectives or all(objective.status == "no_data" for objective in objectives):
        return "unknown"
    if any(objective.status == "breached" for objective in objectives):
        return "breached"
    if any(objective.status in {"at_risk", "no_data"} for objective in objectives):
        return "watch"
    return "healthy"


async def _operations_automation_plan_response(
    *,
    deps: AppContainer,
    actor_user_id: str,
    source: Literal["event_store", "live"],
    deep: bool,
    window_hours: int,
    limit: int,
    stale_after_minutes: int,
    max_active_p0p1_alerts: int,
    max_active_alerts: int,
    max_tool_failure_rate: float,
    max_feedback_negative_rate: float,
    max_eval_age_hours: int,
    min_tool_calls: int,
    min_feedback_count: int,
) -> OperationsAutomationPlan:
    promotion = await _promotion_gate_response(
        deps=deps,
        source=source,
        deep=deep,
        window_hours=window_hours,
        max_active_p0p1_alerts=max_active_p0p1_alerts,
        max_active_alerts=max_active_alerts,
        max_tool_failure_rate=max_tool_failure_rate,
        max_feedback_negative_rate=max_feedback_negative_rate,
        max_eval_age_hours=max_eval_age_hours,
        min_tool_calls=min_tool_calls,
        min_feedback_count=min_feedback_count,
    )
    generated_at = promotion.generated_at
    created_after = generated_at - timedelta(hours=window_hours)
    summary = _load_monitor_summary_for_automation(
        deps=deps,
        source=promotion.source,
        created_after=created_after,
        limit=limit,
    )
    active_alerts = [alert for alert in summary.alerts if _ops_alert_requires_attention(alert)]
    active_p0p1_alerts = [alert for alert in active_alerts if alert.severity in {"P0", "P1"}]
    top_active_alert = active_alerts[0] if active_alerts else None
    top_sample_run_id = _ops_first_sample_run_id(top_active_alert)
    top_sample_event_id = _ops_first_sample_event_id(top_active_alert)
    webhook_enabled = bool(_monitor_alert_webhook_url(deps))
    delivery_summary = _safe_monitor_alert_delivery_summary(deps, limit=200)
    dead_deliveries = _list_ops_delivery_records(deps, status=AlertDeliveryStatus.dead, limit=3)
    actions: list[OperationsAutomationAction] = []

    if active_p0p1_alerts and webhook_enabled:
        highest = active_p0p1_alerts[0].severity
        actions.append(
            _ops_action(
                kind="dispatch_alert_deliveries",
                key=f"{promotion.source}:{highest}:{len(active_p0p1_alerts)}",
                priority=highest,
                title="Dispatch active P0/P1 alert deliveries",
                detail=(
                    f"{len(active_p0p1_alerts)} active P0/P1 alert(s) need outbound notification; "
                    "enqueue and dispatch due webhook rows."
                ),
                safe_to_auto_execute=True,
                required_scopes=["monitor:write"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path="/api/v1/admin/monitor/alert-deliveries/dispatch",
                    query={"source": promotion.source, "monitor_limit": limit, "dispatch_limit": 25},
                ),
                evidence={
                    "active_p0p1_alert_count": len(active_p0p1_alerts),
                    "webhook_enabled": webhook_enabled,
                },
            )
        )
    elif active_p0p1_alerts:
        actions.append(
            _ops_action(
                kind="configure_alert_webhook",
                key=f"{promotion.source}:{len(active_p0p1_alerts)}",
                priority=active_p0p1_alerts[0].severity,
                title="Configure alert webhook before dispatch",
                detail=(
                    f"{len(active_p0p1_alerts)} active P0/P1 alert(s) exist, but webhook delivery is disabled."
                ),
                safe_to_auto_execute=False,
                required_scopes=["monitor:write"],
                evidence={
                    "active_p0p1_alert_count": len(active_p0p1_alerts),
                    "required_settings": [
                        "APP_MONITOR_ALERT_WEBHOOK_ENABLED=true",
                        "APP_MONITOR_ALERT_WEBHOOK_URL",
                    ],
                },
            )
        )
    elif delivery_summary and webhook_enabled and (
        delivery_summary.pending_count or delivery_summary.in_progress_count or delivery_summary.failed_count
    ):
        actions.append(
            _ops_action(
                kind="dispatch_alert_deliveries",
                key=f"queued:{delivery_summary.pending_count}:{delivery_summary.failed_count}",
                priority="P2",
                title="Flush queued alert deliveries",
                detail="Alert delivery outbox has due or previously failed rows that can be retried by the dispatcher.",
                safe_to_auto_execute=True,
                required_scopes=["monitor:write"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path="/api/v1/admin/monitor/alert-deliveries/dispatch",
                    query={"source": promotion.source, "monitor_limit": limit, "dispatch_limit": 25},
                ),
                evidence=_ops_delivery_summary_evidence(delivery_summary),
            )
        )

    unassigned_alert = next((alert for alert in active_alerts if not alert.assignee_user_id), None)
    if unassigned_alert:
        actions.append(
            _ops_action(
                kind="assign_triage_owner",
                key=unassigned_alert.key,
                priority=unassigned_alert.severity,
                title="Assign an owner to the top active alert",
                detail=f"{unassigned_alert.reason} is active and unassigned.",
                safe_to_auto_execute=False,
                required_scopes=["monitor:write"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path=f"/api/v1/admin/monitor/alerts/{unassigned_alert.key}/triage",
                    body={
                        "status": "investigating",
                        "assignee_user_id": actor_user_id,
                        "note": "Automation plan: assign current operator before mitigation.",
                    },
                ),
                evidence=_ops_alert_evidence(unassigned_alert),
            )
        )

    recurring_alert = next((alert for alert in active_alerts if alert.new_events_since_triage), None)
    if recurring_alert:
        actions.append(
            _ops_action(
                kind="retriage_recurring_alert",
                key=recurring_alert.key,
                priority=recurring_alert.severity,
                title="Re-triage an alert with new evidence",
                detail=f"{recurring_alert.reason} has new monitor events after the latest triage action.",
                safe_to_auto_execute=False,
                required_scopes=["monitor:write"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path=f"/api/v1/admin/monitor/alerts/{recurring_alert.key}/triage",
                    body={
                        "status": "investigating",
                        "assignee_user_id": recurring_alert.assignee_user_id or actor_user_id,
                        "note": "Automation plan: new monitor events arrived after triage.",
                    },
                ),
                evidence=_ops_alert_evidence(recurring_alert),
            )
        )

    stale_after = timedelta(minutes=stale_after_minutes)
    stale_alert = next((alert for alert in active_alerts if generated_at - alert.first_seen_at >= stale_after), None)
    if stale_alert:
        actions.append(
            _ops_action(
                kind="investigate_stale_alert",
                key=stale_alert.key,
                priority=stale_alert.severity,
                title="Investigate stale active alert",
                detail=(
                    f"{stale_alert.reason} has been active for at least {stale_after_minutes} minute(s); "
                    "confirm customer impact and mitigation before resolving."
                ),
                safe_to_auto_execute=False,
                required_scopes=["monitor:read", "events:read", "audit:read"],
                command=OperationsAutomationCommand(
                    method="GET",
                    path="/api/v1/admin/monitor/drilldown",
                    query={"source": promotion.source, "alert_key": stale_alert.key, "limit": 100},
                ),
                evidence=_ops_alert_evidence(stale_alert),
            )
        )

    if dead_deliveries:
        record = dead_deliveries[0]
        actions.append(
            _ops_action(
                kind="requeue_dead_delivery",
                key=record.id,
                priority=record.severity,
                title="Review and requeue dead-lettered alert delivery",
                detail=(
                    f"Delivery {record.id} for {record.alert_key} is dead-lettered after "
                    f"{record.attempt_count} attempt(s)."
                ),
                safe_to_auto_execute=False,
                required_scopes=["monitor:write"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path=f"/api/v1/admin/monitor/alert-deliveries/{record.id}/requeue",
                    body={"note": "Automation plan: destination verified; requeue dead-letter delivery."},
                ),
                evidence=_ops_delivery_record_evidence(record),
            )
        )

    if top_active_alert and top_sample_run_id:
        actions.append(
            _ops_action(
                kind="generate_incident_brief",
                key=top_sample_run_id,
                priority=top_active_alert.severity,
                title="Generate sanitized incident brief",
                detail="Prepare an operator-safe incident brief for the top active alert sample run.",
                safe_to_auto_execute=True,
                required_scopes=["events:read", "monitor:read", "audit:read", "memory:replay"],
                command=OperationsAutomationCommand(
                    method="GET",
                    path=f"/api/v1/admin/incidents/runs/{top_sample_run_id}/brief",
                    query={"include_memory": True, "limit": 1000},
                ),
                evidence={
                    "alert": _ops_alert_evidence(top_active_alert),
                    "run_id": top_sample_run_id,
                },
            )
        )
        actions.append(
            _ops_action(
                kind="create_regression_draft",
                key=top_sample_run_id,
                priority="P2",
                title="Draft a regression eval from the incident",
                detail="Convert the sample run and monitor event into a redacted regression case before shipping a fix.",
                safe_to_auto_execute=True,
                required_scopes=["events:read", "monitor:read"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path="/api/v1/admin/evals/regression-drafts",
                    body={
                        "run_id": top_sample_run_id,
                        "monitor_event_id": top_sample_event_id,
                        "source": promotion.source,
                        "limit": 1000,
                    },
                ),
                evidence={
                    "alert_key": top_active_alert.key,
                    "run_id": top_sample_run_id,
                    "monitor_event_id": top_sample_event_id,
                },
            )
        )

    if promotion.status == "blocked":
        blocked_checks = [check for check in promotion.checks if check.status == "blocked"]
        actions.append(
            _ops_action(
                kind="block_promotion",
                key="promotion:blocked",
                priority="P0" if active_p0p1_alerts else "P1",
                title="Keep promotion blocked",
                detail=f"{len(blocked_checks)} release gate check(s) are blocked; do not approve without override.",
                safe_to_auto_execute=True,
                required_scopes=["admin:read", "monitor:read", "audit:read", "eval:read", "feedback:read"],
                command=_ops_promotion_gate_command(
                    source=promotion.source,
                    deep=deep,
                    window_hours=window_hours,
                    max_active_p0p1_alerts=max_active_p0p1_alerts,
                    max_active_alerts=max_active_alerts,
                    max_tool_failure_rate=max_tool_failure_rate,
                    max_feedback_negative_rate=max_feedback_negative_rate,
                    max_eval_age_hours=max_eval_age_hours,
                    min_tool_calls=min_tool_calls,
                    min_feedback_count=min_feedback_count,
                ),
                evidence={
                    "status": promotion.status,
                    "blocked_checks": [check.name for check in blocked_checks],
                },
            )
        )
    elif promotion.status == "warn":
        warn_checks = [check for check in promotion.checks if check.status == "warn"]
        actions.append(
            _ops_action(
                kind="review_promotion_gate",
                key="promotion:warn",
                priority="P2",
                title="Review promotion warnings",
                detail=f"{len(warn_checks)} release gate warning(s) need operator acknowledgement before approval.",
                safe_to_auto_execute=True,
                required_scopes=["admin:read", "monitor:read", "audit:read", "eval:read", "feedback:read"],
                command=_ops_promotion_gate_command(
                    source=promotion.source,
                    deep=deep,
                    window_hours=window_hours,
                    max_active_p0p1_alerts=max_active_p0p1_alerts,
                    max_active_alerts=max_active_alerts,
                    max_tool_failure_rate=max_tool_failure_rate,
                    max_feedback_negative_rate=max_feedback_negative_rate,
                    max_eval_age_hours=max_eval_age_hours,
                    min_tool_calls=min_tool_calls,
                    min_feedback_count=min_feedback_count,
                ),
                evidence={
                    "status": promotion.status,
                    "warn_checks": [check.name for check in warn_checks],
                },
            )
        )

    eval_age_hours = _datetime_age_hours(
        promotion.latest_eval_gate.completed_at if promotion.latest_eval_gate else None,
        generated_at,
    )
    eval_stale = eval_age_hours is not None and eval_age_hours > max_eval_age_hours
    eval_failed = promotion.latest_eval_gate is None or promotion.latest_eval_gate.status != "passed"
    if eval_failed or eval_stale:
        actions.append(
            _ops_action(
                kind="run_staging_eval",
                key=f"staging:{promotion.latest_eval_gate.id if promotion.latest_eval_gate else 'missing'}",
                priority="P1" if promotion.status == "blocked" else "P2",
                title="Run the staging eval gate",
                detail=(
                    "Latest aggregate staging eval is missing, failed, errored, or stale; run this in CI/staging "
                    "before promotion."
                ),
                safe_to_auto_execute=False,
                required_scopes=["eval:run"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path="/api/v1/admin/evals/staging",
                    body={
                        "trigger": "console",
                        "run_id": top_sample_run_id,
                        "alert_key": top_active_alert.key if top_active_alert else None,
                    },
                ),
                evidence={
                    "latest_eval_gate_id": promotion.latest_eval_gate.id if promotion.latest_eval_gate else None,
                    "latest_eval_status": promotion.latest_eval_gate.status if promotion.latest_eval_gate else None,
                    "age_hours": eval_age_hours,
                    "max_eval_age_hours": max_eval_age_hours,
                    "production_guard": "Bundled eval routes reject production execution.",
                },
            )
        )

    if promotion.tool_audit.total_calls >= min_tool_calls and promotion.tool_audit.failure_rate > max_tool_failure_rate:
        top_error = promotion.tool_audit.top_error_codes[0].error_code if promotion.tool_audit.top_error_codes else None
        actions.append(
            _ops_action(
                kind="inspect_tool_audit",
                key=f"tool:{top_error or 'failed'}",
                priority="P1",
                title="Inspect elevated tool failure rate",
                detail=(
                    f"Tool failure rate is {promotion.tool_audit.failure_rate:.1%}, above "
                    f"{max_tool_failure_rate:.1%}."
                ),
                safe_to_auto_execute=True,
                required_scopes=["audit:read"],
                command=OperationsAutomationCommand(
                    method="GET",
                    path="/api/v1/admin/tools/audit",
                    query={"status": "failed", "limit": 100, "order": "desc"},
                ),
                evidence={
                    "total_calls": promotion.tool_audit.total_calls,
                    "failed_calls": promotion.tool_audit.failed_calls,
                    "failure_rate": promotion.tool_audit.failure_rate,
                    "top_error_code": top_error,
                },
            )
        )

    if promotion.feedback.total_count >= min_feedback_count and promotion.feedback.negative_rate > max_feedback_negative_rate:
        actions.append(
            _ops_action(
                kind="review_feedback",
                key="feedback:negative",
                priority="P2",
                title="Review negative feedback cluster",
                detail=(
                    f"Negative feedback rate is {promotion.feedback.negative_rate:.1%}, above "
                    f"{max_feedback_negative_rate:.1%}."
                ),
                safe_to_auto_execute=True,
                required_scopes=["feedback:read"],
                command=OperationsAutomationCommand(
                    method="GET",
                    path="/api/v1/admin/feedback",
                    query={"rating": "negative", "limit": 100, "order": "desc"},
                ),
                evidence={
                    "total_count": promotion.feedback.total_count,
                    "negative_count": promotion.feedback.negative_count,
                    "negative_rate": promotion.feedback.negative_rate,
                    "top_reasons": [
                        reason.model_dump(mode="json") for reason in promotion.feedback.counts_by_reason[:5]
                    ],
                },
            )
        )

    if summary.total_events and summary.grounded_rate < 0.95:
        query = top_active_alert.reason if top_active_alert else next(iter(summary.by_failure_type), "retrieval coverage")
        actions.append(
            _ops_action(
                kind="run_retrieval_diagnostics",
                key=f"retrieval:{query}",
                priority="P2",
                title="Run retrieval diagnostics for weak grounding",
                detail=f"Grounded rate is {summary.grounded_rate:.1%}; inspect recall and selected sources.",
                safe_to_auto_execute=True,
                required_scopes=["knowledge:diagnose"],
                command=OperationsAutomationCommand(
                    method="POST",
                    path="/api/v1/admin/knowledge/search",
                    body={"query": query, "limit": 6, "snippet_chars": 300},
                ),
                evidence={
                    "grounded_rate": summary.grounded_rate,
                    "total_events": summary.total_events,
                    "query_seed": query,
                },
            )
        )

    if not actions:
        actions.append(
            _ops_action(
                kind="no_action_required",
                key=f"{promotion.source}:{window_hours}",
                priority="P3",
                title="No immediate automation action required",
                detail="Monitor, delivery, feedback, tool audit, and eval signals are within configured thresholds.",
                safe_to_auto_execute=True,
                required_scopes=[],
                evidence={
                    "promotion_status": promotion.status,
                    "health_status": promotion.monitor.health_status,
                },
            )
        )

    actions = _sort_ops_actions(actions)
    evidence = {
        "monitor": {
            "total_events": summary.total_events,
            "alert_count": len(summary.alerts),
            "active_alert_count": len(active_alerts),
            "active_p0p1_alert_count": len(active_p0p1_alerts),
            "grounded_rate": summary.grounded_rate,
            "policy_compliance_rate": summary.policy_compliance_rate,
            "human_review_rate": summary.human_review_rate,
            "top_active_alert": _ops_alert_evidence(top_active_alert) if top_active_alert else None,
        },
        "triage": {
            "health_status": promotion.monitor.health_status,
            "new_events_since_triage_count": promotion.monitor.new_events_since_triage_count,
            "stale_active_alert_count": promotion.monitor.stale_active_alert_count,
            "unassigned_active_alert_count": promotion.monitor.unassigned_active_alert_count,
        },
        "alert_delivery": _ops_delivery_summary_evidence(delivery_summary) if delivery_summary else None,
        "promotion_gate": {
            "status": promotion.status,
            "checks": [
                {"name": check.name, "status": check.status, "detail": check.detail}
                for check in promotion.checks
            ],
        },
        "tool_audit": {
            "total_calls": promotion.tool_audit.total_calls,
            "failed_calls": promotion.tool_audit.failed_calls,
            "failure_rate": promotion.tool_audit.failure_rate,
        },
        "feedback": {
            "total_count": promotion.feedback.total_count,
            "negative_count": promotion.feedback.negative_count,
            "negative_rate": promotion.feedback.negative_rate,
        },
        "latest_eval_gate": {
            "id": promotion.latest_eval_gate.id if promotion.latest_eval_gate else None,
            "status": promotion.latest_eval_gate.status if promotion.latest_eval_gate else None,
            "age_hours": eval_age_hours,
        },
    }
    return OperationsAutomationPlan(
        generated_at=generated_at,
        environment=deps.settings.app_env,
        source=promotion.source,
        window_hours=window_hours,
        health_status=promotion.monitor.health_status,
        action_count=len(actions),
        auto_executable_count=sum(1 for action in actions if action.safe_to_auto_execute),
        actions=actions,
        evidence=evidence,
        guardrails=OPS_AUTOMATION_GUARDRAILS,
    )


def _ops_action(
    *,
    kind: OperationsAutomationAction.model_fields["kind"].annotation,
    key: str,
    priority: Literal["P0", "P1", "P2", "P3"],
    title: str,
    detail: str,
    safe_to_auto_execute: bool,
    required_scopes: list[str],
    command: OperationsAutomationCommand | None = None,
    evidence: dict[str, Any] | None = None,
) -> OperationsAutomationAction:
    return OperationsAutomationAction(
        id=_ops_action_id(str(kind), key),
        kind=kind,
        priority=priority,
        title=title,
        detail=detail,
        safe_to_auto_execute=safe_to_auto_execute,
        required_scopes=required_scopes,
        command=command,
        evidence=evidence or {},
    )


def _ops_action_id(kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:10]
    return f"ops_{kind}_{digest}"


def _sort_ops_actions(actions: list[OperationsAutomationAction]) -> list[OperationsAutomationAction]:
    return sorted(
        actions,
        key=lambda action: (
            OPS_PRIORITY_RANK.get(action.priority, 9),
            0 if action.safe_to_auto_execute else 1,
            action.kind,
            action.id,
        ),
    )


def _load_monitor_summary_for_automation(
    *,
    deps: AppContainer,
    source: Literal["event_store", "live"],
    created_after: datetime,
    limit: int,
) -> MonitorSummary:
    if source == "event_store" and deps.event_store:
        events = deps.event_store.list_monitor_events(
            tenant_id=deps.settings.app_tenant_id,
            created_after=created_after.isoformat(),
            limit=limit,
            order="desc",
        )
        triage_events = deps.event_store.list_monitor_alert_triage_events(
            tenant_id=deps.settings.app_tenant_id,
            limit=500,
        )
        return summarize_monitor_events(events, triage_events=triage_events)
    events = [event for event in deps.monitor.events if event.timestamp >= created_after]
    events = sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]
    return summarize_monitor_events(events)


def _safe_monitor_alert_delivery_summary(deps: AppContainer, limit: int) -> AlertDeliverySummary | None:
    if not deps.event_store:
        return None
    return _monitor_alert_delivery_summary(deps, limit=limit)


def _list_ops_delivery_records(
    deps: AppContainer,
    *,
    status: AlertDeliveryStatus,
    limit: int,
) -> list[AlertDeliveryRecord]:
    if not deps.event_store:
        return []
    return deps.event_store.list_alert_delivery_records(
        tenant_id=deps.settings.app_tenant_id,
        statuses=[status.value],
        limit=limit,
        order="desc",
    )


def _ops_alert_requires_attention(alert: MonitorAlert) -> bool:
    return _enum_value(alert.status) in OPS_ACTIVE_ALERT_STATUSES or alert.new_events_since_triage


def _ops_first_sample_run_id(alert: MonitorAlert | None) -> str | None:
    if not alert:
        return None
    return alert.sample_run_ids[0] if alert.sample_run_ids else None


def _ops_first_sample_event_id(alert: MonitorAlert | None) -> str | None:
    if not alert:
        return None
    return alert.sample_event_ids[0] if alert.sample_event_ids else None


def _ops_alert_evidence(alert: MonitorAlert) -> dict[str, Any]:
    return {
        "key": alert.key,
        "severity": alert.severity,
        "status": _enum_value(alert.status),
        "count": alert.count,
        "reason": alert.reason,
        "assignee_user_id": alert.assignee_user_id,
        "new_events_since_triage": alert.new_events_since_triage,
        "first_seen_at": alert.first_seen_at,
        "last_seen_at": alert.last_seen_at,
        "sample_run_ids": alert.sample_run_ids[:3],
        "sample_event_ids": alert.sample_event_ids[:3],
    }


def _ops_delivery_summary_evidence(summary: AlertDeliverySummary) -> dict[str, Any]:
    return {
        "status": summary.status,
        "webhook_enabled": summary.webhook_enabled,
        "pending_count": summary.pending_count,
        "in_progress_count": summary.in_progress_count,
        "failed_count": summary.failed_count,
        "dead_count": summary.dead_count,
        "oldest_pending_at": summary.oldest_pending_at,
        "next_attempt_at": summary.next_attempt_at,
        "last_attempt_at": summary.last_attempt_at,
        "last_success_at": summary.last_success_at,
        "last_error": summary.last_error,
    }


def _ops_delivery_record_evidence(record: AlertDeliveryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "alert_key": record.alert_key,
        "severity": record.severity,
        "status": _enum_value(record.status),
        "attempt_count": record.attempt_count,
        "last_attempt_at": record.last_attempt_at,
        "dead_lettered_at": record.dead_lettered_at,
        "response_status_code": record.response_status_code,
        "last_error": record.last_error,
    }


def _ops_promotion_gate_command(
    *,
    source: Literal["event_store", "live"],
    deep: bool,
    window_hours: int,
    max_active_p0p1_alerts: int,
    max_active_alerts: int,
    max_tool_failure_rate: float,
    max_feedback_negative_rate: float,
    max_eval_age_hours: int,
    min_tool_calls: int,
    min_feedback_count: int,
) -> OperationsAutomationCommand:
    return OperationsAutomationCommand(
        method="GET",
        path="/api/v1/admin/promotion/gate",
        query={
            "source": source,
            "deep": deep,
            "window_hours": window_hours,
            "max_active_p0p1_alerts": max_active_p0p1_alerts,
            "max_active_alerts": max_active_alerts,
            "max_tool_failure_rate": max_tool_failure_rate,
            "max_feedback_negative_rate": max_feedback_negative_rate,
            "max_eval_age_hours": max_eval_age_hours,
            "min_tool_calls": min_tool_calls,
            "min_feedback_count": min_feedback_count,
        },
    )


def _load_monitor_triage_metrics(
    *,
    deps: AppContainer,
    source: Literal["event_store", "live"],
    created_after: datetime,
    limit: int,
) -> MonitorTriageMetricsResponse:
    triage_events: list[MonitorAlertTriageEvent] = []
    if source == "event_store" and deps.event_store:
        events = deps.event_store.list_monitor_events(
            tenant_id=deps.settings.app_tenant_id,
            created_after=created_after.isoformat(),
            limit=limit,
            order="desc",
        )
        triage_events = deps.event_store.list_monitor_alert_triage_events(
            tenant_id=deps.settings.app_tenant_id,
            limit=500,
        )
    else:
        events = [event for event in deps.monitor.events if event.timestamp >= created_after]
        events = sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]
        source = "live"
    return monitor_triage_metrics_response(
        source=source,
        events=events,
        triage_events=triage_events,
        conversation_id=None,
        created_after=created_after,
        created_before=None,
        limit=limit,
        order="desc",
        stale_after=timedelta(minutes=60),
    )


def _load_tool_audit_summary(
    *,
    deps: AppContainer,
    created_after: datetime,
) -> ToolAuditSummary:
    if not deps.event_store:
        return _empty_tool_audit_summary()
    return deps.event_store.summarize_tool_audit_records(
        tenant_id=deps.settings.app_tenant_id,
        created_after=created_after.isoformat(),
    )


def _load_feedback_summary(*, deps: AppContainer, created_after: datetime) -> FeedbackSummary:
    if not deps.event_store:
        return FeedbackSummary()
    return deps.event_store.summarize_agent_feedback(
        tenant_id=deps.settings.app_tenant_id,
        created_after=created_after.isoformat(),
    )


def _empty_tool_audit_summary() -> ToolAuditSummary:
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


def _normalize_feedback_reasons(reasons: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        next_reason = reason.strip().lower().replace(" ", "_")[:80]
        if not next_reason or next_reason in seen:
            continue
        seen.add(next_reason)
        normalized.append(next_reason)
        if len(normalized) >= 10:
            break
    return normalized


def _observe_http_request(app: FastAPI, request: Request, *, status_code: int, started: float) -> None:
    app.state.http_metrics.observe_request(
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        duration_ms=(perf_counter() - started) * 1000,
    )


def _latest_promotion_eval_gate(deps: AppContainer) -> EvalGateRecord | None:
    if not deps.event_store:
        return None
    records = deps.event_store.list_eval_gate_records(
        tenant_id=deps.settings.app_tenant_id,
        gate_name=STAGING_EVAL_GATE_NAME,
        runner="aggregate",
        limit=1,
    )
    return records[0] if records else None


def _promotion_readiness_check(readiness: ReadinessResponse) -> PromotionGateCheck:
    failed = [check for check in readiness.checks if check.status == "failed"]
    if failed:
        return PromotionGateCheck(
            name="readiness",
            status="blocked",
            detail=f"{len(failed)} readiness check(s) failed.",
            evidence={"failed_checks": [check.name for check in failed], "status": readiness.status},
        )
    skipped = [check for check in readiness.checks if check.status == "skipped"]
    if readiness.deep is False and skipped:
        return PromotionGateCheck(
            name="readiness",
            status="warn",
            detail="Readiness passed, but deep dependency checks were skipped.",
            evidence={"skipped_checks": [check.name for check in skipped], "status": readiness.status},
        )
    return PromotionGateCheck(
        name="readiness",
        status="passed",
        detail="Readiness checks passed.",
        evidence={"status": readiness.status, "deep": readiness.deep},
    )


def _promotion_alert_check(
    monitor: MonitorTriageMetricsResponse,
    max_active_p0p1_alerts: int,
    max_active_alerts: int,
) -> PromotionGateCheck:
    active_p0p1 = (monitor.active_by_severity.get("P0", 0) or 0) + (
        monitor.active_by_severity.get("P1", 0) or 0
    )
    evidence = {
        "active_alert_count": monitor.active_alert_count,
        "active_p0p1_alert_count": active_p0p1,
        "new_events_since_triage_count": monitor.new_events_since_triage_count,
        "health_status": monitor.health_status,
    }
    if active_p0p1 > max_active_p0p1_alerts:
        return PromotionGateCheck(
            name="monitor_alerts",
            status="blocked",
            detail=f"Active P0/P1 alerts exceed threshold: {active_p0p1} > {max_active_p0p1_alerts}.",
            evidence=evidence,
        )
    if monitor.active_alert_count > max_active_alerts:
        return PromotionGateCheck(
            name="monitor_alerts",
            status="warn",
            detail=f"Active alerts exceed warning threshold: {monitor.active_alert_count} > {max_active_alerts}.",
            evidence=evidence,
        )
    if monitor.new_events_since_triage_count:
        return PromotionGateCheck(
            name="monitor_alerts",
            status="warn",
            detail="Some alerts have new monitor events after the latest triage action.",
            evidence=evidence,
        )
    return PromotionGateCheck(
        name="monitor_alerts",
        status="passed",
        detail="Monitor alert pressure is within thresholds.",
        evidence=evidence,
    )


def _promotion_tool_audit_check(
    summary: ToolAuditSummary,
    max_tool_failure_rate: float,
    min_tool_calls: int,
) -> PromotionGateCheck:
    evidence = {
        "total_calls": summary.total_calls,
        "failed_calls": summary.failed_calls,
        "failure_rate": summary.failure_rate,
        "top_error_codes": [item.error_code for item in summary.top_error_codes],
    }
    if summary.total_calls < min_tool_calls:
        return PromotionGateCheck(
            name="tool_audit",
            status="warn",
            detail=f"Only {summary.total_calls} audited tool call(s) in the window; threshold evidence is thin.",
            evidence=evidence,
        )
    if summary.failure_rate > max_tool_failure_rate:
        return PromotionGateCheck(
            name="tool_audit",
            status="blocked",
            detail=f"Tool failure rate exceeds threshold: {summary.failure_rate:.1%} > {max_tool_failure_rate:.1%}.",
            evidence=evidence,
        )
    return PromotionGateCheck(
        name="tool_audit",
        status="passed",
        detail="Tool failure rate is within threshold.",
        evidence=evidence,
    )


def _promotion_feedback_check(
    summary: FeedbackSummary,
    max_negative_rate: float,
    min_feedback_count: int,
) -> PromotionGateCheck:
    evidence = {
        "total_count": summary.total_count,
        "positive_count": summary.positive_count,
        "negative_count": summary.negative_count,
        "negative_rate": summary.negative_rate,
        "max_negative_rate": max_negative_rate,
        "min_feedback_count": min_feedback_count,
        "window_start": summary.window_start,
        "window_end": summary.window_end,
        "top_reasons": [
            reason.model_dump(mode="json")
            for reason in summary.counts_by_reason[:5]
        ],
    }
    if summary.total_count < min_feedback_count:
        return PromotionGateCheck(
            name="feedback",
            status="warn",
            detail=f"Only {summary.total_count} feedback rating(s) in the window; threshold evidence is thin.",
            evidence=evidence,
        )
    if summary.negative_rate > max_negative_rate:
        return PromotionGateCheck(
            name="feedback",
            status="blocked",
            detail=f"Feedback negative rate exceeds threshold: {summary.negative_rate:.1%} > {max_negative_rate:.1%}.",
            evidence=evidence,
        )
    return PromotionGateCheck(
        name="feedback",
        status="passed",
        detail="Feedback negative rate is within threshold.",
        evidence=evidence,
    )


def _promotion_eval_gate_check(
    record: EvalGateRecord | None,
    generated_at: datetime,
    max_eval_age_hours: int,
) -> PromotionGateCheck:
    if record is None:
        return PromotionGateCheck(
            name="staging_eval_gate",
            status="blocked",
            detail="No aggregate staging eval gate record is available.",
            evidence={},
        )
    age_hours = _datetime_age_hours(record.completed_at, generated_at)
    evidence = {
        "gate_id": record.id,
        "status": record.status,
        "suite_id": record.suite_id,
        "passed": record.passed,
        "total": record.total,
        "age_hours": age_hours,
    }
    if record.status != "passed":
        return PromotionGateCheck(
            name="staging_eval_gate",
            status="blocked",
            detail=f"Latest aggregate staging eval gate is {record.status}.",
            evidence=evidence,
        )
    if age_hours is not None and age_hours > max_eval_age_hours:
        return PromotionGateCheck(
            name="staging_eval_gate",
            status="warn",
            detail=f"Latest aggregate staging eval gate is older than {max_eval_age_hours} hour(s).",
            evidence=evidence,
        )
    return PromotionGateCheck(
        name="staging_eval_gate",
        status="passed",
        detail="Latest aggregate staging eval gate passed.",
        evidence=evidence,
    )


def _promotion_status(checks: list[PromotionGateCheck]) -> Literal["passed", "warn", "blocked"]:
    if any(check.status == "blocked" for check in checks):
        return "blocked"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "passed"


def _promotion_decision_from_event(event: StoredEvent) -> PromotionDecisionRecord:
    return PromotionDecisionRecord.model_validate(event.payload)


def _audit_export_rows(
    *,
    deps: AppContainer,
    include_events: bool,
    include_tool_audit: bool,
    event_type: str | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
    order: Literal["asc", "desc"],
) -> list[dict[str, Any]]:
    if not deps.event_store:
        return []
    rows: list[dict[str, Any]] = []
    if include_events:
        rows.extend(
            _audit_event_row(event)
            for event in deps.event_store.list_events(
                tenant_id=deps.settings.app_tenant_id,
                event_type=event_type,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                order=order,
            )
        )
    if include_tool_audit:
        rows.extend(
            _audit_tool_row(record)
            for record in deps.event_store.list_tool_audit_records(
                tenant_id=deps.settings.app_tenant_id,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                order=order,
            )
        )
    reverse = order == "desc"
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=reverse)
    return rows[:limit]


def _audit_event_row(event: StoredEvent) -> dict[str, Any]:
    return {
        "schema_version": "audit_export.v1",
        "record_type": "event",
        "source": "events",
        "id": event.id,
        "tenant_id": event.tenant_id,
        "event_type": event.event_type,
        "created_at": event.created_at,
        "correlation": _audit_correlation(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            conversation_id=event.conversation_id,
            run_id=event.run_id,
        ),
        "payload_summary": _audit_payload_summary(event.payload),
    }


def _audit_tool_row(record: ToolAuditRecord) -> dict[str, Any]:
    return {
        "schema_version": "audit_export.v1",
        "record_type": "tool_audit",
        "source": "tool_audit_records",
        "id": record.id,
        "tenant_id": record.tenant_id,
        "tool_name": record.tool_name,
        "status": record.status.value,
        "latency_ms": record.latency_ms,
        "error_code": record.error_code,
        "argument_hash": record.argument_hash,
        "idempotency_key_hash": record.idempotency_key_hash,
        "replayed": record.replayed,
        "created_at": record.created_at,
        "correlation": _audit_correlation(
            tenant_id=record.tenant_id,
            user_id=record.actor_user_id,
            run_id=record.trace_id,
            request_id=record.request_id,
        ),
    }


def _audit_correlation(
    *,
    tenant_id: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "tenant_id": tenant_id,
        "user_hash": _audit_hash(user_id),
        "conversation_hash": _audit_hash(conversation_id),
        "run_hash": _audit_hash(run_id),
        "request_hash": _audit_hash(request_id),
    }


def _audit_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"audit_export.v1:{value}".encode("utf-8")).hexdigest()[:32]


def _audit_payload_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "id",
        "status",
        "rating",
        "source",
        "decision",
        "gate_status",
        "target_version",
        "environment",
        "tool_name",
        "error_code",
        "risk_level",
        "user_intent",
        "needs_human_review",
        "grounded",
        "policy_compliant",
        "pii_leak",
        "severity",
        "gate_name",
        "runner",
        "suite_id",
        "trigger",
        "total",
        "passed",
        "score",
    ):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
    if isinstance(payload.get("reasons"), list):
        summary["reasons"] = _audit_string_list(payload.get("reasons"))
    if isinstance(payload.get("failure_types"), list):
        summary["failure_types"] = _audit_string_list(payload.get("failure_types"))
    if isinstance(payload.get("failed_case_ids"), list):
        summary["failed_case_count"] = len(payload["failed_case_ids"])
    if isinstance(payload.get("policy_findings"), list):
        summary["policy_codes"] = _audit_policy_codes(payload["policy_findings"])
    if isinstance(payload.get("tool_results"), list):
        tool_results = [item for item in payload["tool_results"] if isinstance(item, dict)]
        summary["tool_count"] = len(tool_results)
        summary["failed_tool_count"] = sum(1 for item in tool_results if item.get("status") != "success")
        summary["tool_names"] = sorted(
            {
                str(item.get("name"))
                for item in tool_results
                if isinstance(item.get("name"), str)
            }
        )
        summary["tool_error_codes"] = sorted(
            {
                str(item.get("error_code"))
                for item in tool_results
                if isinstance(item.get("error_code"), str) and item.get("error_code")
            }
        )
    intent = payload.get("intent")
    if isinstance(intent, dict):
        summary["intent_primary"] = intent.get("primary")
        summary["intent_confidence"] = intent.get("confidence")
    route = payload.get("route")
    if isinstance(route, dict):
        summary["route_target"] = route.get("target")
        summary["route_needs_human"] = route.get("needs_human")
    retrieval = payload.get("retrieval")
    if isinstance(retrieval, dict):
        selected_context = retrieval.get("selected_context")
        selected_sources = retrieval.get("selected_sources")
        if isinstance(selected_context, list):
            summary["retrieval_selected_count"] = len(selected_context)
        if isinstance(selected_sources, list):
            summary["retrieval_source_count"] = len(selected_sources)
    gate = payload.get("gate")
    if isinstance(gate, dict):
        summary["gate_snapshot_status"] = gate.get("status")
        checks = gate.get("checks")
        if isinstance(checks, list):
            summary["gate_check_statuses"] = {
                str(item.get("name")): item.get("status")
                for item in checks
                if isinstance(item, dict) and item.get("name")
            }
    alert_key = payload.get("alert_key")
    if isinstance(alert_key, str):
        summary["alert_key_hash"] = _audit_hash(alert_key)
    return {key: value for key, value in summary.items() if value not in ({}, [])}


def _audit_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item)[:120] for item in value if isinstance(item, (str, int, float))})


def _audit_policy_codes(findings: list[Any]) -> list[str]:
    codes: set[str] = set()
    for finding in findings:
        if isinstance(finding, dict) and isinstance(finding.get("code"), str):
            codes.add(finding["code"])
    return sorted(codes)


def _ndjson(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n"


INCIDENT_BRIEF_REDACTIONS = [
    "message_content",
    "tool_arguments",
    "tool_payloads",
    "tool_error_messages",
    "retrieval_content",
    "memory_facts",
    "feedback_comments",
]


def _load_incident_run_bundle(
    *,
    deps: AppContainer,
    run_id: str,
    include_memory: bool,
    limit: int,
) -> IncidentRunBundle:
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
        events = deps.event_store.list_conversation_memory_events(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=run.conversation_id,
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


def _incident_brief_response(
    bundle: IncidentRunBundle,
    *,
    generated_at: datetime | None = None,
) -> IncidentBriefResponse:
    generated_at = generated_at or utc_now()
    run = bundle.run
    monitor_summary = summarize_monitor_events(bundle.monitor_events)
    alert = monitor_summary.alerts[0] if monitor_summary.alerts else None
    monitor_event = bundle.monitor_events[0] if bundle.monitor_events else None
    tool_failures = [tool for tool in run.tool_results if _enum_value(tool.status) != "success"]
    tool_error_codes = _unique_strings(tool.error_code for tool in tool_failures if tool.error_code)
    policy_codes = _unique_strings(finding.code for finding in run.policy_findings)
    failure_types = _unique_strings(
        failure
        for event in bundle.monitor_events
        for failure in event.failure_types
    )
    citation_count = len(run.retrieval.selected_context) if run.retrieval else 0
    risk_label = alert.severity if alert else _incident_risk_label(bundle.monitor_events)
    alert_key = alert.key if alert else monitor_event.alert_key if monitor_event else None
    title = alert.reason if alert else f"Run {run.id}"
    summary = (
        f"Run {run.id} handled {_enum_value(run.intent.primary) if run.intent else 'unknown'} "
        f"via {_enum_value(run.route.target) if run.route else 'unknown'} with "
        f"{len(tool_failures)} tool failure(s), {len(run.policy_findings)} policy finding(s), "
        f"{len(bundle.monitor_events)} monitor event(s), and {citation_count} citation(s)."
    )
    evidence = _incident_brief_evidence(
        bundle=bundle,
        alert=alert,
        tool_failures=tool_failures,
        tool_error_codes=tool_error_codes,
        policy_codes=policy_codes,
        failure_types=failure_types,
    )
    recommended_actions = _incident_recommended_actions(
        alert=alert,
        monitor_events=bundle.monitor_events,
        tool_failures=tool_failures,
        tool_error_codes=tool_error_codes,
        policy_codes=policy_codes,
        citation_count=citation_count,
        memory_replay=bundle.memory_replay,
    )
    markdown = _incident_brief_markdown(
        generated_at=generated_at,
        run=run,
        title=title,
        risk_label=risk_label,
        alert=alert,
        alert_key=alert_key,
        summary=summary,
        evidence=evidence,
        recommended_actions=recommended_actions,
    )
    return IncidentBriefResponse(
        generated_at=generated_at,
        title=title,
        risk_label=risk_label,
        summary=summary,
        run_id=run.id,
        conversation_id=run.conversation_id,
        run_source=bundle.run_source,
        alert_key=alert_key,
        recommended_actions=recommended_actions,
        evidence=evidence,
        redactions=INCIDENT_BRIEF_REDACTIONS,
        markdown=markdown,
    )


def _incident_brief_evidence(
    *,
    bundle: IncidentRunBundle,
    alert: MonitorAlert | None,
    tool_failures: list[Any],
    tool_error_codes: list[str],
    policy_codes: list[str],
    failure_types: list[str],
) -> dict[str, Any]:
    run = bundle.run
    retrieval_sources: list[str] = []
    if run.retrieval:
        retrieval_sources = _unique_strings(
            [
                *run.retrieval.selected_sources,
                *(hit.document_id for hit in run.retrieval.selected_context),
            ]
        )[:8]
    audit_errors = Counter(record.error_code for record in bundle.tool_audit_records if record.error_code)
    audit_tools = _unique_strings(record.tool_name for record in bundle.tool_audit_records)[:12]
    memory = bundle.memory_replay
    return {
        "run": {
            "id": run.id,
            "conversation_id": run.conversation_id,
            "user_hash": _audit_hash(run.user_id),
            "agent_version": run.agent_version,
            "status": run.status,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "intent": _enum_value(run.intent.primary) if run.intent else None,
            "intent_confidence": run.intent.confidence if run.intent else None,
            "route": _enum_value(run.route.target) if run.route else None,
            "route_needs_human": run.route.needs_human if run.route else False,
            "tool_count": len(run.tool_results),
            "failed_tool_count": len(tool_failures),
            "tool_error_codes": tool_error_codes,
            "policy_codes": policy_codes,
            "llm_call_count": len(run.llm_calls),
            "llm_fallback_used": any(call.fallback_used for call in run.llm_calls),
            "citation_count": len(run.retrieval.selected_context) if run.retrieval else 0,
            "retrieval_sources": retrieval_sources,
        },
        "monitor": {
            "event_count": len(bundle.monitor_events),
            "alert_key": alert.key if alert else None,
            "severity": alert.severity if alert else None,
            "alert_status": _enum_value(alert.status) if alert else None,
            "assignee_user_id": alert.assignee_user_id if alert else None,
            "new_events_since_triage": alert.new_events_since_triage if alert else False,
            "failure_types": failure_types,
            "risk_levels": _unique_strings(_enum_value(event.risk_level) for event in bundle.monitor_events),
            "ungrounded_events": sum(1 for event in bundle.monitor_events if not event.grounded),
            "policy_violation_events": sum(1 for event in bundle.monitor_events if not event.policy_compliant),
            "human_review_events": sum(1 for event in bundle.monitor_events if event.needs_human_review),
            "pii_leak_events": sum(1 for event in bundle.monitor_events if event.pii_leak),
        },
        "tool_audit": {
            "record_count": len(bundle.tool_audit_records),
            "failed_record_count": sum(1 for record in bundle.tool_audit_records if _enum_value(record.status) == "failed"),
            "tools": audit_tools,
            "top_error_codes": [
                {"error_code": str(error_code), "count": count}
                for error_code, count in audit_errors.most_common(5)
            ],
        },
        "memory": {
            "included": memory is not None,
            "event_count": memory.event_count if memory else 0,
            "replayed_message_count": memory.replayed_message_count if memory else 0,
            "replayed_run_count": memory.replayed_run_count if memory else 0,
            "ignored_event_count": memory.ignored_event_count if memory else 0,
            "fact_count": len(memory.state.facts) if memory else 0,
            "open_question_count": len(memory.state.open_questions) if memory else 0,
        },
    }


def _incident_recommended_actions(
    *,
    alert: MonitorAlert | None,
    monitor_events: list[MonitorEvent],
    tool_failures: list[Any],
    tool_error_codes: list[str],
    policy_codes: list[str],
    citation_count: int,
    memory_replay: MemoryReplayResult | None,
) -> list[str]:
    actions: list[str] = []
    if alert and not alert.assignee_user_id and _enum_value(alert.status) in {"open", "acknowledged", "investigating"}:
        actions.append("Assign an owner before changing prompts or tools.")
    if alert and alert.new_events_since_triage:
        actions.append("Re-open triage because new monitor events arrived after the last action.")
    if tool_failures:
        codes = ", ".join(tool_error_codes) if tool_error_codes else "TOOL_FAILED"
        actions.append(f"Inspect tool audit and upstream health for {codes}.")
    if policy_codes:
        actions.append(f"Review policy findings before replaying the case: {', '.join(policy_codes)}.")
    if any(not event.grounded for event in monitor_events) or citation_count == 0:
        actions.append("Check retrieval diagnostics and add a retrieval challenge case if grounding is weak.")
    if memory_replay is None:
        actions.append("Fetch memory replay when the incident depends on earlier turns.")
    if not actions:
        actions.append("No blocking signal found; keep this brief as the audit note for the run.")
    actions.append("Turn the confirmed failure into a regression eval before shipping a fix.")
    return _unique_strings(actions)


def _incident_brief_markdown(
    *,
    generated_at: datetime,
    run: AgentRunTrace,
    title: str,
    risk_label: str,
    alert: MonitorAlert | None,
    alert_key: str | None,
    summary: str,
    evidence: dict[str, Any],
    recommended_actions: list[str],
) -> str:
    run_evidence = evidence["run"]
    monitor_evidence = evidence["monitor"]
    tool_audit_evidence = evidence["tool_audit"]
    memory_evidence = evidence["memory"]
    lines = [
        "# PSA Lab Incident Brief",
        "",
        f"- Generated: {generated_at.isoformat()}",
        f"- Title: {title}",
        f"- Risk: {risk_label}",
        f"- Alert: {alert_key or 'none'}",
        f"- Alert status: {_enum_value(alert.status) if alert else 'none'}",
        f"- Assignee: {alert.assignee_user_id if alert and alert.assignee_user_id else 'unassigned'}",
        f"- Run: {run.id}",
        f"- Conversation: {run.conversation_id}",
        f"- Run source: {run_evidence['status']} from {run_evidence['agent_version']}",
        "",
        "## Summary",
        summary,
        "",
        "## Run Evidence",
        f"- Intent: {run_evidence['intent'] or 'unknown'} ({run_evidence['intent_confidence'] or 'n/a'})",
        f"- Route: {run_evidence['route'] or 'unknown'}; human handoff: {run_evidence['route_needs_human']}",
        f"- Tools: {run_evidence['failed_tool_count']} failed / {run_evidence['tool_count']} total",
        f"- Tool errors: {_brief_join(run_evidence['tool_error_codes'])}",
        f"- Policy codes: {_brief_join(run_evidence['policy_codes'])}",
        f"- Citations: {run_evidence['citation_count']}",
        f"- Retrieval sources: {_brief_join(run_evidence['retrieval_sources'])}",
        "",
        "## Monitor Evidence",
        f"- Monitor events: {monitor_evidence['event_count']}",
        f"- Failure types: {_brief_join(monitor_evidence['failure_types'])}",
        f"- Risk levels: {_brief_join(monitor_evidence['risk_levels'])}",
        f"- Ungrounded events: {monitor_evidence['ungrounded_events']}",
        f"- Policy violations: {monitor_evidence['policy_violation_events']}",
        f"- Human review events: {monitor_evidence['human_review_events']}",
        "",
        "## Tool Audit Evidence",
        f"- Audit records: {tool_audit_evidence['record_count']}",
        f"- Failed audit records: {tool_audit_evidence['failed_record_count']}",
        f"- Tools: {_brief_join(tool_audit_evidence['tools'])}",
        f"- Top audit errors: {_brief_join(item['error_code'] for item in tool_audit_evidence['top_error_codes'])}",
        "",
        "## Memory Replay",
        f"- Included: {memory_evidence['included']}",
        f"- Events replayed: {memory_evidence['event_count']}",
        f"- Messages replayed: {memory_evidence['replayed_message_count']}",
        f"- Runs replayed: {memory_evidence['replayed_run_count']}",
        f"- Facts count: {memory_evidence['fact_count']}",
        "",
        "## Recommended Next Actions",
        *[f"- {action}" for action in recommended_actions],
        "",
        "## Redaction Contract",
        "- This brief excludes message content, tool arguments, tool payloads, tool error messages, retrieval body text, memory facts, and feedback comments.",
    ]
    return "\n".join(lines)


def _incident_risk_label(events: list[MonitorEvent]) -> str:
    ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    labels = [_enum_value(event.risk_level) for event in events]
    if not labels:
        return "none"
    return sorted(labels, key=lambda label: ranks.get(label, 9))[0]


def _brief_join(values: Any) -> str:
    if isinstance(values, str):
        return values
    items = [str(item) for item in values if item not in (None, "")]
    return ", ".join(items) if items else "none"


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        item = str(_enum_value(value)).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _datetime_age_hours(then: datetime | None, now: datetime) -> float | None:
    if then is None:
        return None
    return round(max(0.0, (now - then).total_seconds() / 3600), 3)


def _monitor_alert_webhook_url(deps: AppContainer) -> str | None:
    if not deps.settings.app_monitor_alert_webhook_enabled:
        return None
    return deps.settings.app_monitor_alert_webhook_url


def _monitor_alert_delivery_summary(deps: AppContainer, limit: int) -> AlertDeliverySummary:
    if not deps.event_store:
        raise HTTPException(status_code=404, detail="Event store is not configured")
    records = deps.event_store.list_alert_delivery_records(
        tenant_id=deps.settings.app_tenant_id,
        limit=limit,
        order="desc",
    )
    return summarize_alert_deliveries(
        records,
        webhook_enabled=bool(_monitor_alert_webhook_url(deps)),
    )


def _monitor_events_for_delivery(
    deps: AppContainer,
    *,
    source: Literal["event_store", "live"],
    limit: int,
) -> tuple[list[MonitorEvent], list[MonitorAlertTriageEvent]]:
    if source == "event_store":
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        return (
            deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=limit,
                order="desc",
            ),
            deps.event_store.list_monitor_alert_triage_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=500,
            ),
        )
    return deps.monitor.events[:limit], []


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
    if failure_type and failure_type not in monitor_failure_labels(event):
        return False
    if needs_human_review is not None and event.needs_human_review != needs_human_review:
        return False
    if grounded is not None and event.grounded != grounded:
        return False
    if policy_compliant is not None and event.policy_compliant != policy_compliant:
        return False
    return include_healthy or monitor_event_alerted(event)


def _monitor_drilldown_stats(
    all_events: list[MonitorEvent],
    matching_events: list[MonitorEvent],
) -> MonitorDrilldownStats:
    timestamps = [event.timestamp for event in matching_events]
    return MonitorDrilldownStats(
        total_events=len(all_events),
        matching_events=len(matching_events),
        alerted_events=sum(1 for event in matching_events if monitor_event_alerted(event)),
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
    "FEEDBACK_UNSAFE",
    "FEEDBACK_POLICY",
    "FEEDBACK_POLICY_VIOLATION",
    "FEEDBACK_PRIVACY",
    "FEEDBACK_PII",
    "FEEDBACK_PROMPT_INJECTION",
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
    feedback: AgentFeedback | None,
    messages: list[Message],
    requested_failure_type: str | None,
) -> RegressionDraftResponse:
    warnings: list[str] = []
    redactions: list[str] = []
    turns, turn_redactions = _regression_turns(run, messages)
    redactions.extend(turn_redactions)
    if not messages:
        warnings.append("No message events were available; review the synthetic turn before committing.")
    failure_labels = _regression_failure_labels(run, monitor_event, feedback, requested_failure_type)
    target_file = _regression_target_file(run, failure_labels)
    expected = _regression_expected(run, monitor_event, target_file)
    if not expected:
        warnings.append("Draft has no strong expected assertions; add intent, route, tool, policy, or answer checks.")
    tool_faults = _regression_tool_faults(run)
    if tool_faults:
        warnings.append("Tool faults are injected before real handlers; review that the failure should be simulated offline.")
    if "PII_IN_OUTPUT" in failure_labels:
        warnings.append("PII_IN_OUTPUT is observed by the online monitor; add answer-level checks before committing.")
    if feedback:
        warnings.append("Feedback-derived draft needs human review of answer-level assertions before committing.")

    scenario_seed = _regression_scenario_seed(run, monitor_event, feedback)
    scenario, scenario_redactions = _redact_eval_text(scenario_seed)
    redactions.extend(scenario_redactions)
    draft: dict[str, Any] = {
        "case_id": _regression_case_id(run, failure_labels),
        "scenario": scenario,
        "locale": "zh-CN",
        "user_id": run.user_id,
        "turns": turns,
        "expected": expected,
        "tags": _regression_tags(run, monitor_event, feedback, failure_labels),
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
            feedback_id=feedback.id if feedback else None,
            feedback_rating=feedback.rating if feedback else None,
            feedback_reasons=feedback.reasons if feedback else [],
            conversation_id=run.conversation_id,
            alert_key=monitor_event.alert_key if monitor_event else None,
        ),
        redactions=sorted(set(redactions)),
        warnings=warnings,
    )


STAGING_AGENT_EVAL_SUITES = tuple(
    (suite.suite_id, suite.path) for suite in STAGING_EVAL_SUITES if suite.runner == "agent"
)
STAGING_MONITOR_EVAL_SUITE = next(
    (suite.suite_id, suite.path) for suite in STAGING_EVAL_SUITES if suite.runner == "monitor"
)
STAGING_RETRIEVAL_EVAL_SUITE = next(
    (suite.suite_id, suite.path) for suite in STAGING_EVAL_SUITES if suite.runner == "retrieval"
)
STAGING_EVAL_GATE_NAME = "staging"


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
    gate_name: str = "golden",
    metadata: dict[str, Any] | None = None,
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
        gate_name=gate_name,
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
        metadata={"report_shape": "case_summary_only", **(metadata or {})},
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
    gate_name: str = "golden",
    runner: Literal["agent", "monitor", "retrieval", "aggregate"] = "agent",
    metadata: dict[str, Any] | None = None,
) -> EvalGateRecord:
    return EvalGateRecord(
        tenant_id=tenant_id,
        gate_name=gate_name,
        runner=runner,
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
        metadata=metadata or {},
    )


def _retrieval_eval_gate_record(
    *,
    report: Any,
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
    gate_name: str,
    metadata: dict[str, Any] | None = None,
) -> EvalGateRecord:
    case_results = [
        EvalGateCaseSummary(
            case_id=result.case_id,
            passed=result.passed,
            score=result.score,
            failures=result.failures,
            observed_intent="retrieval",
            observed_route=None,
            observed_error_codes=[],
            observed_policy_codes=[],
        )
        for result in report.results
    ]
    failed_case_ids = [result.case_id for result in case_results if not result.passed]
    return EvalGateRecord(
        tenant_id=tenant_id,
        gate_name=gate_name,
        runner="retrieval",
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
        metadata={"report_shape": "retrieval_summary", **(metadata or {})},
    )


def _monitor_eval_gate_record(
    *,
    report: Any,
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
    gate_name: str,
    metadata: dict[str, Any] | None = None,
) -> EvalGateRecord:
    case_result = EvalGateCaseSummary(
        case_id=suite_id,
        passed=report.passed,
        score=report.score,
        failures=report.failures,
        observed_intent="monitor",
        observed_route=None,
        observed_error_codes=[],
        observed_policy_codes=[],
    )
    return EvalGateRecord(
        tenant_id=tenant_id,
        gate_name=gate_name,
        runner="monitor",
        suite_id=suite_id,
        suite_path=suite_path,
        environment=environment,
        actor_user_id=actor_user_id,
        trigger=trigger,
        status="passed" if report.passed else "failed",
        total=1,
        passed=1 if report.passed else 0,
        score=report.score,
        failed_case_ids=[] if report.passed else [suite_id],
        case_results=[case_result],
        run_id=run_id,
        alert_key=alert_key,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(started_at, completed_at),
        created_at=completed_at,
        metadata={
            "report_shape": "monitor_summary",
            "summary": report.summary.model_dump(mode="json"),
            **(metadata or {}),
        },
    )


def _aggregate_eval_gate_record(
    *,
    records: list[EvalGateRecord],
    gate_run_id: str,
    tenant_id: str,
    environment: str,
    actor_user_id: str | None,
    trigger: Literal["api", "console"],
    started_at: datetime,
    completed_at: datetime,
    run_id: str | None,
    alert_key: str | None,
) -> EvalGateRecord:
    total = sum(record.total or 0 for record in records)
    passed = sum(record.passed or 0 for record in records)
    failed_case_ids: list[str] = []
    failed_case_results: list[EvalGateCaseSummary] = []
    for record in records:
        for case_id in record.failed_case_ids:
            failed_case_ids.append(f"{record.runner}:{record.suite_id}:{case_id}")
        for result in record.case_results:
            if not result.passed:
                failed_case_results.append(
                    EvalGateCaseSummary(
                        case_id=f"{record.suite_id}:{result.case_id}",
                        passed=False,
                        score=result.score,
                        failures=result.failures or [record.error_message or "Eval case failed."],
                        observed_intent=result.observed_intent,
                        observed_route=result.observed_route,
                        observed_error_codes=result.observed_error_codes,
                        observed_policy_codes=result.observed_policy_codes,
                    )
                )
        if record.status == "error":
            failed_case_ids.append(f"{record.runner}:{record.suite_id}:runner_error")
            failed_case_results.append(
                EvalGateCaseSummary(
                    case_id=f"{record.suite_id}:runner_error",
                    passed=False,
                    score=0,
                    failures=[record.error_message or "Eval runner failed."],
                    observed_intent=record.runner,
                    observed_route=None,
                    observed_error_codes=[],
                    observed_policy_codes=[],
                )
            )
    status: Literal["passed", "failed", "error"] = "passed"
    if any(record.status == "error" for record in records):
        status = "error"
    elif any(record.status == "failed" for record in records):
        status = "failed"
    return EvalGateRecord(
        tenant_id=tenant_id,
        gate_name=STAGING_EVAL_GATE_NAME,
        runner="aggregate",
        suite_id="staging_release_gate",
        suite_path="examples/evals/*",
        environment=environment,
        actor_user_id=actor_user_id,
        trigger=trigger,
        status=status,
        total=total,
        passed=passed,
        score=passed / max(total, 1),
        failed_case_ids=failed_case_ids,
        case_results=failed_case_results,
        run_id=run_id,
        alert_key=alert_key,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(started_at, completed_at),
        created_at=completed_at,
        metadata={
            "gate_run_id": gate_run_id,
            "suite_count": len(records),
            "suite_record_ids": [record.id for record in records],
        },
    )


async def _run_staging_eval_gate(
    *,
    deps: AppContainer,
    actor: RequestActor,
    request_body: RunGoldenEvalRequest,
) -> EvalGateRunResponse:
    from support_agent_lab.evals.monitor_runner import load_suite as load_monitor_suite
    from support_agent_lab.evals.monitor_runner import run_suite as run_monitor_suite
    from support_agent_lab.evals.retrieval_runner import load_cases as load_retrieval_cases
    from support_agent_lab.evals.retrieval_runner import run_cases as run_retrieval_cases
    from support_agent_lab.evals.runner import load_cases as load_agent_cases
    from support_agent_lab.evals.runner import run_cases as run_agent_cases

    assert deps.event_store is not None
    gate_run_id = new_id("evalrun")
    gate_started_at = utc_now()
    records: list[EvalGateRecord] = []
    suite_count = len(STAGING_AGENT_EVAL_SUITES) + 2

    def suite_metadata(index: int) -> dict[str, Any]:
        return {
            "gate_run_id": gate_run_id,
            "suite_index": index,
            "suite_count": suite_count,
        }

    def append_record(record: EvalGateRecord) -> None:
        deps.event_store.append_eval_gate_record(record, tenant_id=deps.settings.app_tenant_id)
        records.append(record)

    for index, (suite_id, suite_path) in enumerate(STAGING_AGENT_EVAL_SUITES, start=1):
        started_at = utc_now()
        try:
            eval_container = create_eval_container(deps.settings)
            report = await run_agent_cases(load_agent_cases(suite_path), eval_container.orchestrator)
            completed_at = utc_now()
            record = _eval_gate_record(
                report=report,
                suite_id=suite_id,
                suite_path=suite_path,
                tenant_id=deps.settings.app_tenant_id,
                environment=deps.settings.app_env,
                actor_user_id=actor.user_id,
                trigger=request_body.trigger,
                started_at=started_at,
                completed_at=completed_at,
                run_id=request_body.run_id,
                alert_key=request_body.alert_key,
                gate_name=STAGING_EVAL_GATE_NAME,
                metadata=suite_metadata(index),
            )
        except Exception as exc:
            completed_at = utc_now()
            record = _eval_gate_error_record(
                suite_id=suite_id,
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
                gate_name=STAGING_EVAL_GATE_NAME,
                runner="agent",
                metadata=suite_metadata(index),
            )
        append_record(record)

    monitor_index = len(STAGING_AGENT_EVAL_SUITES) + 1
    suite_id, suite_path = STAGING_MONITOR_EVAL_SUITE
    started_at = utc_now()
    try:
        eval_container = create_eval_container(deps.settings)
        report = await run_monitor_suite(load_monitor_suite(suite_path), eval_container.orchestrator)
        completed_at = utc_now()
        record = _monitor_eval_gate_record(
            report=report,
            suite_id=suite_id,
            suite_path=suite_path,
            tenant_id=deps.settings.app_tenant_id,
            environment=deps.settings.app_env,
            actor_user_id=actor.user_id,
            trigger=request_body.trigger,
            started_at=started_at,
            completed_at=completed_at,
            run_id=request_body.run_id,
            alert_key=request_body.alert_key,
            gate_name=STAGING_EVAL_GATE_NAME,
            metadata=suite_metadata(monitor_index),
        )
    except Exception as exc:
        completed_at = utc_now()
        record = _eval_gate_error_record(
            suite_id=suite_id,
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
            gate_name=STAGING_EVAL_GATE_NAME,
            runner="monitor",
            metadata=suite_metadata(monitor_index),
        )
    append_record(record)

    retrieval_index = len(STAGING_AGENT_EVAL_SUITES) + 2
    suite_id, suite_path = STAGING_RETRIEVAL_EVAL_SUITE
    started_at = utc_now()
    try:
        eval_container = create_eval_container(deps.settings)
        report = run_retrieval_cases(load_retrieval_cases(suite_path), eval_container.knowledge)
        completed_at = utc_now()
        record = _retrieval_eval_gate_record(
            report=report,
            suite_id=suite_id,
            suite_path=suite_path,
            tenant_id=deps.settings.app_tenant_id,
            environment=deps.settings.app_env,
            actor_user_id=actor.user_id,
            trigger=request_body.trigger,
            started_at=started_at,
            completed_at=completed_at,
            run_id=request_body.run_id,
            alert_key=request_body.alert_key,
            gate_name=STAGING_EVAL_GATE_NAME,
            metadata=suite_metadata(retrieval_index),
        )
    except Exception as exc:
        completed_at = utc_now()
        record = _eval_gate_error_record(
            suite_id=suite_id,
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
            gate_name=STAGING_EVAL_GATE_NAME,
            runner="retrieval",
            metadata=suite_metadata(retrieval_index),
        )
    append_record(record)

    completed_at = utc_now()
    aggregate_record = _aggregate_eval_gate_record(
        records=records,
        gate_run_id=gate_run_id,
        tenant_id=deps.settings.app_tenant_id,
        environment=deps.settings.app_env,
        actor_user_id=actor.user_id,
        trigger=request_body.trigger,
        started_at=gate_started_at,
        completed_at=completed_at,
        run_id=request_body.run_id,
        alert_key=request_body.alert_key,
    )
    suite_records = list(records)
    deps.event_store.append_eval_gate_record(aggregate_record, tenant_id=deps.settings.app_tenant_id)
    return EvalGateRunResponse(
        gate_name=STAGING_EVAL_GATE_NAME,
        gate_run_id=gate_run_id,
        status=aggregate_record.status,
        total=aggregate_record.total or 0,
        passed=aggregate_record.passed or 0,
        score=aggregate_record.score or 0,
        failed_gate_ids=[record.id for record in suite_records if record.status != "passed"],
        records=[aggregate_record, *suite_records],
        run_id=request_body.run_id,
        alert_key=request_body.alert_key,
        started_at=gate_started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(gate_started_at, completed_at),
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


def _regression_scenario_seed(
    run: AgentRunTrace,
    monitor_event: MonitorEvent | None,
    feedback: AgentFeedback | None,
) -> str:
    if feedback:
        reasons = ", ".join(feedback.reasons) or "none"
        comment = feedback.comment or "No feedback comment."
        return (
            f"Regression draft from response feedback {feedback.id} for run {run.id}. "
            f"Rating: {feedback.rating.value}. Reasons: {reasons}. Comment: {comment}"
        )
    return (
        "Regression draft from monitor event "
        f"{monitor_event.id if monitor_event else 'none'} for run {run.id}. "
        f"{monitor_event.summary if monitor_event else 'No monitor event was selected.'}"
    )


def _regression_failure_labels(
    run: AgentRunTrace,
    monitor_event: MonitorEvent | None,
    feedback: AgentFeedback | None,
    requested_failure_type: str | None,
) -> list[str]:
    labels: list[str] = []
    if requested_failure_type:
        labels.append(requested_failure_type)
    if monitor_event:
        labels.extend(monitor_failure_labels(monitor_event))
    if feedback:
        labels.append(f"FEEDBACK_{feedback.rating.value.upper()}")
        labels.extend(f"FEEDBACK_{reason.upper()}" for reason in feedback.reasons)
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
    feedback: AgentFeedback | None,
    failure_labels: list[str],
) -> list[str]:
    values = [
        "regression",
        "draft",
        f"run_{_safe_eval_token(run.id)}",
    ]
    if monitor_event:
        values.append("monitor")
        values.append(f"event_{_safe_eval_token(monitor_event.id)}")
        if monitor_event.alert_key:
            values.append(f"alert_{_safe_eval_token(monitor_event.alert_key)}")
    if feedback:
        values.extend(
            [
                "feedback",
                f"feedback_{_safe_eval_token(feedback.id)}",
                f"feedback_{_safe_eval_token(feedback.rating.value)}",
            ]
        )
        values.extend(f"feedback_reason_{_safe_eval_token(reason)}" for reason in feedback.reasons)
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


def _backup_label(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip(".-_")[:80]


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
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.http_metrics = InMemoryHTTPMetrics()

    @app.middleware("http")
    async def production_request_signature_middleware(request: Request, call_next):
        started = perf_counter()
        settings = get_settings()
        if request_signature_required(settings, request.url.path):
            body = await read_body_and_restore(request)
            try:
                verified = verify_request_signature(settings=settings, request=request, body=body)
                reserve_request_nonce(settings, verified)
            except RequestSignatureError as exc:
                _observe_http_request(app, request, status_code=401, started=started)
                return JSONResponse(status_code=401, content={"detail": str(exc)})
        rate_decision = None
        if should_rate_limit(settings, request.url.path):
            rate_decision = app.state.rate_limiter.check(
                rate_limit_key(settings, request),
                requests_per_minute=settings.app_rate_limit_requests_per_minute,
                burst=settings.app_rate_limit_burst,
            )
            if not rate_decision.allowed:
                app.state.http_metrics.observe_rate_limit(path=request.url.path, decision="blocked")
                _observe_http_request(app, request, status_code=429, started=started)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "retry_after_seconds": rate_decision.retry_after_seconds,
                    },
                    headers={
                        "Retry-After": str(rate_decision.retry_after_seconds),
                        "X-RateLimit-Limit": str(rate_decision.limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
            app.state.http_metrics.observe_rate_limit(path=request.url.path, decision="allowed")
        try:
            response = await call_next(request)
        except Exception:
            _observe_http_request(app, request, status_code=500, started=started)
            raise
        if rate_decision:
            response.headers["X-RateLimit-Limit"] = str(rate_decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(rate_decision.remaining)
        _observe_http_request(app, request, status_code=response.status_code, started=started)
        return response

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

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(
        deps: Annotated[AppContainer, Depends(get_container)],
        source: Annotated[Literal["event_store", "live"], Query()] = "event_store",
        window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    ) -> PlainTextResponse:
        return PlainTextResponse(
            render_prometheus_metrics(
                deps,
                source=source,
                window_hours=window_hours,
                limit=limit,
                http_metrics=app.state.http_metrics,
            ),
            media_type=PROMETHEUS_CONTENT_TYPE,
        )

    @app.get("/api/v1/admin/promotion/gate")
    async def promotion_gate(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[Literal["event_store", "live"], Query()] = "event_store",
        deep: Annotated[bool, Query()] = False,
        window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
        max_active_p0p1_alerts: Annotated[int, Query(ge=0, le=100)] = 0,
        max_active_alerts: Annotated[int, Query(ge=0, le=1000)] = 10,
        max_tool_failure_rate: Annotated[float, Query(ge=0, le=1)] = 0.05,
        max_feedback_negative_rate: Annotated[float, Query(ge=0, le=1)] = 0.4,
        max_eval_age_hours: Annotated[int, Query(ge=1, le=720)] = 24,
        min_tool_calls: Annotated[int, Query(ge=0, le=10000)] = 1,
        min_feedback_count: Annotated[int, Query(ge=0, le=10000)] = 5,
    ) -> PromotionGateResponse:
        require_admin(actor)
        require_scope(actor, "admin:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        require_scope(actor, "eval:read")
        require_scope(actor, "feedback:read")
        return await _promotion_gate_response(
            deps=deps,
            source=source,
            deep=deep,
            window_hours=window_hours,
            max_active_p0p1_alerts=max_active_p0p1_alerts,
            max_active_alerts=max_active_alerts,
            max_tool_failure_rate=max_tool_failure_rate,
            max_feedback_negative_rate=max_feedback_negative_rate,
            max_eval_age_hours=max_eval_age_hours,
            min_tool_calls=min_tool_calls,
            min_feedback_count=min_feedback_count,
        )

    @app.get("/api/v1/admin/operations/automation-plan")
    async def operations_automation_plan(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[Literal["event_store", "live"], Query()] = "event_store",
        deep: Annotated[bool, Query()] = False,
        window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        stale_after_minutes: Annotated[int, Query(ge=1, le=1440)] = 60,
        max_active_p0p1_alerts: Annotated[int, Query(ge=0, le=100)] = 0,
        max_active_alerts: Annotated[int, Query(ge=0, le=1000)] = 10,
        max_tool_failure_rate: Annotated[float, Query(ge=0, le=1)] = 0.05,
        max_feedback_negative_rate: Annotated[float, Query(ge=0, le=1)] = 0.4,
        max_eval_age_hours: Annotated[int, Query(ge=1, le=720)] = 24,
        min_tool_calls: Annotated[int, Query(ge=0, le=10000)] = 1,
        min_feedback_count: Annotated[int, Query(ge=0, le=10000)] = 5,
    ) -> OperationsAutomationPlan:
        require_admin(actor)
        require_scope(actor, "admin:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        require_scope(actor, "events:read")
        require_scope(actor, "eval:read")
        require_scope(actor, "feedback:read")
        return await _operations_automation_plan_response(
            deps=deps,
            actor_user_id=actor.user_id,
            source=source,
            deep=deep,
            window_hours=window_hours,
            limit=limit,
            stale_after_minutes=stale_after_minutes,
            max_active_p0p1_alerts=max_active_p0p1_alerts,
            max_active_alerts=max_active_alerts,
            max_tool_failure_rate=max_tool_failure_rate,
            max_feedback_negative_rate=max_feedback_negative_rate,
            max_eval_age_hours=max_eval_age_hours,
            min_tool_calls=min_tool_calls,
            min_feedback_count=min_feedback_count,
        )

    @app.get("/api/v1/admin/operations/slo-report")
    async def operations_slo_report(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[Literal["event_store", "live"], Query()] = "event_store",
        deep: Annotated[bool, Query()] = False,
        window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
        min_grounded_rate: Annotated[float, Query(ge=0, le=1)] = 0.95,
        min_policy_compliance_rate: Annotated[float, Query(ge=0, le=1)] = 0.99,
        max_human_review_rate: Annotated[float, Query(ge=0, le=1)] = 0.4,
        max_active_p0p1_alerts: Annotated[int, Query(ge=0, le=100)] = 0,
        max_tool_failure_rate: Annotated[float, Query(ge=0, le=1)] = 0.05,
        max_feedback_negative_rate: Annotated[float, Query(ge=0, le=1)] = 0.4,
        max_eval_age_hours: Annotated[int, Query(ge=1, le=720)] = 24,
        max_mtta_seconds: Annotated[int, Query(ge=1, le=86400)] = 900,
        max_alert_delivery_dead_count: Annotated[int, Query(ge=0, le=1000)] = 0,
        min_tool_calls: Annotated[int, Query(ge=0, le=10000)] = 1,
        min_feedback_count: Annotated[int, Query(ge=0, le=10000)] = 5,
    ) -> SloReportResponse:
        require_admin(actor)
        require_scope(actor, "admin:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        require_scope(actor, "eval:read")
        require_scope(actor, "feedback:read")
        return await _slo_report_response(
            deps=deps,
            source=source,
            deep=deep,
            window_hours=window_hours,
            min_grounded_rate=min_grounded_rate,
            min_policy_compliance_rate=min_policy_compliance_rate,
            max_human_review_rate=max_human_review_rate,
            max_active_p0p1_alerts=max_active_p0p1_alerts,
            max_tool_failure_rate=max_tool_failure_rate,
            max_feedback_negative_rate=max_feedback_negative_rate,
            max_eval_age_hours=max_eval_age_hours,
            max_mtta_seconds=max_mtta_seconds,
            max_alert_delivery_dead_count=max_alert_delivery_dead_count,
            min_tool_calls=min_tool_calls,
            min_feedback_count=min_feedback_count,
        )

    @app.get("/api/v1/admin/promotion/decisions")
    def list_promotion_decisions(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
        order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    ) -> list[PromotionDecisionRecord]:
        require_admin(actor)
        require_scope(actor, "admin:read")
        require_scope(actor, "audit:read")
        if not deps.event_store:
            return []
        events = deps.event_store.list_events(
            tenant_id=deps.settings.app_tenant_id,
            event_type=PROMOTION_DECISION_EVENT_TYPE,
            limit=limit,
            order=order,
        )
        return [_promotion_decision_from_event(event) for event in events]

    @app.post("/api/v1/admin/promotion/decisions")
    async def record_promotion_decision(
        body: PromotionDecisionRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> PromotionDecisionRecord:
        require_admin(actor)
        require_scope(actor, "admin:write")
        require_scope(actor, "admin:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        require_scope(actor, "eval:read")
        require_scope(actor, "feedback:read")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        target_version = body.target_version.strip()
        note = body.note.strip()
        if not target_version:
            raise HTTPException(status_code=422, detail="target_version is required")
        if not note:
            raise HTTPException(status_code=422, detail="note is required")
        override_reason = body.override_reason.strip()
        if body.override_blocked and body.decision != "approved":
            raise HTTPException(status_code=422, detail="override_blocked only applies to approved decisions")
        if body.override_blocked and not override_reason:
            raise HTTPException(status_code=422, detail="override_reason is required when overriding a blocked gate")
        gate = await _promotion_gate_response(
            deps=deps,
            source=body.source,
            deep=body.deep,
            window_hours=body.window_hours,
            max_active_p0p1_alerts=body.max_active_p0p1_alerts,
            max_active_alerts=body.max_active_alerts,
            max_tool_failure_rate=body.max_tool_failure_rate,
            max_feedback_negative_rate=body.max_feedback_negative_rate,
            max_eval_age_hours=body.max_eval_age_hours,
            min_tool_calls=body.min_tool_calls,
            min_feedback_count=body.min_feedback_count,
        )
        if body.decision == "approved" and gate.status == "blocked" and not body.override_blocked:
            raise HTTPException(
                status_code=409,
                detail="Cannot approve while promotion gate is blocked without override_blocked=true",
            )
        record = PromotionDecisionRecord(
            tenant_id=deps.settings.app_tenant_id,
            environment=gate.environment,
            target_version=target_version,
            decision=body.decision,
            gate_status=gate.status,
            gate=gate,
            note=note,
            override_blocked=body.override_blocked,
            override_reason=override_reason,
            actor_user_id=actor.user_id,
        )
        deps.event_store.append(
            tenant_id=deps.settings.app_tenant_id,
            user_id=actor.user_id,
            event_type=PROMOTION_DECISION_EVENT_TYPE,
            payload=record.model_dump(mode="json"),
        )
        return record

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
                actor_roles=actor.roles,
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

    @app.post("/api/v1/agent/runs/{run_id}/feedback")
    def submit_agent_feedback(
        run_id: str,
        body: AgentFeedbackRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> AgentFeedback:
        require_scope(actor, "feedback:write")
        if not deps.event_store:
            raise HTTPException(status_code=503, detail="Event store is required for response feedback")
        run = deps.orchestrator.runs.get(run_id)
        if run is None:
            run = deps.event_store.get_agent_run_trace(
                run_id,
                tenant_id=deps.settings.app_tenant_id,
            )
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if body.source != "user":
            require_admin(actor)
        if run.user_id != actor.user_id:
            require_admin(actor)
            if body.source == "user":
                raise HTTPException(
                    status_code=403,
                    detail="Cross-user feedback must use operator or qa source",
                )
        feedback = AgentFeedback(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            user_id=actor.user_id,
            rating=body.rating,
            reasons=_normalize_feedback_reasons(body.reasons),
            comment=body.comment.strip(),
            source=body.source,
        )
        deps.event_store.append_agent_feedback(feedback)
        return feedback

    @app.get("/api/v1/admin/feedback")
    def list_agent_feedback(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        conversation_id: Annotated[str | None, Query()] = None,
        run_id: Annotated[str | None, Query()] = None,
        user_id: Annotated[str | None, Query()] = None,
        rating: Annotated[FeedbackRating | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    ) -> list[AgentFeedback]:
        require_admin(actor)
        require_scope(actor, "feedback:read")
        if not deps.event_store:
            return []
        return deps.event_store.list_agent_feedback(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating.value if rating else None,
            created_after=created_after.isoformat() if created_after else None,
            created_before=created_before.isoformat() if created_before else None,
            limit=limit,
            order=order,
        )

    @app.get("/api/v1/admin/feedback/summary")
    def summarize_agent_feedback(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        conversation_id: Annotated[str | None, Query()] = None,
        run_id: Annotated[str | None, Query()] = None,
        user_id: Annotated[str | None, Query()] = None,
        rating: Annotated[FeedbackRating | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
    ) -> FeedbackSummary:
        require_admin(actor)
        require_scope(actor, "feedback:read")
        if not deps.event_store:
            return FeedbackSummary()
        return deps.event_store.summarize_agent_feedback(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            rating=rating.value if rating else None,
            created_after=created_after.isoformat() if created_after else None,
            created_before=created_before.isoformat() if created_before else None,
        )

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
        context = RetrievalContext(
            tenant_id=deps.settings.app_tenant_id,
            actor_user_id=actor.user_id,
            actor_roles=actor.roles,
            actor_scopes=actor.scopes,
            request_id=new_id("req"),
            trace_id=new_id("kbdiag"),
        )
        search_result = call_knowledge_search(
            deps.knowledge,
            body.query,
            limit=body.limit,
            context=context,
        )
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

        return _load_incident_run_bundle(
            deps=deps,
            run_id=run_id,
            include_memory=include_memory,
            limit=limit,
        )

    @app.get("/api/v1/admin/incidents/runs/{run_id}/brief")
    def incident_run_brief(
        run_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        include_memory: Annotated[bool, Query()] = True,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> IncidentBriefResponse:
        require_admin(actor)
        require_scope(actor, "events:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        if include_memory:
            require_scope(actor, "memory:replay")
        bundle = _load_incident_run_bundle(
            deps=deps,
            run_id=run_id,
            include_memory=include_memory,
            limit=limit,
        )
        return _incident_brief_response(bundle)

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
        return monitor_triage_metrics_response(
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

    @app.get("/api/v1/admin/monitor/alert-deliveries/summary")
    def monitor_alert_delivery_summary(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> AlertDeliverySummary:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        return _monitor_alert_delivery_summary(deps, limit=limit)

    @app.get("/api/v1/admin/monitor/alert-deliveries")
    def list_monitor_alert_deliveries(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        alert_key: Annotated[str | None, Query(max_length=256)] = None,
        status: Annotated[Literal["pending", "in_progress", "sent", "failed", "dead", "closed"] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    ) -> list[AlertDeliveryRecord]:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        return deps.event_store.list_alert_delivery_records(
            tenant_id=deps.settings.app_tenant_id,
            alert_key=alert_key,
            statuses=[status] if status else None,
            limit=limit,
            order=order,
        )

    @app.post("/api/v1/admin/monitor/alert-deliveries/{delivery_id}/requeue")
    def requeue_monitor_alert_delivery(
        delivery_id: str,
        body: AlertDeliveryOperatorActionRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> AlertDeliveryRecord:
        require_admin(actor)
        require_scope(actor, "monitor:write")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        try:
            return deps.event_store.requeue_alert_delivery(
                delivery_id,
                tenant_id=deps.settings.app_tenant_id,
                actor_user_id=actor.user_id,
                note=body.note.strip(),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Alert delivery not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/admin/monitor/alert-deliveries/{delivery_id}/close")
    def close_monitor_alert_delivery(
        delivery_id: str,
        body: AlertDeliveryOperatorActionRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> AlertDeliveryRecord:
        require_admin(actor)
        require_scope(actor, "monitor:write")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        try:
            return deps.event_store.close_alert_delivery(
                delivery_id,
                tenant_id=deps.settings.app_tenant_id,
                actor_user_id=actor.user_id,
                note=body.note.strip(),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Alert delivery not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/admin/monitor/alert-deliveries/dispatch")
    def dispatch_monitor_alert_deliveries(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[Literal["event_store", "live"], Query()] = "event_store",
        monitor_limit: Annotated[int, Query(ge=1, le=500)] = 500,
        dispatch_limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> AlertDispatchReport:
        require_admin(actor)
        require_scope(actor, "monitor:write")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        events, triage_events = _monitor_events_for_delivery(
            deps,
            source=source,
            limit=monitor_limit,
        )
        summary = summarize_monitor_events(events, triage_events=triage_events)
        webhook_url = _monitor_alert_webhook_url(deps)
        if not webhook_url:
            return AlertDispatchReport(
                webhook_enabled=False,
                skipped_count=len(summary.alerts),
            )
        enqueue_report = enqueue_alert_deliveries(
            event_store=deps.event_store,
            tenant_id=deps.settings.app_tenant_id,
            alerts=summary.alerts,
            webhook_url=webhook_url,
            min_severity=deps.settings.app_monitor_alert_min_severity,
        )
        dispatch_report = dispatch_alert_deliveries(
            event_store=deps.event_store,
            tenant_id=deps.settings.app_tenant_id,
            webhook_url=webhook_url,
            webhook_secret=deps.settings.app_monitor_alert_webhook_secret,
            max_attempts=deps.settings.app_monitor_alert_max_attempts,
            limit=dispatch_limit,
            timeout_ms=deps.settings.app_monitor_alert_webhook_timeout_ms,
            backoff_base_seconds=deps.settings.app_monitor_alert_backoff_base_seconds,
            backoff_max_seconds=deps.settings.app_monitor_alert_backoff_max_seconds,
            claim_lease_seconds=deps.settings.app_monitor_alert_claim_lease_seconds,
        )
        return AlertDispatchReport(
            webhook_enabled=True,
            enqueued_count=enqueue_report.enqueued_count,
            existing_count=enqueue_report.existing_count,
            skipped_count=enqueue_report.skipped_count,
            claimed_count=dispatch_report.claimed_count,
            attempted_count=dispatch_report.attempted_count,
            sent_count=dispatch_report.sent_count,
            failed_count=dispatch_report.failed_count,
            dead_count=dispatch_report.dead_count,
            deliveries=[*enqueue_report.deliveries, *dispatch_report.deliveries],
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
        if body.feedback_id:
            require_scope(actor, "feedback:read")

        feedback = None
        if body.feedback_id:
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            feedback = deps.event_store.get_agent_feedback(
                body.feedback_id,
                tenant_id=deps.settings.app_tenant_id,
            )
            if feedback is None:
                raise HTTPException(status_code=404, detail="Feedback not found")
            if feedback.run_id != body.run_id:
                raise HTTPException(status_code=400, detail="Feedback does not belong to run")

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
            feedback=feedback,
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

    @app.post("/api/v1/admin/evals/staging")
    async def run_staging_eval_gate(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        body: RunGoldenEvalRequest | None = None,
    ) -> EvalGateRunResponse:
        require_admin(actor)
        require_scope(actor, "eval:run")
        if deps.settings.is_production:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The bundled staging eval gate uses lab fixtures and is disabled in production. "
                    "Run offline evals in CI or a staging sandbox instead."
                ),
            )
        if not deps.event_store:
            raise HTTPException(status_code=503, detail="Event store is required for eval gate audit records")
        return await _run_staging_eval_gate(
            deps=deps,
            actor=actor,
            request_body=body or RunGoldenEvalRequest(),
        )

    @app.get("/api/v1/admin/evals/gates")
    def list_eval_gate_records(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        run_id: Annotated[str | None, Query(max_length=128)] = None,
        alert_key: Annotated[str | None, Query(max_length=256)] = None,
        gate_name: Annotated[str | None, Query(max_length=80)] = None,
        runner: Annotated[Literal["agent", "monitor", "retrieval", "aggregate"] | None, Query()] = None,
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

    @app.get("/api/v1/admin/audit/export")
    def export_audit_records(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        event_type: Annotated[str | None, Query()] = None,
        created_after: Annotated[str | None, Query()] = None,
        created_before: Annotated[str | None, Query()] = None,
        include_events: Annotated[bool, Query()] = True,
        include_tool_audit: Annotated[bool, Query()] = True,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    ) -> PlainTextResponse:
        require_admin(actor)
        require_scope(actor, "audit:read")
        require_scope(actor, "events:read")
        if not include_events and not include_tool_audit:
            raise HTTPException(status_code=422, detail="At least one audit source must be included")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        rows = _audit_export_rows(
            deps=deps,
            include_events=include_events,
            include_tool_audit=include_tool_audit,
            event_type=event_type,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            order=order,
        )
        return PlainTextResponse(
            _ndjson(rows),
            media_type=AUDIT_EXPORT_MEDIA_TYPE,
            headers={
                "Content-Disposition": "attachment; filename=support-agent-audit-export.ndjson",
                "X-Audit-Export-Records": str(len(rows)),
            },
        )

    @app.post("/api/v1/admin/event-store/backups")
    def create_event_store_backup(
        body: EventStoreBackupRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> SQLiteBackupReport:
        require_admin(actor)
        require_scope(actor, "admin:write")
        require_scope(actor, "audit:read")
        require_scope(actor, "events:read")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        backup_dir = Path(deps.settings.app_event_store_backup_dir)
        timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        label = _backup_label(body.label)
        tenant_label = _backup_label(deps.settings.app_tenant_id) or "tenant"
        suffix = f"-{label}" if label else ""
        target_path = backup_dir / f"support-agent-lab-{tenant_label}-{timestamp}{suffix}.db"
        try:
            return deps.event_store.backup_to(
                target_path,
                overwrite=body.overwrite,
                verify=body.verify,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/admin/event-store/retention")
    def apply_event_store_retention(
        body: EventStoreRetentionRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> EventStoreRetentionReport:
        require_admin(actor)
        require_scope(actor, "admin:write")
        require_scope(actor, "audit:read")
        require_scope(actor, "events:read")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        return deps.event_store.apply_retention_policy(
            tenant_id=deps.settings.app_tenant_id,
            dry_run=body.dry_run,
            include_events=body.include_events,
            vacuum=body.vacuum,
            event_retention_days=body.event_retention_days or deps.settings.app_event_retention_days,
            tool_audit_retention_days=body.tool_audit_retention_days or deps.settings.app_tool_audit_retention_days,
            idempotency_retention_days=body.idempotency_retention_days or deps.settings.app_idempotency_retention_days,
            alert_delivery_retention_days=body.alert_delivery_retention_days
            or deps.settings.app_alert_delivery_retention_days,
        )

    @app.get("/api/v1/admin/conversations/{conversation_id}/memory/replay")
    def replay_memory(
        conversation_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=0, le=20000)] = 0,
    ) -> MemoryReplayResult:
        require_admin(actor)
        require_scope(actor, "memory:replay")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        events = deps.event_store.list_conversation_memory_events(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            limit=limit or None,
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

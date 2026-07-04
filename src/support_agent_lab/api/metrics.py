from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal

from support_agent_lab.api.rate_limit import route_family
from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex
from support_agent_lab.models import (
    AlertDeliveryStatus,
    MonitorAlertStatus,
    MonitorAlertTriageEvent,
    MonitorEvent,
    ToolStatus,
    utc_now,
)
from support_agent_lab.monitoring.monitor import summarize_monitor_events
from support_agent_lab.monitoring.triage import (
    MONITOR_ALERT_SEVERITIES,
    MONITOR_TRIAGE_HEALTH_STATUSES,
    MonitorTriageMetricsResponse,
    monitor_triage_metrics_response,
)
from support_agent_lab.tools.http_business_tools import HTTPBusinessClient
from support_agent_lab.tools.registry import ToolAuditSummary, ToolAuditToolSummary


PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
ALERT_DELIVERY_HEALTH_STATUSES = ("ok", "queued", "degraded", "failed", "disabled", "unknown")
ALERT_DELIVERY_SEVERITIES = ("P0", "P1", "P2", "P3")


@dataclass(frozen=True)
class HTTPMetricsSnapshot:
    request_counts: dict[tuple[str, str, str], int]
    request_duration_ms: dict[tuple[str, str, str], float]
    rate_limit_counts: dict[tuple[str, str], int]


@dataclass
class InMemoryHTTPMetrics:
    """Low-cardinality process-local HTTP counters for single-instance scrapes."""

    _request_counts: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    _request_duration_ms: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    _rate_limit_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    _lock: Lock = field(default_factory=Lock)

    def observe_request(self, *, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = (method.upper(), route_family(path), str(status_code))
        with self._lock:
            self._request_counts[key] += 1
            self._request_duration_ms[key] += max(0.0, duration_ms)

    def observe_rate_limit(self, *, path: str, decision: Literal["allowed", "blocked"]) -> None:
        key = (route_family(path), decision)
        with self._lock:
            self._rate_limit_counts[key] += 1

    def snapshot(self) -> HTTPMetricsSnapshot:
        with self._lock:
            return HTTPMetricsSnapshot(
                request_counts=dict(self._request_counts),
                request_duration_ms=dict(self._request_duration_ms),
                rate_limit_counts=dict(self._rate_limit_counts),
            )

    def reset(self) -> None:
        with self._lock:
            self._request_counts.clear()
            self._request_duration_ms.clear()
            self._rate_limit_counts.clear()


def render_prometheus_metrics(
    deps: AppContainer,
    *,
    source: Literal["event_store", "live"] = "event_store",
    window_hours: int = 24,
    limit: int = 1000,
    http_metrics: InMemoryHTTPMetrics | None = None,
) -> str:
    now = utc_now()
    created_after = now - timedelta(hours=window_hours)
    monitor_events, triage_events, resolved_source = _load_monitor_window(
        deps,
        source=source,
        created_after=created_after,
        limit=limit,
    )
    monitor_summary = summarize_monitor_events(monitor_events, triage_events=triage_events)
    triage_metrics = monitor_triage_metrics_response(
        source=resolved_source,
        events=monitor_events,
        triage_events=triage_events,
        conversation_id=None,
        created_after=created_after,
        created_before=None,
        limit=limit,
        order="desc",
        stale_after=timedelta(minutes=60),
    )
    tool_summary = _load_tool_audit_summary(deps, created_after=created_after)

    metrics = _MetricWriter()
    info_labels = {
        "environment": deps.settings.app_env,
        "tenant": deps.settings.app_tenant_id,
        "model_provider": deps.settings.app_model_provider,
        "llm_model": getattr(deps.llm.provider, "model", "unknown"),
        "business_backend": "http" if isinstance(deps.business_client, HTTPBusinessClient) else "local",
        "knowledge_backend": "http" if isinstance(deps.knowledge, HTTPKnowledgeIndex) else "local",
    }
    metrics.add("support_agent_info", 1, info_labels, metric_type="gauge", help_text="Static support agent deployment metadata.")
    metrics.add(
        "support_agent_metrics_window_hours",
        window_hours,
        {"source": resolved_source},
        metric_type="gauge",
        help_text="Lookback window used to derive scrape-time aggregate metrics.",
    )

    metrics.add(
        "support_agent_rate_limit_enabled",
        _bool(deps.settings.rate_limit_enabled),
        metric_type="gauge",
        help_text="Whether ingress rate limiting is enabled.",
    )
    metrics.add(
        "support_agent_rate_limit_requests_per_minute",
        deps.settings.app_rate_limit_requests_per_minute,
        metric_type="gauge",
        help_text="Configured ingress rate-limit refill rate.",
    )
    metrics.add(
        "support_agent_rate_limit_burst",
        deps.settings.app_rate_limit_burst,
        metric_type="gauge",
        help_text="Configured ingress rate-limit burst size.",
    )

    _add_http_metrics(metrics, http_metrics)
    _add_alert_delivery_metrics(metrics, deps, now=now)
    _add_monitor_triage_metrics(metrics, triage_metrics, now=now)
    _add_monitor_metrics(metrics, monitor_summary, monitor_events)
    _add_tool_metrics(metrics, tool_summary)
    _add_circuit_metrics(metrics, deps)
    _add_llm_metrics(metrics, deps, source=resolved_source, created_after=created_after, limit=limit)
    return metrics.render()


def _add_http_metrics(metrics: "_MetricWriter", http_metrics: InMemoryHTTPMetrics | None) -> None:
    if not http_metrics:
        return
    snapshot = http_metrics.snapshot()
    for (method, family, status), count in sorted(snapshot.request_counts.items()):
        labels = {"method": method, "route_family": family, "status": status}
        metrics.add(
            "support_agent_http_requests_total",
            count,
            labels,
            metric_type="counter",
            help_text="HTTP requests observed by this process since startup.",
        )
        metrics.add(
            "support_agent_http_request_duration_ms_sum",
            snapshot.request_duration_ms[(method, family, status)],
            labels,
            metric_type="counter",
            help_text="Total HTTP request duration observed by this process since startup.",
        )
        metrics.add(
            "support_agent_http_request_duration_ms_count",
            count,
            labels,
            metric_type="counter",
            help_text="HTTP request duration sample count observed by this process since startup.",
        )
    for (family, decision), count in sorted(snapshot.rate_limit_counts.items()):
        metrics.add(
            "support_agent_rate_limit_decisions_total",
            count,
            {"route_family": family, "decision": decision},
            metric_type="counter",
            help_text="Ingress rate-limit decisions observed by this process since startup.",
        )


def _add_alert_delivery_metrics(metrics: "_MetricWriter", deps: AppContainer, *, now: datetime) -> None:
    webhook_enabled = bool(
        deps.settings.app_monitor_alert_webhook_enabled and deps.settings.app_monitor_alert_webhook_url
    )
    metrics.add(
        "support_agent_alert_delivery_webhook_enabled",
        _bool(webhook_enabled),
        metric_type="gauge",
        help_text="Whether proactive monitor alert webhook delivery is enabled.",
    )
    metrics.add(
        "support_agent_alert_delivery_outbox_configured",
        _bool(bool(deps.event_store)),
        metric_type="gauge",
        help_text="Whether a durable alert delivery outbox is configured.",
    )
    if not deps.event_store:
        _add_alert_delivery_health_metric(
            metrics,
            "unknown" if webhook_enabled else "disabled",
        )
        return

    summary = deps.event_store.summarize_alert_delivery_records(tenant_id=deps.settings.app_tenant_id)
    status_counts = {status.value: 0 for status in AlertDeliveryStatus}
    status_counts.update(summary.counts_by_status)
    for status in [status.value for status in AlertDeliveryStatus]:
        metrics.add(
            "support_agent_alert_delivery_records",
            status_counts[status],
            {"status": status},
            metric_type="gauge",
            help_text="Alert delivery outbox rows by durable delivery status.",
        )
    for severity in ALERT_DELIVERY_SEVERITIES:
        metrics.add(
            "support_agent_alert_delivery_records_by_severity",
            summary.counts_by_severity.get(severity, 0),
            {"severity": severity},
            metric_type="gauge",
            help_text="Alert delivery outbox rows by bounded monitor alert severity.",
        )
    metrics.add(
        "support_agent_alert_delivery_due_records",
        summary.due_count,
        metric_type="gauge",
        help_text="Pending or failed alert delivery rows due for dispatch now.",
    )
    metrics.add(
        "support_agent_alert_delivery_attempts_recorded",
        summary.attempt_count_total,
        metric_type="gauge",
        help_text="Total webhook attempts recorded on current alert delivery rows.",
    )
    _add_alert_delivery_health_metric(
        metrics,
        _alert_delivery_health_status(webhook_enabled=webhook_enabled, status_counts=status_counts),
    )
    if summary.oldest_actionable_at:
        metrics.add(
            "support_agent_alert_delivery_oldest_actionable_age_seconds",
            _age_seconds(summary.oldest_actionable_at, now),
            metric_type="gauge",
            help_text="Age of the oldest pending, in-progress, or retryable failed alert delivery row.",
        )
    _add_optional_timestamp(
        metrics,
        "support_agent_alert_delivery_next_attempt_timestamp_seconds",
        summary.next_attempt_at,
    )
    _add_optional_timestamp(
        metrics,
        "support_agent_alert_delivery_last_attempt_timestamp_seconds",
        summary.last_attempt_at,
    )
    _add_optional_timestamp(
        metrics,
        "support_agent_alert_delivery_last_success_timestamp_seconds",
        summary.last_success_at,
    )
    _add_optional_timestamp(
        metrics,
        "support_agent_alert_delivery_last_dead_letter_timestamp_seconds",
        summary.last_dead_lettered_at,
    )


def _add_alert_delivery_health_metric(metrics: "_MetricWriter", current_status: str) -> None:
    for status in ALERT_DELIVERY_HEALTH_STATUSES:
        metrics.add(
            "support_agent_alert_delivery_health_status",
            _bool(status == current_status),
            {"status": status},
            metric_type="gauge",
            help_text="Current alert delivery outbox health status as a one-hot gauge.",
        )


def _alert_delivery_health_status(*, webhook_enabled: bool, status_counts: dict[str, int]) -> str:
    if not webhook_enabled:
        return "disabled"
    if status_counts.get(AlertDeliveryStatus.failed.value, 0) or status_counts.get(AlertDeliveryStatus.dead.value, 0):
        return "failed"
    queued_count = status_counts.get(AlertDeliveryStatus.pending.value, 0) + status_counts.get(
        AlertDeliveryStatus.in_progress.value,
        0,
    )
    if queued_count >= 10:
        return "degraded"
    if queued_count:
        return "queued"
    return "ok"


def _add_monitor_triage_metrics(
    metrics: "_MetricWriter",
    triage: MonitorTriageMetricsResponse,
    *,
    now: datetime,
) -> None:
    metrics.add(
        "support_agent_monitor_triage_active_alerts",
        triage.active_alert_count,
        metric_type="gauge",
        help_text="Active monitor alerts including open, acknowledged, and investigating statuses.",
    )
    metrics.add(
        "support_agent_monitor_triage_unassigned_active_alerts",
        triage.unassigned_active_alert_count,
        metric_type="gauge",
        help_text="Active monitor alerts without an assignee.",
    )
    metrics.add(
        "support_agent_monitor_triage_untriaged_alerts",
        triage.untriaged_alert_count,
        metric_type="gauge",
        help_text="Monitor alerts that have no triage event.",
    )
    metrics.add(
        "support_agent_monitor_triage_new_events_since_triage",
        triage.new_events_since_triage_count,
        metric_type="gauge",
        help_text="Monitor alerts with newer events after the latest triage action.",
    )
    metrics.add(
        "support_agent_monitor_triage_stale_active_alerts",
        triage.stale_active_alert_count,
        metric_type="gauge",
        help_text="Active monitor alerts older than the configured stale threshold.",
    )
    metrics.add(
        "support_agent_monitor_triage_stale_threshold_seconds",
        triage.stale_threshold_seconds,
        metric_type="gauge",
        help_text="Age threshold used to classify active monitor alerts as stale.",
    )
    metrics.add(
        "support_agent_monitor_triage_alert_rate",
        triage.alert_rate,
        metric_type="gauge",
        help_text="Share of monitor events that produced alert pressure in the scrape window.",
    )
    for status in MONITOR_TRIAGE_HEALTH_STATUSES:
        metrics.add(
            "support_agent_monitor_triage_health_status",
            _bool(triage.health_status == status),
            {"status": status},
            metric_type="gauge",
            help_text="Current monitor triage health as a one-hot gauge.",
        )
    for status in [status.value for status in MonitorAlertStatus]:
        metrics.add(
            "support_agent_monitor_triage_alerts_by_status",
            triage.by_status.get(status, 0),
            {"status": status},
            metric_type="gauge",
            help_text="Monitor alerts by triage status in the scrape window.",
        )
    for severity in MONITOR_ALERT_SEVERITIES:
        labels = {"severity": severity}
        metrics.add(
            "support_agent_monitor_triage_alerts_by_severity",
            triage.by_severity.get(severity, 0),
            labels,
            metric_type="gauge",
            help_text="Monitor alerts by bounded severity in the scrape window.",
        )
        metrics.add(
            "support_agent_monitor_triage_active_alerts_by_severity",
            triage.active_by_severity.get(severity, 0),
            labels,
            metric_type="gauge",
            help_text="Active monitor alerts by bounded severity in the scrape window.",
        )
    if triage.mtta_seconds is not None:
        metrics.add(
            "support_agent_monitor_triage_mtta_seconds",
            triage.mtta_seconds,
            metric_type="gauge",
            help_text="Average seconds from alert first seen to first triage action.",
        )
    if triage.mttr_seconds is not None:
        metrics.add(
            "support_agent_monitor_triage_mttr_seconds",
            triage.mttr_seconds,
            metric_type="gauge",
            help_text="Average seconds from alert first seen to resolved or silenced triage action.",
        )
    if triage.oldest_active_alert_at:
        metrics.add(
            "support_agent_monitor_triage_oldest_active_alert_age_seconds",
            _age_seconds(triage.oldest_active_alert_at, now),
            metric_type="gauge",
            help_text="Age of the oldest active monitor alert.",
        )
    _add_optional_timestamp(
        metrics,
        "support_agent_monitor_triage_latest_action_timestamp_seconds",
        triage.latest_triage_at,
    )


def _load_monitor_window(
    deps: AppContainer,
    *,
    source: Literal["event_store", "live"],
    created_after,
    limit: int,
) -> tuple[list[MonitorEvent], list[MonitorAlertTriageEvent], Literal["event_store", "live"]]:
    if source == "event_store" and deps.event_store:
        return (
            deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                created_after=created_after.isoformat(),
                limit=limit,
                order="desc",
            ),
            deps.event_store.list_monitor_alert_triage_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=limit,
            ),
            "event_store",
        )
    events = [event for event in deps.monitor.events if event.timestamp >= created_after]
    events = sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]
    return events, [], "live"


def _load_tool_audit_summary(deps: AppContainer, *, created_after) -> ToolAuditSummary:
    if deps.event_store:
        return deps.event_store.summarize_tool_audit_records(
            tenant_id=deps.settings.app_tenant_id,
            created_after=created_after.isoformat(),
        )
    records = [record for record in deps.tools.audit_log if _record_in_window(record.created_at, created_after)]
    total_calls = len(records)
    failed_calls = sum(1 for record in records if record.status == ToolStatus.failed)
    replayed_calls = sum(1 for record in records if record.replayed)
    latencies = [record.latency_ms for record in records]
    tools: list[ToolAuditToolSummary] = []
    for tool_name in sorted({record.tool_name for record in records}):
        tool_records = [record for record in records if record.tool_name == tool_name]
        tool_failed = sum(1 for record in tool_records if record.status == ToolStatus.failed)
        tool_latencies = [record.latency_ms for record in tool_records]
        error_codes = [record.error_code for record in tool_records if record.error_code]
        tools.append(
            ToolAuditToolSummary(
                tool_name=tool_name,
                total_calls=len(tool_records),
                failed_calls=tool_failed,
                replayed_calls=sum(1 for record in tool_records if record.replayed),
                failure_rate=_rate(tool_failed, len(tool_records)),
                average_latency_ms=_avg(tool_latencies),
                max_latency_ms=max(tool_latencies, default=None),
                top_error_code=error_codes[0] if error_codes else None,
                last_seen_at=max((record.created_at for record in tool_records if record.created_at), default=None),
            )
        )
    return ToolAuditSummary(
        total_calls=total_calls,
        failed_calls=failed_calls,
        replayed_calls=replayed_calls,
        failure_rate=_rate(failed_calls, total_calls),
        average_latency_ms=_avg(latencies),
        max_latency_ms=max(latencies, default=None),
        window_start=min((record.created_at for record in records if record.created_at), default=None),
        window_end=max((record.created_at for record in records if record.created_at), default=None),
        top_error_codes=[],
        tools=tools,
    )


def _record_in_window(created_at: str | None, created_after) -> bool:
    if not created_at:
        return True
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= created_after


def _add_monitor_metrics(
    metrics: "_MetricWriter",
    summary,
    events: list[MonitorEvent],
) -> None:
    metrics.add(
        "support_agent_monitor_events_window",
        summary.total_events,
        metric_type="gauge",
        help_text="Monitor events in the scrape window.",
    )
    metrics.add(
        "support_agent_monitor_grounded_rate",
        summary.grounded_rate,
        metric_type="gauge",
        help_text="Share of monitor events with grounded answers in the scrape window.",
    )
    metrics.add(
        "support_agent_monitor_policy_compliance_rate",
        summary.policy_compliance_rate,
        metric_type="gauge",
        help_text="Share of monitor events that passed output policy checks in the scrape window.",
    )
    metrics.add(
        "support_agent_monitor_human_review_rate",
        summary.human_review_rate,
        metric_type="gauge",
        help_text="Share of monitor events requiring human review in the scrape window.",
    )
    metrics.add(
        "support_agent_monitor_active_alerts",
        sum(1 for alert in summary.alerts if alert.status == MonitorAlertStatus.open),
        metric_type="gauge",
        help_text="Open monitor alerts derived from the scrape window.",
    )
    for key, count in sorted(summary.by_risk_level.items()):
        metrics.add("support_agent_monitor_events_by_risk_window", count, {"risk_level": key})
    for key, count in sorted(summary.by_intent.items()):
        metrics.add("support_agent_monitor_events_by_intent_window", count, {"intent": key})
    for key, count in sorted(summary.by_failure_type.items()):
        metrics.add("support_agent_monitor_events_by_failure_window", count, {"failure_type": key})
    fallback_events = sum(1 for event in events if "LLM_FALLBACK" in event.failure_types)
    metrics.add(
        "support_agent_llm_fallback_monitor_events_window",
        fallback_events,
        metric_type="gauge",
        help_text="Monitor events in the scrape window that included LLM_FALLBACK.",
    )


def _add_tool_metrics(metrics: "_MetricWriter", summary: ToolAuditSummary) -> None:
    metrics.add(
        "support_agent_tool_calls_window",
        summary.total_calls,
        metric_type="gauge",
        help_text="Tool calls recorded in the scrape window.",
    )
    metrics.add(
        "support_agent_tool_failed_calls_window",
        summary.failed_calls,
        metric_type="gauge",
        help_text="Failed tool calls recorded in the scrape window.",
    )
    metrics.add(
        "support_agent_tool_replayed_calls_window",
        summary.replayed_calls,
        metric_type="gauge",
        help_text="Replayed idempotent write tool results in the scrape window.",
    )
    metrics.add(
        "support_agent_tool_failure_rate",
        summary.failure_rate,
        metric_type="gauge",
        help_text="Tool failure rate in the scrape window.",
    )
    if summary.average_latency_ms is not None:
        metrics.add("support_agent_tool_latency_average_ms", summary.average_latency_ms, metric_type="gauge")
    if summary.max_latency_ms is not None:
        metrics.add("support_agent_tool_latency_max_ms", summary.max_latency_ms, metric_type="gauge")
    for tool in summary.tools:
        labels = {"tool_name": tool.tool_name}
        metrics.add("support_agent_tool_calls_by_tool_window", tool.total_calls, labels)
        metrics.add("support_agent_tool_failed_calls_by_tool_window", tool.failed_calls, labels)
        metrics.add("support_agent_tool_failure_rate_by_tool", tool.failure_rate, labels, metric_type="gauge")


def _add_circuit_metrics(metrics: "_MetricWriter", deps: AppContainer) -> None:
    _add_adapter_circuit_metrics(metrics, "business", deps.business_client)
    _add_adapter_circuit_metrics(metrics, "knowledge", deps.knowledge)
    status = deps.llm.circuit_status()
    labels = {"provider": deps.llm.provider.provider, "model": deps.llm.provider.model}
    metrics.add("support_agent_llm_circuit_open", _bool(status["state"] == "open"), labels, metric_type="gauge")
    metrics.add("support_agent_llm_circuit_half_open", _bool(status["state"] == "half_open"), labels, metric_type="gauge")
    metrics.add("support_agent_llm_circuit_failures", int(status["failure_count"]), labels, metric_type="gauge")
    metrics.add(
        "support_agent_llm_circuit_failure_threshold",
        int(status["failure_threshold"]),
        labels,
        metric_type="gauge",
    )
    metrics.add("support_agent_llm_retry_attempts", int(status["retry_attempts"]), labels, metric_type="gauge")


def _add_adapter_circuit_metrics(metrics: "_MetricWriter", adapter: str, target: object) -> None:
    if not hasattr(target, "circuit_status"):
        metrics.add("support_agent_adapter_circuit_open", 0, {"adapter": adapter}, metric_type="gauge")
        return
    status = target.circuit_status()
    state = str(status["state"])
    labels = {"adapter": adapter}
    metrics.add("support_agent_adapter_circuit_open", _bool(state == "open"), labels, metric_type="gauge")
    metrics.add("support_agent_adapter_circuit_half_open", _bool(state == "half_open"), labels, metric_type="gauge")
    metrics.add("support_agent_adapter_circuit_failures", int(status["failure_count"]), labels, metric_type="gauge")
    metrics.add(
        "support_agent_adapter_circuit_failure_threshold",
        int(status["failure_threshold"]),
        labels,
        metric_type="gauge",
    )
    metrics.add("support_agent_adapter_retry_attempts", int(status["retry_attempts"]), labels, metric_type="gauge")


def _add_llm_metrics(
    metrics: "_MetricWriter",
    deps: AppContainer,
    *,
    source: Literal["event_store", "live"],
    created_after,
    limit: int,
) -> None:
    metrics.add(
        "support_agent_llm_timeout_ms",
        deps.settings.app_llm_timeout_ms,
        {"provider": deps.llm.provider.provider, "model": deps.llm.provider.model},
        metric_type="gauge",
        help_text="Configured LLM gateway timeout.",
    )
    if source != "event_store" or not deps.event_store:
        return
    runs, _total = deps.event_store.search_agent_run_traces(
        tenant_id=deps.settings.app_tenant_id,
        created_after=created_after.isoformat(),
        limit=limit,
    )
    llm_calls = [call for run in runs for call in run.llm_calls]
    fallback_calls = [call for call in llm_calls if call.fallback_used]
    metrics.add("support_agent_llm_calls_window", len(llm_calls), metric_type="gauge")
    metrics.add("support_agent_llm_fallback_calls_window", len(fallback_calls), metric_type="gauge")
    if llm_calls:
        metrics.add(
            "support_agent_llm_fallback_rate",
            len(fallback_calls) / len(llm_calls),
            metric_type="gauge",
        )


def _bool(value: bool) -> int:
    return 1 if value else 0


def _add_optional_timestamp(metrics: "_MetricWriter", name: str, value: datetime | None) -> None:
    if value is None:
        return
    metrics.add(name, _timestamp_seconds(value), metric_type="gauge")


def _timestamp_seconds(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return round(value.timestamp(), 3)


def _age_seconds(then: datetime, now: datetime) -> float:
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return round(max(0.0, (now - then).total_seconds()), 3)


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _avg(values: Iterable[int]) -> float | None:
    items = list(values)
    if not items:
        return None
    return round(sum(items) / len(items), 2)


class _MetricWriter:
    def __init__(self) -> None:
        self._lines: list[str] = []
        self._metadata: set[str] = set()

    def add(
        self,
        name: str,
        value: int | float,
        labels: dict[str, str] | None = None,
        *,
        metric_type: str = "gauge",
        help_text: str | None = None,
    ) -> None:
        if name not in self._metadata:
            if help_text:
                self._lines.append(f"# HELP {name} {help_text}")
            self._lines.append(f"# TYPE {name} {metric_type}")
            self._metadata.add(name)
        self._lines.append(f"{name}{_format_labels(labels or {})} {_format_value(value)}")

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    label_text = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items()))
    return "{" + label_text + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.6g}"

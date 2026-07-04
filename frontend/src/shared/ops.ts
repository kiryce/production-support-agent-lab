import type {
  AgentRunSearchResponse,
  ConsoleSnapshot,
  EvalGateRecord,
  EvalReport,
  KnowledgeSearchResponse,
  MonitorAlert,
  MonitorDrilldownResponse,
  MonitorTriageMetricsResponse,
  ToolAuditSummary
} from "./types";

export type AlertStatusFilter =
  | "active"
  | "all"
  | "open"
  | "acknowledged"
  | "investigating"
  | "resolved"
  | "silenced";

export type AlertSort = "severity" | "newest" | "count";

export type AlertFilters = {
  severity: string;
  status: AlertStatusFilter;
  query: string;
  onlyNew: boolean;
  sort: AlertSort;
};

export type OpsMetrics = {
  openAlerts: number;
  activeAlerts: number;
  p0p1Alerts: number;
  newSinceTriage: number;
  readinessFailed: number;
  groundedRate: number;
  policyComplianceRate: number;
  humanReviewRate: number;
  topFailure: string;
};

export type IncidentBrief = {
  title: string;
  summary: string;
  riskLabel: string;
  recommendedActions: string[];
  markdown: string;
};

export type RunSearchStats = {
  total: number;
  failedRuns: number;
  humanReviewRuns: number;
  toolFailureRuns: number;
  averageDurationMs: number | null;
};

export type ToolAuditStats = {
  totalCalls: number;
  failedCalls: number;
  replayedCalls: number;
  failureRate: number;
  averageLatencyMs: number | null;
  worstToolName: string;
  topErrorCode: string;
};

export type KnowledgeSearchStats = {
  selectedChunks: number;
  sourceCount: number;
  candidateCount: number;
  droppedCandidates: number;
  topScore: number | null;
  topSource: string;
};

export type MonitorDrilldownUiStats = {
  totalEvents: number;
  matchingEvents: number;
  alertRate: number;
  policyViolationRate: number;
  humanReviewRate: number;
  topFailure: string;
  topIntent: string;
  topRisk: string;
};

export type MonitorTriageHealthStats = {
  healthStatus: "ok" | "degraded" | "critical" | "unknown";
  activeAlerts: number;
  unassignedActiveAlerts: number;
  newEventsSinceTriage: number;
  staleActiveAlerts: number;
  p0p1Alerts: number;
  mttaSeconds: number | null;
  mttrSeconds: number | null;
  oldestActiveAlertAt: string | null;
};

const SEVERITY_RANK: Record<string, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
  P3: 3
};

export function filterAndSortAlerts(alerts: MonitorAlert[], filters: AlertFilters) {
  const query = filters.query.trim().toLowerCase();
  return alerts
    .filter((alert) => filters.severity === "all" || alert.severity === filters.severity)
    .filter((alert) => {
      if (filters.status === "all") {
        return true;
      }
      if (filters.status === "active") {
        return alertNeedsAttention(alert);
      }
      return alert.status === filters.status;
    })
    .filter((alert) => !filters.onlyNew || alert.new_events_since_triage)
    .filter((alert) => {
      if (!query) {
        return true;
      }
      return [
        alert.key,
        alert.reason,
        alert.status,
        alert.assignee_user_id ?? "",
        ...alert.sample_run_ids,
        ...alert.sample_event_ids
      ]
        .join(" ")
        .toLowerCase()
        .includes(query);
    })
    .sort((left, right) => {
      if (filters.sort === "newest") {
        return Date.parse(right.last_seen_at) - Date.parse(left.last_seen_at);
      }
      if (filters.sort === "count") {
        return right.count - left.count || compareSeverity(left, right);
      }
      return compareSeverity(left, right) || right.count - left.count || left.key.localeCompare(right.key);
    });
}

export function buildOpsMetrics(snapshot: ConsoleSnapshot | null): OpsMetrics {
  const alerts = snapshot?.summary.alerts ?? [];
  const activeAlerts = alerts.filter(alertNeedsAttention);
  const topFailure =
    topEntry(snapshot?.summary.by_failure_type ?? {})?.[0] ??
    (alerts[0]?.reason ? alerts[0].reason.split(" ")[0] : "none");
  return {
    openAlerts: alerts.filter((alert) => alert.status === "open").length,
    activeAlerts: activeAlerts.length,
    p0p1Alerts: alerts.filter((alert) => alert.severity === "P0" || alert.severity === "P1").length,
    newSinceTriage: alerts.filter((alert) => alert.new_events_since_triage).length,
    readinessFailed: snapshot?.ready?.checks.filter((check) => check.status === "failed").length ?? 0,
    groundedRate: snapshot?.summary.grounded_rate ?? 1,
    policyComplianceRate: snapshot?.summary.policy_compliance_rate ?? 1,
    humanReviewRate: snapshot?.summary.human_review_rate ?? 0,
    topFailure
  };
}

export function buildIncidentBrief(
  snapshot: ConsoleSnapshot | null,
  activeAlert: MonitorAlert | null,
  evalReport: EvalReport | null
): IncidentBrief {
  const run = snapshot?.incident?.run ?? null;
  const monitorEvent = snapshot?.incident?.monitor_events[0] ?? null;
  const toolFailures = run?.tool_results.filter((tool) => tool.status !== "success") ?? [];
  const policyFindings = run?.policy_findings ?? [];
  const citations = run?.retrieval?.selected_context ?? [];
  const readinessFailures = snapshot?.ready?.checks.filter((check) => check.status === "failed") ?? [];
  const evalGate = snapshot?.evalGateLatest ?? latestEvalGateRecord(snapshot?.evalGateRecords ?? []);
  const recommendedActions = buildRecommendedActions({
    activeAlert,
    hasRun: Boolean(run),
    toolFailures: toolFailures.map((tool) => tool.error_code ?? tool.name),
    policyFindings: policyFindings.map((finding) => finding.code),
    citationCount: citations.length,
    readinessFailures: readinessFailures.map((check) => check.name),
    evalReport,
    evalGate
  });
  const riskLabel = activeAlert?.severity ?? monitorEvent?.risk_level ?? "none";
  const title = activeAlert?.reason ?? (run ? `Run ${run.id}` : "No incident selected");
  const summary = run
    ? `${run.intent?.primary ?? "unknown"} routed to ${run.route?.target ?? "unknown"} with ${toolFailures.length} tool failure(s), ${policyFindings.length} policy finding(s), and ${citations.length} citation(s).`
    : "Select an alert or run a local scenario to generate a real incident brief.";
  const markdown = [
    `# PSA Lab Incident Brief`,
    ``,
    `- Risk: ${riskLabel}`,
    `- Alert: ${activeAlert?.key ?? "none"}`,
    `- Alert status: ${activeAlert?.status ?? "none"}`,
    `- Assignee: ${activeAlert?.assignee_user_id ?? "unassigned"}`,
    `- Run: ${run?.id ?? snapshot?.activeRunId ?? "none"}`,
    `- Conversation: ${run?.conversation_id ?? "none"}`,
    `- User: ${run?.user_id ?? "none"}`,
    `- Intent: ${run?.intent?.primary ?? "unknown"}`,
    `- Route: ${run?.route?.target ?? "unknown"}`,
    `- Monitor source: ${snapshot?.monitorSource ?? "unknown"}`,
    `- Tool failures: ${toolFailures.map((tool) => tool.error_code ?? tool.name).join(", ") || "none"}`,
    `- Policy findings: ${policyFindings.map((finding) => finding.code).join(", ") || "none"}`,
    `- Citations used: ${citations.length}`,
    `- Eval gate: ${formatEvalStatus(evalReport, evalGate)}`,
    ``,
    `## Summary`,
    summary,
    ``,
    `## Recommended next actions`,
    ...recommendedActions.map((action) => `- ${action}`)
  ].join("\n");
  return { title, summary, riskLabel, recommendedActions, markdown };
}

export function buildRunSearchStats(response: AgentRunSearchResponse | null): RunSearchStats {
  const items = response?.items ?? [];
  const durations = items
    .map((item) => item.duration_ms)
    .filter((duration): duration is number => typeof duration === "number");
  return {
    total: response?.total ?? 0,
    failedRuns: items.filter((item) => item.status === "failed").length,
    humanReviewRuns: items.filter((item) => item.needs_human).length,
    toolFailureRuns: items.filter((item) => item.failed_tool_count > 0).length,
    averageDurationMs: durations.length
      ? Math.round(durations.reduce((sum, duration) => sum + duration, 0) / durations.length)
      : null
  };
}

export function buildToolAuditStats(summary: ToolAuditSummary | null): ToolAuditStats {
  const worstTool =
    summary?.tools
      .slice()
      .sort(
        (left, right) =>
          right.failed_calls - left.failed_calls ||
          right.total_calls - left.total_calls ||
          left.tool_name.localeCompare(right.tool_name)
      )[0] ?? null;
  return {
    totalCalls: summary?.total_calls ?? 0,
    failedCalls: summary?.failed_calls ?? 0,
    replayedCalls: summary?.replayed_calls ?? 0,
    failureRate: summary?.failure_rate ?? 0,
    averageLatencyMs: summary?.average_latency_ms ?? null,
    worstToolName: worstTool?.tool_name ?? "none",
    topErrorCode: summary?.top_error_codes[0]?.error_code ?? "none"
  };
}

export function buildKnowledgeSearchStats(trace: KnowledgeSearchResponse | null): KnowledgeSearchStats {
  const stageCounts = Object.values(trace?.candidates_by_stage ?? {});
  const topHit = trace?.selected_context[0] ?? null;
  return {
    selectedChunks: trace?.selected_context.length ?? 0,
    sourceCount: new Set(trace?.selected_sources ?? []).size,
    candidateCount: stageCounts.length ? Math.max(...stageCounts) : 0,
    droppedCandidates: trace?.dropped_candidates.length ?? 0,
    topScore: typeof topHit?.score === "number" ? topHit.score : null,
    topSource: topHit?.source_uri ?? "none"
  };
}

export function buildMonitorDrilldownStats(response: MonitorDrilldownResponse | null): MonitorDrilldownUiStats {
  const matchingEvents = response?.stats.matching_events ?? 0;
  return {
    totalEvents: response?.stats.total_events ?? 0,
    matchingEvents,
    alertRate: rate(response?.stats.alerted_events ?? 0, matchingEvents),
    policyViolationRate: rate(response?.stats.policy_violations ?? 0, matchingEvents),
    humanReviewRate: rate(response?.stats.human_review_events ?? 0, matchingEvents),
    topFailure: response?.failure_buckets[0]?.key ?? "none",
    topIntent: response?.intent_buckets[0]?.key ?? "none",
    topRisk: response?.risk_buckets[0]?.key ?? "none"
  };
}

export function buildMonitorTriageHealthStats(metrics: MonitorTriageMetricsResponse | null): MonitorTriageHealthStats {
  if (!metrics) {
    return {
      healthStatus: "unknown",
      activeAlerts: 0,
      unassignedActiveAlerts: 0,
      newEventsSinceTriage: 0,
      staleActiveAlerts: 0,
      p0p1Alerts: 0,
      mttaSeconds: null,
      mttrSeconds: null,
      oldestActiveAlertAt: null
    };
  }
  return {
    healthStatus: metrics.health_status,
    activeAlerts: metrics.active_alert_count,
    unassignedActiveAlerts: metrics.unassigned_active_alert_count,
    newEventsSinceTriage: metrics.new_events_since_triage_count,
    staleActiveAlerts: metrics.stale_active_alert_count,
    p0p1Alerts: (metrics.by_severity.P0 ?? 0) + (metrics.by_severity.P1 ?? 0),
    mttaSeconds: metrics.mtta_seconds,
    mttrSeconds: metrics.mttr_seconds,
    oldestActiveAlertAt: metrics.oldest_active_alert_at
  };
}

export function latestEvalGateRecord(records: EvalGateRecord[]): EvalGateRecord | null {
  return (
    records
      .slice()
      .sort((left, right) => gateTimestamp(right) - gateTimestamp(left))[0] ?? null
  );
}

function buildRecommendedActions(input: {
  activeAlert: MonitorAlert | null;
  hasRun: boolean;
  toolFailures: string[];
  policyFindings: string[];
  citationCount: number;
  readinessFailures: string[];
  evalReport: EvalReport | null;
  evalGate: EvalGateRecord | null;
}) {
  const actions: string[] = [];
  if (!input.hasRun) {
    actions.push("Open a sample run from the alert queue or run a local scenario before triage.");
  }
  if (input.activeAlert && !input.activeAlert.assignee_user_id) {
    actions.push("Assign an owner before changing alert status so follow-up is accountable.");
  }
  if (input.toolFailures.length) {
    actions.push(`Inspect tool audit rows for ${input.toolFailures.join(", ")} and confirm retry/idempotency behavior.`);
  }
  if (input.policyFindings.length) {
    actions.push(`Review policy findings ${input.policyFindings.join(", ")} before resolving the incident.`);
  }
  if (input.hasRun && input.citationCount === 0) {
    actions.push("Check retrieval coverage because this answer has no grounding citations.");
  }
  if (input.readinessFailures.length) {
    actions.push(`Fix readiness failures first: ${input.readinessFailures.join(", ")}.`);
  }
  const persistedGateFailed = input.evalGate?.status === "failed" || input.evalGate?.status === "error";
  if (
    (input.evalReport && input.evalReport.passed !== input.evalReport.total) ||
    (!input.evalReport && persistedGateFailed)
  ) {
    actions.push("Do not promote this change until the eval failures are investigated.");
  }
  if (!actions.length) {
    actions.push("Evidence is complete; resolve only after the operator note explains the customer impact and mitigation.");
  }
  return actions;
}

function compareSeverity(left: MonitorAlert, right: MonitorAlert) {
  return (SEVERITY_RANK[left.severity] ?? 9) - (SEVERITY_RANK[right.severity] ?? 9);
}

function alertNeedsAttention(alert: MonitorAlert) {
  if (alert.status === "silenced") {
    return false;
  }
  return alert.status !== "resolved" || alert.new_events_since_triage;
}

function topEntry(values: Record<string, number>) {
  return Object.entries(values).sort((left, right) => right[1] - left[1])[0];
}

function rate(numerator: number, denominator: number) {
  return denominator > 0 ? numerator / denominator : 0;
}

export function formatEvalStatus(report: EvalReport | null, gate: EvalGateRecord | null = null) {
  if (!report) {
    if (!gate) {
      return "not run";
    }
    if (gate.status === "error") {
      return `error (${gate.error_message ?? "runner failed"})`;
    }
    if (typeof gate.passed === "number" && typeof gate.total === "number") {
      return `${gate.passed}/${gate.total} passed (${Math.round((gate.score ?? 0) * 100)}%)`;
    }
    return gate.status;
  }
  return `${report.passed}/${report.total} passed (${Math.round(report.score * 100)}%)`;
}

function gateTimestamp(record: EvalGateRecord) {
  const timestamp = Date.parse(record.completed_at ?? record.created_at);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

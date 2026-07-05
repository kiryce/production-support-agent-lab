import { NextRequest, NextResponse } from "next/server";
import { agentFetch, getConsoleConnection, issueFrom } from "@/src/server/agentApi";
import type {
  ConsoleSnapshot,
  EvalGateRecord,
  IncidentBriefResponse,
  IncidentRunBundle,
  JsonRecord,
  MonitorAlert,
  MonitorAlertDeliverySummary,
  MonitorAlertTriageEvent,
  MonitorSummary,
  MonitorTriageMetricsResponse,
  OperationsAutomationPlan,
  PromotionDecisionRecord,
  PromotionGateResponse,
  ReadinessResponse,
  SloReportResponse,
  StoredEvent,
  ToolDefinition
} from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const requestedRunId = request.nextUrl.searchParams.get("runId");
  const requestedAlertKey = request.nextUrl.searchParams.get("alertKey");
  const issues: ConsoleSnapshot["issues"] = [];

  const [health, ready] = await Promise.all([
    optional<JsonRecord>(() => agentFetch("/api/v1/health"), issues),
    optional<ReadinessResponse>(() => agentFetch("/api/v1/ready", { query: { deep: false } }), issues)
  ]);

  let monitorSource: ConsoleSnapshot["monitorSource"] = "event_store";
  let summary = await optional<MonitorSummary>(
    () =>
      agentFetch("/api/v1/admin/monitor/summary", {
        query: { source: "event_store", limit: 500 }
      }),
    issues
  );

  if (!summary) {
    monitorSource = "live";
    summary = await required<MonitorSummary>(() =>
      agentFetch("/api/v1/admin/monitor/summary", {
        query: { source: "live", limit: 500 }
      })
    );
  }
  const triageMetrics = await optional<MonitorTriageMetricsResponse>(
    () =>
      agentFetch("/api/v1/admin/monitor/triage/metrics", {
        query: { source: monitorSource, limit: 500 }
      }),
    issues
  );
  const promotionGate = await optional<PromotionGateResponse>(
    () =>
      agentFetch("/api/v1/admin/promotion/gate", {
        query: { source: monitorSource, deep: false, window_hours: 24 }
      }),
    issues
  );
  const promotionDecisions = await optional<PromotionDecisionRecord[]>(
    () =>
      agentFetch("/api/v1/admin/promotion/decisions", {
        query: { limit: 5, order: "desc" }
      }),
    issues
  );
  const operationsAutomation = await optional<OperationsAutomationPlan>(
    () =>
      agentFetch("/api/v1/admin/operations/automation-plan", {
        query: { source: monitorSource, deep: false, window_hours: 24, limit: 500 }
      }),
    issues
  );
  const sloReport = await optional<SloReportResponse>(
    () =>
      agentFetch("/api/v1/admin/operations/slo-report", {
        query: { source: monitorSource, deep: false, window_hours: 24 }
      }),
    issues
  );
  const monitorAlertDelivery = await optional<MonitorAlertDeliverySummary>(
    () =>
      agentFetch("/api/v1/admin/monitor/alert-deliveries/summary", {
        query: { limit: 200 }
      }),
    issues
  );

  const activeAlert = requestedRunId
    ? selectAlertForRun(summary.alerts, requestedAlertKey, requestedRunId)
    : selectAlert(summary.alerts, requestedAlertKey);
  const candidateRunIds = requestedRunId
    ? [requestedRunId]
    : activeAlert?.sample_run_ids ?? [];
  let activeRunId: string | null = candidateRunIds[0] ?? null;
  let incident: IncidentRunBundle | null = null;
  for (const candidateRunId of candidateRunIds) {
    const candidateIssues = requestedRunId ? issues : [];
    const candidateIncident = await optional<IncidentRunBundle>(
      () =>
        agentFetch(`/api/v1/admin/incidents/runs/${encodeURIComponent(candidateRunId)}`, {
          query: { include_memory: true, limit: 1000 }
        }),
      candidateIssues
    );
    if (candidateIncident) {
      activeRunId = candidateRunId;
      incident = candidateIncident;
      break;
    }
  }

  const incidentAlertKey = incident?.monitor_events.find((event) => event.alert_key)?.alert_key;
  const matchingAlertKey = activeRunId
    ? summary.alerts.find((alert) => alert.sample_run_ids.includes(activeRunId))?.key
    : null;
  const knownIncidentAlertKey = incidentAlertKey
    ? summary.alerts.find((alert) => alert.key === incidentAlertKey)?.key
    : null;
  const activeAlertKey = requestedRunId
    ? activeAlert?.key ?? matchingAlertKey ?? knownIncidentAlertKey ?? null
    : requestedAlertKey ??
      activeAlert?.key ??
      matchingAlertKey ??
      knownIncidentAlertKey ??
      null;

  const incidentBrief =
    activeRunId && incident
      ? await optional<IncidentBriefResponse>(
          () =>
            agentFetch(`/api/v1/admin/incidents/runs/${encodeURIComponent(activeRunId)}/brief`, {
              query: { include_memory: true, limit: 1000 }
            }),
          issues
        )
      : null;

  const evalGateQuery = activeAlertKey
    ? { alert_key: activeAlertKey, limit: 5 }
    : activeRunId
      ? { run_id: activeRunId, limit: 5 }
      : { limit: 5 };

  const [triageEvents, rawEvents, tools, evalGateRecords] = await Promise.all([
    activeAlertKey
      ? optional<MonitorAlertTriageEvent[]>(
          () =>
            agentFetch(
              `/api/v1/admin/monitor/alerts/${encodeURIComponent(activeAlertKey)}/triage`,
              { query: { limit: 100 } }
            ),
          issues
        )
      : Promise.resolve([]),
    incident?.run.conversation_id
      ? optional<StoredEvent[]>(
          () =>
            agentFetch("/api/v1/admin/events", {
              query: { conversation_id: incident.run.conversation_id, limit: 200 }
            }),
          issues
        )
      : Promise.resolve([]),
    optional<ToolDefinition[]>(() => agentFetch("/api/v1/admin/tools"), issues),
    optional<EvalGateRecord[]>(
      () =>
        agentFetch("/api/v1/admin/evals/gates", {
          query: evalGateQuery
        }),
      issues
    )
  ]);
  const resolvedEvalGateRecords = evalGateRecords ?? [];

  const snapshot: ConsoleSnapshot = {
    health,
    ready,
    summary,
    monitorSource,
    activeAlertKey,
    activeRunId,
    incident,
    incidentBrief,
    triageMetrics,
    promotionGate,
    promotionDecisions: promotionDecisions ?? [],
    operationsAutomation,
    sloReport,
    monitorAlertDelivery,
    triageEvents: triageEvents ?? [],
    evalGateLatest: resolvedEvalGateRecords[0] ?? null,
    evalGateRecords: resolvedEvalGateRecords,
    rawEvents: rawEvents ?? [],
    tools: tools ?? [],
    issues,
    connection: getConsoleConnection()
  };

  return NextResponse.json(snapshot);
}

async function optional<T>(
  fetcher: () => Promise<T>,
  issues: ConsoleSnapshot["issues"]
): Promise<T | null> {
  try {
    return await fetcher();
  } catch (error) {
    issues.push(issueFrom(error));
    return null;
  }
}

async function required<T>(fetcher: () => Promise<T>): Promise<T> {
  try {
    return await fetcher();
  } catch (error) {
    return Promise.reject(error);
  }
}

function selectAlert(alerts: MonitorAlert[], key: string | null) {
  if (!alerts.length) {
    return null;
  }
  if (!key) {
    return alerts[0];
  }
  return alerts.find((alert) => alert.key === key) ?? alerts[0];
}

function selectAlertForRun(alerts: MonitorAlert[], key: string | null, runId: string) {
  if (!alerts.length) {
    return null;
  }
  if (key) {
    const requested = alerts.find((alert) => alert.key === key);
    if (requested?.sample_run_ids.includes(runId)) {
      return requested;
    }
  }
  return alerts.find((alert) => alert.sample_run_ids.includes(runId)) ?? null;
}

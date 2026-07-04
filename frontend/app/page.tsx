"use client";

import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  AlertTriangle,
  Bell,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Copy,
  Database,
  Download,
  FileCheck2,
  Filter,
  Gauge,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Route as RouteIcon,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  User,
  UserPlus,
  Wrench,
  X
} from "lucide-react";
import {
  buildIncidentBrief,
  buildKnowledgeSearchStats,
  buildOpsMetrics,
  buildRunSearchStats,
  buildToolAuditStats,
  filterAndSortAlerts,
  type AlertSort,
  type AlertStatusFilter,
  type IncidentBrief,
  type KnowledgeSearchStats,
  type OpsMetrics,
  type RunSearchStats,
  type ToolAuditStats
} from "@/src/shared/ops";
import type {
  AgentRunSearchItem,
  AgentRunSearchResponse,
  AgentRunTrace,
  ConsoleSnapshot,
  EvalReport,
  IncidentRunBundle,
  JsonValue,
  KnowledgeSearchResponse,
  MonitorAlert,
  MonitorEvent,
  PolicyFinding,
  RetrievalHit,
  RetrievalTrace,
  StoredEvent,
  ToolAuditRecord,
  ToolAuditSearchResponse,
  ToolResult,
  TraceSpan
} from "@/src/shared/types";

const LOCAL_SCENARIO =
  "My order A1001 headphones arrived broken. Can I return them or get help?";

const STEPS: Array<{ id: TimelineStepId; label: string; icon: LucideIcon }> = [
  { id: "message", label: "Message", icon: ClipboardList },
  { id: "intent", label: "Intent", icon: Search },
  { id: "route", label: "Route", icon: RouteIcon },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "retrieval", label: "Retrieval", icon: BookOpen },
  { id: "answer", label: "Answer", icon: Check },
  { id: "monitor", label: "Monitor", icon: Activity }
];

type TimelineStepId =
  | "message"
  | "intent"
  | "route"
  | "tools"
  | "retrieval"
  | "answer"
  | "monitor";

type EvidenceTab = "brief" | "citations" | "tool-audit" | "memory" | "triage";
type WorkspaceMode = "alerts" | "runs" | "tools" | "knowledge";

type LoadInput = {
  runId?: string | null;
  alertKey?: string | null;
};

type ToolAuditSearchOverrides = Partial<{
  toolName: string;
  actorUserId: string;
  traceId: string;
  requestId: string;
  status: string;
  errorCode: string;
  replayed: string;
  createdAfter: string;
  createdBefore: string;
  order: "asc" | "desc";
}>;

type TimelineStep = {
  id: TimelineStepId;
  title: string;
  eyebrow: string;
  time: string;
  duration: string;
  status: "ok" | "warn" | "error" | "empty";
  chips: string[];
  body: ReactNode;
};

export default function Home() {
  const [snapshot, setSnapshot] = useState<ConsoleSnapshot | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedAlertKey, setSelectedAlertKey] = useState<string | null>(null);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("alerts");
  const [runQuery, setRunQuery] = useState("");
  const [runSearchQuery, setRunSearchQuery] = useState("");
  const [runSearchUserId, setRunSearchUserId] = useState("");
  const [runSearchConversationId, setRunSearchConversationId] = useState("");
  const [runSearchIntent, setRunSearchIntent] = useState("");
  const [runSearchRoute, setRunSearchRoute] = useState("");
  const [runSearchStatus, setRunSearchStatus] = useState("");
  const [runSearchErrorCode, setRunSearchErrorCode] = useState("");
  const [runSearchResults, setRunSearchResults] = useState<AgentRunSearchResponse | null>(null);
  const [runSearchOffset, setRunSearchOffset] = useState(0);
  const [runSearchLoading, setRunSearchLoading] = useState(false);
  const [runSearchError, setRunSearchError] = useState<string | null>(null);
  const [toolAuditToolName, setToolAuditToolName] = useState("");
  const [toolAuditActorUserId, setToolAuditActorUserId] = useState("");
  const [toolAuditTraceId, setToolAuditTraceId] = useState("");
  const [toolAuditRequestId, setToolAuditRequestId] = useState("");
  const [toolAuditStatus, setToolAuditStatus] = useState("");
  const [toolAuditErrorCode, setToolAuditErrorCode] = useState("");
  const [toolAuditReplayed, setToolAuditReplayed] = useState("");
  const [toolAuditCreatedAfter, setToolAuditCreatedAfter] = useState("");
  const [toolAuditCreatedBefore, setToolAuditCreatedBefore] = useState("");
  const [toolAuditOrder, setToolAuditOrder] = useState<"asc" | "desc">("desc");
  const [toolAuditResults, setToolAuditResults] = useState<ToolAuditSearchResponse | null>(null);
  const [toolAuditLoading, setToolAuditLoading] = useState(false);
  const [toolAuditError, setToolAuditError] = useState<string | null>(null);
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [knowledgeLimit, setKnowledgeLimit] = useState("4");
  const [knowledgeTrace, setKnowledgeTrace] = useState<KnowledgeSearchResponse | null>(null);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<AlertStatusFilter>("active");
  const [queueQuery, setQueueQuery] = useState("");
  const [queueSort, setQueueSort] = useState<AlertSort>("severity");
  const [onlyNewAlerts, setOnlyNewAlerts] = useState(false);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("brief");
  const [expandedSteps, setExpandedSteps] = useState<Set<TimelineStepId>>(
    () => new Set(["message", "retrieval", "monitor"])
  );
  const [rawOpen, setRawOpen] = useState(false);
  const [expandedCitations, setExpandedCitations] = useState(false);
  const [scenarioText, setScenarioText] = useState(LOCAL_SCENARIO);
  const [triageNote, setTriageNote] = useState("");
  const [assigneeUserId, setAssigneeUserId] = useState("");
  const [evalReport, setEvalReport] = useState<EvalReport | null>(null);
  const [copiedBrief, setCopiedBrief] = useState(false);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSnapshot = useCallback(async (input: LoadInput = {}) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (input.runId) {
        params.set("runId", input.runId);
      }
      if (input.alertKey) {
        params.set("alertKey", input.alertKey);
      }
      const response = await fetch(`/api/console/snapshot?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Console snapshot failed");
      }
      const nextSnapshot = data as ConsoleSnapshot;
      setSnapshot(nextSnapshot);
      setSelectedRunId(nextSnapshot.activeRunId);
      setSelectedAlertKey(nextSnapshot.activeAlertKey);
      setRunQuery(nextSnapshot.activeRunId ?? "");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Console snapshot failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  const activeAlert = useMemo(() => {
    if (!snapshot?.summary.alerts.length || !snapshot.activeAlertKey) {
      return null;
    }
    return snapshot.summary.alerts.find((alert) => alert.key === snapshot.activeAlertKey) ?? null;
  }, [snapshot]);

  const filteredAlerts = useMemo(
    () =>
      filterAndSortAlerts(snapshot?.summary.alerts ?? [], {
        severity: severityFilter,
        status: statusFilter,
        query: queueQuery,
        onlyNew: onlyNewAlerts,
        sort: queueSort
      }),
    [onlyNewAlerts, queueQuery, queueSort, severityFilter, snapshot, statusFilter]
  );

  const opsMetrics = useMemo<OpsMetrics>(() => buildOpsMetrics(snapshot), [snapshot]);

  const incidentBrief = useMemo<IncidentBrief>(
    () => buildIncidentBrief(snapshot, activeAlert, evalReport),
    [activeAlert, evalReport, snapshot]
  );

  const runSearchStats = useMemo<RunSearchStats>(
    () => buildRunSearchStats(runSearchResults),
    [runSearchResults]
  );

  const toolAuditStats = useMemo<ToolAuditStats>(
    () => buildToolAuditStats(toolAuditResults?.summary ?? null),
    [toolAuditResults]
  );

  const knowledgeStats = useMemo<KnowledgeSearchStats>(
    () => buildKnowledgeSearchStats(knowledgeTrace),
    [knowledgeTrace]
  );

  useEffect(() => {
    setAssigneeUserId(activeAlert?.assignee_user_id ?? "");
  }, [activeAlert?.key, activeAlert?.assignee_user_id]);

  const timelineSteps = useMemo(
    () => buildTimeline(snapshot?.incident ?? null),
    [snapshot?.incident]
  );

  const run = snapshot?.incident?.run ?? null;
  const isDemo = snapshot?.connection.authMode !== "production";

  async function runScenario() {
    setActionBusy("scenario");
    setError(null);
    try {
      const response = await fetch("/api/console/run-scenario", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: scenarioText })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Scenario failed");
      }
      await loadSnapshot({ runId: data.message.trace_id });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Scenario failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function searchRuns(nextOffset = 0) {
    setRunSearchLoading(true);
    setRunSearchError(null);
    try {
      const params = new URLSearchParams();
      const values: Record<string, string> = {
        q: runSearchQuery,
        userId: runSearchUserId,
        conversationId: runSearchConversationId,
        intent: runSearchIntent,
        route: runSearchRoute,
        status: runSearchStatus,
        errorCode: runSearchErrorCode,
        limit: "25",
        offset: String(nextOffset)
      };
      for (const [key, value] of Object.entries(values)) {
        const trimmed = value.trim();
        if (trimmed) {
          params.set(key, trimmed);
        }
      }
      const response = await fetch(`/api/console/runs?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Run search failed");
      }
      setRunSearchResults(data as AgentRunSearchResponse);
      setRunSearchOffset(nextOffset);
    } catch (nextError) {
      setRunSearchError(nextError instanceof Error ? nextError.message : "Run search failed");
    } finally {
      setRunSearchLoading(false);
    }
  }

  function submitRunSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void searchRuns(0);
  }

  async function searchToolAudit(overrides: ToolAuditSearchOverrides = {}) {
    setToolAuditLoading(true);
    setToolAuditError(null);
    try {
      const values = {
        toolName: overrides.toolName ?? toolAuditToolName,
        actorUserId: overrides.actorUserId ?? toolAuditActorUserId,
        traceId: overrides.traceId ?? toolAuditTraceId,
        requestId: overrides.requestId ?? toolAuditRequestId,
        status: overrides.status ?? toolAuditStatus,
        errorCode: overrides.errorCode ?? toolAuditErrorCode,
        replayed: overrides.replayed ?? toolAuditReplayed,
        createdAfter: overrides.createdAfter ?? toolAuditCreatedAfter,
        createdBefore: overrides.createdBefore ?? toolAuditCreatedBefore,
        order: overrides.order ?? toolAuditOrder
      };
      if (overrides.toolName !== undefined) {
        setToolAuditToolName(overrides.toolName);
      }
      if (overrides.traceId !== undefined) {
        setToolAuditTraceId(overrides.traceId);
      }
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(values)) {
        const trimmed = String(value).trim();
        if (trimmed) {
          params.set(key, trimmed);
        }
      }
      params.set("limit", "50");
      const response = await fetch(`/api/console/tools/audit?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Tool audit search failed");
      }
      setToolAuditResults(data as ToolAuditSearchResponse);
    } catch (nextError) {
      setToolAuditError(nextError instanceof Error ? nextError.message : "Tool audit search failed");
    } finally {
      setToolAuditLoading(false);
    }
  }

  function submitToolAuditSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void searchToolAudit();
  }

  async function searchKnowledge(nextQuery = knowledgeQuery, nextLimit = knowledgeLimit) {
    const trimmed = nextQuery.trim();
    if (!trimmed) {
      setKnowledgeError("Enter a query before searching knowledge.");
      return;
    }
    setKnowledgeLoading(true);
    setKnowledgeError(null);
    try {
      const response = await fetch("/api/console/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          query: trimmed,
          limit: Number(nextLimit),
          snippet_chars: 500
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Knowledge search failed");
      }
      setKnowledgeQuery(trimmed);
      setKnowledgeLimit(nextLimit);
      setKnowledgeTrace(data as KnowledgeSearchResponse);
    } catch (nextError) {
      setKnowledgeError(nextError instanceof Error ? nextError.message : "Knowledge search failed");
    } finally {
      setKnowledgeLoading(false);
    }
  }

  function submitKnowledgeSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void searchKnowledge();
  }

  function loadCurrentRunRetrieval(trace: RetrievalTrace) {
    setKnowledgeTrace(toKnowledgeSearchResponse(trace));
    setKnowledgeQuery(trace.query);
    setKnowledgeLimit(String(Math.max(1, Math.min(20, trace.selected_context.length || 4))));
    setEvidenceTab("citations");
  }

  function openRunFromWorkbench(item: AgentRunSearchItem) {
    setWorkspaceMode("runs");
    setSelectedAlertKey(null);
    setSelectedRunId(item.id);
    setRunQuery(item.id);
    void loadSnapshot({ runId: item.id });
  }

  function openToolAuditRecord(record: ToolAuditRecord) {
    setWorkspaceMode("tools");
    setEvidenceTab("tool-audit");
    setSelectedAlertKey(null);
    setSelectedRunId(record.trace_id);
    setRunQuery(record.trace_id);
    void loadSnapshot({ runId: record.trace_id });
  }

  async function submitTriage(status: string, nextAssigneeUserId?: string | null, noteOverride?: string) {
    if (!snapshot?.activeAlertKey) {
      return;
    }
    setActionBusy(status);
    setError(null);
    try {
      const response = await fetch("/api/console/triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alertKey: snapshot.activeAlertKey,
          status,
          assigneeUserId: nextAssigneeUserId ?? (assigneeUserId || null),
          note: noteOverride ?? (triageNote || `${status} from PSA Lab Console`)
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Triage update failed");
      }
      setTriageNote("");
      await loadSnapshot({
        alertKey: snapshot.activeAlertKey,
        runId: snapshot.activeRunId
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Triage update failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function runGoldenEval() {
    setActionBusy("eval");
    setError(null);
    try {
      const response = await fetch("/api/console/run-eval", {
        method: "POST",
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Eval gate failed");
      }
      setEvalReport(data as EvalReport);
      setEvidenceTab("brief");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Eval gate failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function copyIncidentBrief() {
    try {
      await navigator.clipboard.writeText(incidentBrief.markdown);
      setCopiedBrief(true);
      window.setTimeout(() => setCopiedBrief(false), 1800);
    } catch {
      setError("Clipboard is not available in this browser session");
    }
  }

  function chooseAlert(alert: MonitorAlert) {
    const runId = alert.sample_run_ids[0] ?? null;
    setSelectedAlertKey(alert.key);
    setSelectedRunId(runId);
    void loadSnapshot({ alertKey: alert.key, runId });
  }

  function submitRunLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextRun = runQuery.trim();
    const lookupInput = event.currentTarget.querySelector<HTMLInputElement>("input");
    lookupInput?.setSelectionRange(0, 0);
    if (lookupInput) {
      lookupInput.scrollLeft = 0;
    }
    lookupInput?.blur();
    setSelectedRunId(nextRun || null);
    setSelectedAlertKey(null);
    void loadSnapshot({ runId: nextRun || null });
  }

  function exportJson() {
    if (!snapshot) {
      return;
    }
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
      type: "application/json"
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${snapshot.activeRunId ?? "psa-console"}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function toggleStep(step: TimelineStepId) {
    setExpandedSteps((current) => {
      const next = new Set(current);
      if (next.has(step)) {
        next.delete(step);
      } else {
        next.add(step);
      }
      return next;
    });
  }

  return (
    <main className="console-shell">
      <Rail
        activeTarget={workspaceMode}
        alertCount={opsMetrics.activeAlerts}
        onSelect={(target) => {
          if (target === "runs") {
            setWorkspaceMode("runs");
            if (!runSearchResults && !runSearchLoading) {
              void searchRuns(0);
            }
          }
          if (target === "tools") {
            setWorkspaceMode("tools");
            setEvidenceTab("tool-audit");
            if (!toolAuditResults && !toolAuditLoading) {
              void searchToolAudit();
            }
          }
          if (target === "knowledge") {
            setWorkspaceMode("knowledge");
            setEvidenceTab("citations");
            const currentRetrieval = snapshot?.incident?.run.retrieval ?? null;
            if (!knowledgeTrace && currentRetrieval) {
              loadCurrentRunRetrieval(currentRetrieval);
            }
          }
          if (target === "memory") {
            setEvidenceTab("memory");
          }
          if (target === "alerts") {
            setWorkspaceMode("alerts");
            setSeverityFilter("all");
          }
          if (target === "settings") {
            setRawOpen((value) => !value);
          }
        }}
      />

      <section className="console-main">
        <header className="topbar">
          <div className="brand-lockup">
            <div className="brand-mark">
              <Layers size={24} />
            </div>
            <div>
              <strong>PSA Lab</strong>
              <span>Production Support</span>
            </div>
          </div>

          <div className="stepper" aria-label="Agent run stages">
            {STEPS.slice(0, 6).map((step, index) => {
              const Icon = step.icon;
              const current = step.id === "intent";
              return (
                <button
                  className={`step-pill ${current ? "is-current" : ""}`}
                  key={step.id}
                  type="button"
                  title={`Focus ${step.label}`}
                  onClick={() => toggleStep(step.id)}
                >
                  <span>{index + 1}</span>
                  <Icon size={14} />
                  {step.label}
                </button>
              );
            })}
          </div>

          <div className="top-actions">
            <button
              className="ghost-button"
              type="button"
              onClick={() => void loadSnapshot({ runId: selectedRunId, alertKey: selectedAlertKey })}
              disabled={loading}
              title="Refresh console data"
            >
              <RefreshCw size={16} />
              Refresh
            </button>
            <form className="run-lookup" onSubmit={submitRunLookup}>
              <input
                aria-label="Run ID"
                value={runQuery}
                onChange={(event) => setRunQuery(event.target.value)}
                placeholder="run_id"
              />
              {runQuery ? (
                <button
                  type="button"
                  title="Clear run id"
                  onClick={() => {
                    setRunQuery("");
                    setSelectedRunId(null);
                    void loadSnapshot({ alertKey: selectedAlertKey });
                  }}
                >
                  <X size={14} />
                </button>
              ) : null}
            </form>
            <button className="ghost-button" type="button" onClick={exportJson} disabled={!snapshot}>
              <Download size={16} />
              Export JSON
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={!isDemo || actionBusy === "scenario"}
              onClick={() => void runScenario()}
              title={isDemo ? "Run a real local API scenario" : "Disabled in production auth mode"}
            >
              {actionBusy === "scenario" ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
              Run Scenario
            </button>
          </div>
        </header>

        <section className="contextbar">
          <ContextItem
            label="API Connection"
            value={snapshot?.connection.label ?? "Agent API"}
            detail={snapshot?.monitorSource === "live" ? "Live process memory" : "Event store"}
            state={snapshot?.health?.status === "ok" ? "ok" : "warn"}
          />
          <ContextItem
            label="Actor Mode"
            value={snapshot?.connection.actorRole ?? "admin"}
            detail={snapshot?.connection.actorUserId ?? "console"}
            icon={User}
          />
          <ContextItem
            label="Scopes"
            value="monitor, audit, knowledge"
            detail="BFF signed server-side"
            icon={ShieldCheck}
          />
          <ContextItem
            label="Run ID"
            value={run?.id ?? selectedRunId ?? "No run selected"}
            detail={run ? `${run.status} from ${snapshot?.incident?.run_source}` : "Waiting for events"}
          />
          <ContextItem
            label="Conversation"
            value={run?.conversation_id ?? "None"}
            detail={run ? run.user_id : "No active trace"}
          />
          <div className="context-actions">
            <button
              className="secondary-button"
              type="button"
              disabled={!activeAlert || actionBusy === "acknowledged"}
              onClick={() => void submitTriage("acknowledged")}
            >
              <Check size={16} />
              Acknowledge
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={!activeAlert || actionBusy === "investigating"}
              onClick={() =>
                void submitTriage(
                  "investigating",
                  snapshot?.connection.actorUserId ?? null,
                  "Assigned to current console operator"
                )
              }
            >
              <UserPlus size={16} />
              Assign
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={!activeAlert || actionBusy === "resolved"}
              onClick={() => void submitTriage("resolved")}
            >
              <Check size={16} />
              Resolve
            </button>
          </div>
        </section>

        <div
          className={error ? "error-strip" : "sr-only"}
          role={error ? "alert" : "status"}
          aria-live="polite"
        >
          {error ?? ""}
        </div>

        <OpsOverview metrics={opsMetrics} snapshot={snapshot} evalReport={evalReport} />

        <section className="workspace">
          {workspaceMode === "knowledge" ? (
            <KnowledgeWorkbenchPanel
              trace={knowledgeTrace}
              stats={knowledgeStats}
              loading={knowledgeLoading}
              error={knowledgeError}
              query={knowledgeQuery}
              limit={knowledgeLimit}
              currentRetrieval={run?.retrieval ?? null}
              onQuery={setKnowledgeQuery}
              onLimit={setKnowledgeLimit}
              onSubmit={submitKnowledgeSearch}
              onSearch={searchKnowledge}
              onUseCurrent={loadCurrentRunRetrieval}
            />
          ) : workspaceMode === "tools" ? (
            <ToolAuditWorkbenchPanel
              results={toolAuditResults}
              stats={toolAuditStats}
              loading={toolAuditLoading}
              error={toolAuditError}
              selectedTraceId={selectedRunId}
              tools={snapshot?.tools ?? []}
              toolName={toolAuditToolName}
              actorUserId={toolAuditActorUserId}
              traceId={toolAuditTraceId}
              requestId={toolAuditRequestId}
              status={toolAuditStatus}
              errorCode={toolAuditErrorCode}
              replayed={toolAuditReplayed}
              createdAfter={toolAuditCreatedAfter}
              createdBefore={toolAuditCreatedBefore}
              order={toolAuditOrder}
              onToolName={setToolAuditToolName}
              onActorUserId={setToolAuditActorUserId}
              onTraceId={setToolAuditTraceId}
              onRequestId={setToolAuditRequestId}
              onStatus={setToolAuditStatus}
              onErrorCode={setToolAuditErrorCode}
              onReplayed={setToolAuditReplayed}
              onCreatedAfter={setToolAuditCreatedAfter}
              onCreatedBefore={setToolAuditCreatedBefore}
              onOrder={setToolAuditOrder}
              onSubmit={submitToolAuditSearch}
              onSearch={searchToolAudit}
              onOpenRecord={openToolAuditRecord}
            />
          ) : workspaceMode === "runs" ? (
            <RunWorkbenchPanel
              results={runSearchResults}
              stats={runSearchStats}
              loading={runSearchLoading}
              error={runSearchError}
              selectedRunId={selectedRunId}
              query={runSearchQuery}
              userId={runSearchUserId}
              conversationId={runSearchConversationId}
              intent={runSearchIntent}
              route={runSearchRoute}
              status={runSearchStatus}
              errorCode={runSearchErrorCode}
              offset={runSearchOffset}
              onQuery={setRunSearchQuery}
              onUserId={setRunSearchUserId}
              onConversationId={setRunSearchConversationId}
              onIntent={setRunSearchIntent}
              onRoute={setRunSearchRoute}
              onStatus={setRunSearchStatus}
              onErrorCode={setRunSearchErrorCode}
              onSubmit={submitRunSearch}
              onPage={searchRuns}
              onOpenRun={openRunFromWorkbench}
            />
          ) : (
            <aside className="alerts-panel">
            <div className="panel-heading">
              <div>
                <span>Monitor Alert Queue</span>
                <strong>
                  {filteredAlerts.length} of {snapshot?.summary.alerts.length ?? 0} alerts
                </strong>
              </div>
            </div>

            <div className="queue-controls" aria-label="Alert queue controls">
              <label className="search-control">
                <Search size={14} />
                <input
                  value={queueQuery}
                  onChange={(event) => setQueueQuery(event.target.value)}
                  placeholder="Search run, reason, owner"
                  aria-label="Search alert queue"
                />
              </label>
              <label className="filter-control">
                <Filter size={14} />
                <select
                  value={severityFilter}
                  onChange={(event) => setSeverityFilter(event.target.value)}
                  aria-label="Filter alerts by severity"
                >
                  <option value="all">All severity</option>
                  <option value="P0">P0</option>
                  <option value="P1">P1</option>
                  <option value="P2">P2</option>
                  <option value="P3">P3</option>
                </select>
              </label>
              <label className="filter-control">
                <SlidersHorizontal size={14} />
                <select
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value as AlertStatusFilter)}
                  aria-label="Filter alerts by status"
                >
                  <option value="active">Active</option>
                  <option value="all">All status</option>
                  <option value="open">Open</option>
                  <option value="acknowledged">Acknowledged</option>
                  <option value="investigating">Investigating</option>
                  <option value="resolved">Resolved</option>
                  <option value="silenced">Silenced</option>
                </select>
              </label>
              <label className="filter-control">
                <SlidersHorizontal size={14} />
                <select
                  value={queueSort}
                  onChange={(event) => setQueueSort(event.target.value as AlertSort)}
                  aria-label="Sort alerts"
                >
                  <option value="severity">Severity</option>
                  <option value="newest">Newest</option>
                  <option value="count">Count</option>
                </select>
              </label>
              <label className="check-control">
                <input
                  type="checkbox"
                  checked={onlyNewAlerts}
                  onChange={(event) => setOnlyNewAlerts(event.target.checked)}
                />
                New events
              </label>
            </div>

            <div className="alert-list">
              {loading && !snapshot ? <LoadingBlock /> : null}
              {!loading && snapshot && filteredAlerts.length === 0 ? (
                <EmptyQueue
                  isDemo={isDemo}
                  scenarioText={scenarioText}
                  onScenarioText={setScenarioText}
                  onRunScenario={() => void runScenario()}
                  busy={actionBusy === "scenario"}
                />
              ) : null}
              {filteredAlerts.map((alert) => (
                <button
                  type="button"
                  className={`alert-card severity-${alert.severity.toLowerCase()} ${
                    alert.key === activeAlert?.key ? "is-selected" : ""
                  }`}
                  key={alert.key}
                  onClick={() => chooseAlert(alert)}
                  aria-pressed={alert.key === activeAlert?.key}
                >
                  <div className="alert-card-top">
                    <span className="severity-dot" />
                    <strong>{alert.severity}</strong>
                    <time title={alert.last_seen_at}>{ageLabel(alert.last_seen_at)}</time>
                  </div>
                  <span className="alert-title">{alert.reason}</span>
                  <span className="alert-meta">
                    {alert.sample_run_ids[0] ?? "no run"} · {alert.count} event
                    {alert.count === 1 ? "" : "s"}
                  </span>
                  <span className="tag-row">
                    <Badge>{alert.status}</Badge>
                    <Badge>{alert.assignee_user_id ?? "unassigned"}</Badge>
                    {alert.new_events_since_triage ? <Badge tone="warn">new events</Badge> : null}
                  </span>
                </button>
              ))}
            </div>

            <div className="queue-footer">
              <span>
                {filteredAlerts.length} of {snapshot?.summary.alerts.length ?? 0}
              </span>
              <button type="button" onClick={() => setSeverityFilter("all")}>
                View all
                <ChevronRight size={15} />
              </button>
            </div>
            </aside>
          )}

          <section className="run-panel">
            <div className="run-heading">
              <div>
                <span>Agent Run</span>
                <h1>{run?.id ?? "No run selected"}</h1>
              </div>
              <Badge tone={statusTone(run?.status)}>{run?.status ?? "idle"}</Badge>
              <Metric label="Duration" value={runDuration(run)} />
              <Metric
                label="Outcome"
                value={
                  run?.policy_findings.some((finding) => finding.should_block)
                    ? "Policy blocked"
                    : run
                      ? "Answer recorded"
                      : "No trace"
                }
              />
            </div>

            {snapshot?.issues.length ? (
              <div className="issue-row">
                {snapshot.issues.slice(0, 3).map((issue) => (
                  <Badge tone="warn" key={`${issue.status}-${issue.detail}`}>
                    {issue.status}: {issue.detail}
                  </Badge>
                ))}
              </div>
            ) : null}

            {run ? (
              <div className="timeline">
                {timelineSteps.map((step, index) => (
                  <TimelineCard
                    key={step.id}
                    step={step}
                    index={index}
                    expanded={expandedSteps.has(step.id)}
                    onToggle={() => toggleStep(step.id)}
                  />
                ))}
              </div>
            ) : (
              <NoRunState
                isDemo={isDemo}
                scenarioText={scenarioText}
                onScenarioText={setScenarioText}
                onRunScenario={() => void runScenario()}
                busy={actionBusy === "scenario"}
              />
            )}

            {rawOpen && snapshot ? (
              <section className="raw-trace">
                <div className="panel-heading compact">
                  <span>Raw Trace</span>
                  <button type="button" onClick={() => setRawOpen(false)}>
                    <X size={14} />
                  </button>
                </div>
                <pre>{JSON.stringify(snapshot.incident?.run ?? snapshot, null, 2)}</pre>
              </section>
            ) : null}
          </section>

          <aside className="evidence-panel">
            <div className="evidence-heading">
              <div>
                <span>Evidence</span>
                <strong>{run ? run.conversation_id : "No conversation"}</strong>
              </div>
            </div>
            <div className="tabs" role="tablist" aria-label="Evidence tabs">
              {([
                ["brief", "Brief"],
                ["citations", "Citations"],
                ["tool-audit", "Tool Audit"],
                ["memory", "Memory"],
                ["triage", "Triage"]
              ] as Array<[EvidenceTab, string]>).map(([id, label]) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={evidenceTab === id}
                  className={evidenceTab === id ? "is-active" : ""}
                  onClick={() => setEvidenceTab(id)}
                  key={id}
                >
                  {label}
                </button>
              ))}
            </div>

            <EvidenceContent
              tab={evidenceTab}
              snapshot={snapshot}
              expandedCitations={expandedCitations}
              onToggleCitations={() => setExpandedCitations((value) => !value)}
              triageNote={triageNote}
              onTriageNote={setTriageNote}
              onSubmitTriage={(status, nextAssigneeUserId, note) =>
                void submitTriage(status, nextAssigneeUserId, note)
              }
              assigneeUserId={assigneeUserId}
              onAssigneeUserId={setAssigneeUserId}
              actorUserId={snapshot?.connection.actorUserId ?? "console_operator"}
              activeAlert={activeAlert}
              incidentBrief={incidentBrief}
              evalReport={evalReport}
              onRunEval={() => void runGoldenEval()}
              onCopyBrief={() => void copyIncidentBrief()}
              copiedBrief={copiedBrief}
              busy={actionBusy}
            />
          </aside>
        </section>

        <footer className="bottombar">
          <StatusPill label="Environment" value={snapshot?.connection.authMode ?? "demo"} ok />
          <StatusPill label="Monitor source" value={snapshot?.monitorSource ?? "event_store"} />
          <StatusPill label="Model" value={run?.llm_calls[0]?.model ?? "not called"} />
          <StatusPill label="User ID" value={run?.user_id ?? snapshot?.connection.actorUserId ?? "user_demo"} />
          <StatusPill label="Session" value={run?.conversation_id ?? "none"} />
          <button
            className="raw-toggle"
            type="button"
            aria-pressed={rawOpen}
            onClick={() => setRawOpen((value) => !value)}
          >
            Show raw trace
            <span className={rawOpen ? "toggle is-on" : "toggle"} />
          </button>
        </footer>
      </section>
    </main>
  );
}

function OpsOverview({
  metrics,
  snapshot,
  evalReport
}: {
  metrics: OpsMetrics;
  snapshot: ConsoleSnapshot | null;
  evalReport: EvalReport | null;
}) {
  return (
    <section className="ops-strip" aria-label="Operations overview">
      <div className="ops-tile">
        <Gauge size={16} />
        <span>P0/P1</span>
        <strong>{metrics.p0p1Alerts}</strong>
      </div>
      <div className="ops-tile">
        <Bell size={16} />
        <span>Active</span>
        <strong>{metrics.activeAlerts}</strong>
      </div>
      <div className="ops-tile">
        <AlertTriangle size={16} />
        <span>New Since Triage</span>
        <strong>{metrics.newSinceTriage}</strong>
      </div>
      <div className="ops-tile">
        <ShieldCheck size={16} />
        <span>Policy</span>
        <strong>{formatRate(metrics.policyComplianceRate)}</strong>
      </div>
      <div className="ops-tile">
        <BookOpen size={16} />
        <span>Grounded</span>
        <strong>{formatRate(metrics.groundedRate)}</strong>
      </div>
      <div className="ops-tile">
        <FileCheck2 size={16} />
        <span>Eval Gate</span>
        <strong>{evalReport ? `${evalReport.passed}/${evalReport.total}` : "not run"}</strong>
      </div>
      <div className={`ops-tile ${metrics.readinessFailed ? "is-bad" : ""}`}>
        <Activity size={16} />
        <span>Ready</span>
        <strong>{snapshot?.ready?.status ?? "unknown"}</strong>
      </div>
    </section>
  );
}

function Rail({
  activeTarget,
  alertCount,
  onSelect
}: {
  activeTarget: WorkspaceMode;
  alertCount: number;
  onSelect: (target: string) => void;
}) {
  const items: Array<[string, string, LucideIcon, number | null]> = [
    ["runs", "Runs", Play, null],
    ["alerts", "Alerts", Bell, alertCount],
    ["tools", "Tools", Wrench, null],
    ["knowledge", "Knowledge", BookOpen, null],
    ["memory", "Memory", Database, null],
    ["settings", "Settings", Settings, null]
  ];
  return (
    <aside className="rail">
      <div className="rail-logo">
        <Layers size={26} />
      </div>
      <nav>
        {items.map(([id, label, Icon, count]) => (
          <button
            className={id === activeTarget ? "is-active" : ""}
            type="button"
            key={id}
            onClick={() => onSelect(id)}
            title={label}
            aria-pressed={id === activeTarget}
          >
            <Icon size={19} />
            <span>{label}</span>
            {count ? <b>{count}</b> : null}
          </button>
        ))}
      </nav>
    </aside>
  );
}

function RunWorkbenchPanel({
  results,
  stats,
  loading,
  error,
  selectedRunId,
  query,
  userId,
  conversationId,
  intent,
  route,
  status,
  errorCode,
  offset,
  onQuery,
  onUserId,
  onConversationId,
  onIntent,
  onRoute,
  onStatus,
  onErrorCode,
  onSubmit,
  onPage,
  onOpenRun
}: {
  results: AgentRunSearchResponse | null;
  stats: RunSearchStats;
  loading: boolean;
  error: string | null;
  selectedRunId: string | null;
  query: string;
  userId: string;
  conversationId: string;
  intent: string;
  route: string;
  status: string;
  errorCode: string;
  offset: number;
  onQuery: (value: string) => void;
  onUserId: (value: string) => void;
  onConversationId: (value: string) => void;
  onIntent: (value: string) => void;
  onRoute: (value: string) => void;
  onStatus: (value: string) => void;
  onErrorCode: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onPage: (offset: number) => void | Promise<void>;
  onOpenRun: (item: AgentRunSearchItem) => void;
}) {
  const items = results?.items ?? [];
  const limit = results?.limit ?? 25;
  const previousOffset = Math.max(0, offset - limit);
  const nextOffset = offset + limit;
  return (
    <aside className="alerts-panel run-workbench">
      <div className="panel-heading">
        <div>
          <span>Run Workbench</span>
          <strong>{stats.total} persisted runs</strong>
        </div>
      </div>

      <form className="run-search-form" onSubmit={onSubmit}>
        <label className="search-control">
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Search run, conversation, message"
            aria-label="Search persisted runs"
          />
        </label>
        <label className="field-label">
          User
          <input value={userId} onChange={(event) => onUserId(event.target.value)} placeholder="user_demo" />
        </label>
        <label className="field-label">
          Conversation
          <input
            value={conversationId}
            onChange={(event) => onConversationId(event.target.value)}
            placeholder="conv_..."
          />
        </label>
        <div className="run-filter-grid">
          <label className="filter-control">
            <SlidersHorizontal size={14} />
            <select value={intent} onChange={(event) => onIntent(event.target.value)} aria-label="Filter runs by intent">
              <option value="">Any intent</option>
              <option value="order_status">Order status</option>
              <option value="refund_or_return">Refund</option>
              <option value="billing">Billing</option>
              <option value="technical_issue">Technical</option>
              <option value="complaint">Complaint</option>
              <option value="account_security">Security</option>
              <option value="general_question">General</option>
            </select>
          </label>
          <label className="filter-control">
            <RouteIcon size={14} />
            <select value={route} onChange={(event) => onRoute(event.target.value)} aria-label="Filter runs by route">
              <option value="">Any route</option>
              <option value="order_agent">Order</option>
              <option value="billing_agent">Billing</option>
              <option value="tech_agent">Tech</option>
              <option value="retention_agent">Retention</option>
              <option value="safety_agent">Safety</option>
              <option value="general_agent">General</option>
              <option value="human">Human</option>
            </select>
          </label>
          <label className="filter-control">
            <Filter size={14} />
            <select value={status} onChange={(event) => onStatus(event.target.value)} aria-label="Filter runs by status">
              <option value="">Any status</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="running">Running</option>
            </select>
          </label>
          <label className="field-label compact">
            Error
            <input
              value={errorCode}
              onChange={(event) => onErrorCode(event.target.value)}
              placeholder="TIMEOUT"
            />
          </label>
        </div>
        <button className="primary-button" type="submit" disabled={loading}>
          {loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
          Search runs
        </button>
      </form>

      <div className="run-search-stats" aria-label="Run search stats">
        <Metric label="Failed" value={String(stats.failedRuns)} />
        <Metric label="Tool fail" value={String(stats.toolFailureRuns)} />
        <Metric label="Human" value={String(stats.humanReviewRuns)} />
        <Metric label="Avg" value={stats.averageDurationMs === null ? "n/a" : formatDurationMs(stats.averageDurationMs)} />
      </div>

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? `${items.length} run search results loaded`}
      </div>

      <div className="run-result-list">
        {loading && !results ? <LoadingBlock /> : null}
        {!loading && results && !items.length ? (
          <PanelEmpty title="No runs found" detail="Try a broader query or clear a filter." />
        ) : null}
        {!results && !loading ? (
          <PanelEmpty title="Search persisted runs" detail="Find runs by conversation, user, route, status, or tool error." />
        ) : null}
        {items.map((item) => (
          <button
            type="button"
            className={`run-result-card ${item.id === selectedRunId ? "is-selected" : ""}`}
            key={item.id}
            onClick={() => onOpenRun(item)}
            aria-pressed={item.id === selectedRunId}
          >
            <div className="run-result-top">
              <Badge tone={statusTone(item.status)}>{item.status}</Badge>
              <time title={item.created_at}>{ageLabel(item.created_at)}</time>
            </div>
            <strong>{item.id}</strong>
            <span>{item.conversation_id}</span>
            <div className="tag-row">
              <Badge>{item.intent ?? "unknown"}</Badge>
              <Badge>{item.route ?? "no route"}</Badge>
              {item.needs_human ? <Badge tone="warn">human</Badge> : null}
              {item.failed_tool_count ? <Badge tone="danger">{item.failed_tool_count} tool fail</Badge> : null}
            </div>
            <div className="run-result-meta">
              <span>{item.user_id}</span>
              <span>{formatDurationMs(item.duration_ms)}</span>
              <span>{item.citation_count} citations</span>
            </div>
            {item.tool_error_codes.length ? (
              <div className="tag-row">
                {item.tool_error_codes.slice(0, 3).map((code) => (
                  <Badge tone="warn" key={code}>
                    {code}
                  </Badge>
                ))}
              </div>
            ) : null}
          </button>
        ))}
      </div>

      {results ? (
        <div className="queue-footer">
          <span>
            {offset + 1}-{offset + items.length} of {results.total}
          </span>
          <div className="pager-actions">
            <button type="button" disabled={loading || offset === 0} onClick={() => void onPage(previousOffset)}>
              Previous
            </button>
            <button type="button" disabled={loading || !results.has_more} onClick={() => void onPage(nextOffset)}>
              Next
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      ) : null}
    </aside>
  );
}

function KnowledgeWorkbenchPanel({
  trace,
  stats,
  loading,
  error,
  query,
  limit,
  currentRetrieval,
  onQuery,
  onLimit,
  onSubmit,
  onSearch,
  onUseCurrent
}: {
  trace: KnowledgeSearchResponse | null;
  stats: KnowledgeSearchStats;
  loading: boolean;
  error: string | null;
  query: string;
  limit: string;
  currentRetrieval: RetrievalTrace | null;
  onQuery: (value: string) => void;
  onLimit: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onSearch: (query?: string, limit?: string) => void | Promise<void>;
  onUseCurrent: (trace: RetrievalTrace) => void;
}) {
  const hits = trace?.selected_context ?? [];
  const stages = Object.entries(trace?.candidates_by_stage ?? {});
  return (
    <aside className="alerts-panel run-workbench knowledge-workbench">
      <div className="panel-heading">
        <div>
          <span>Knowledge Search</span>
          <strong>{stats.selectedChunks} selected chunks</strong>
        </div>
      </div>

      <form className="run-search-form" onSubmit={onSubmit}>
        <label className="search-control">
          <BookOpen size={14} />
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Search policy, shipping, invoice"
            aria-label="Search knowledge base"
          />
        </label>
        <div className="run-filter-grid">
          <label className="filter-control">
            <SlidersHorizontal size={14} />
            <select value={limit} onChange={(event) => onLimit(event.target.value)} aria-label="Knowledge search limit">
              <option value="4">4 chunks</option>
              <option value="8">8 chunks</option>
              <option value="12">12 chunks</option>
              <option value="20">20 chunks</option>
            </select>
          </label>
          <button className="primary-button compact-action" type="submit" disabled={loading || !query.trim()}>
            {loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
            Search
          </button>
        </div>
        {currentRetrieval ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => onUseCurrent(currentRetrieval)}
            disabled={loading}
          >
            <BookOpen size={16} />
            Use run query
          </button>
        ) : null}
      </form>

      <div className="run-search-stats" aria-label="Knowledge retrieval stats">
        <Metric label="Candidates" value={String(stats.candidateCount)} />
        <Metric label="Sources" value={String(stats.sourceCount)} />
        <Metric label="Dropped" value={String(stats.droppedCandidates)} />
        <Metric label="Top score" value={stats.topScore === null ? "n/a" : stats.topScore.toFixed(2)} />
      </div>

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? `${hits.length} knowledge chunks loaded`}
      </div>

      {trace ? (
        <div className="knowledge-diagnostics">
          <section>
            <strong>Rewrite</strong>
            <div className="knowledge-query-list">
              {trace.rewritten_queries.map((item) => (
                <button
                  type="button"
                  key={item}
                  onClick={() => void onSearch(item, limit)}
                  title={`Search ${item}`}
                >
                  {item}
                </button>
              ))}
            </div>
          </section>
          <section>
            <strong>Stages</strong>
            <div className="knowledge-stage-list">
              {stages.length ? (
                stages.map(([name, count]) => (
                  <span key={name}>
                    <b>{name}</b>
                    {count}
                  </span>
                ))
              ) : (
                <span>
                  <b>none</b>
                  0
                </span>
              )}
            </div>
          </section>
        </div>
      ) : null}

      <div className="run-result-list">
        {loading && !trace ? <LoadingBlock /> : null}
        {!loading && trace && !hits.length ? (
          <PanelEmpty title="No chunks returned" detail="Capture this query in the retrieval challenge before changing prompts." />
        ) : null}
        {!trace && !loading ? (
          <PanelEmpty title="Search knowledge base" detail="Run a query or open a run with retrieval evidence." />
        ) : null}
        {hits.map((hit) => (
          <article className="run-result-card knowledge-hit-card" key={hit.chunk_id}>
            <div className="run-result-top">
              <Badge tone={hit.score >= 1 ? "success" : "neutral"}>{hit.score.toFixed(2)}</Badge>
              <span>{hit.source_uri || "no source"}</span>
            </div>
            <strong>{hit.title}</strong>
            <p>{hit.content_snippet}</p>
            <div className="run-result-meta">
              <span>{hit.document_id}</span>
              <span>{hit.chunk_id}</span>
            </div>
          </article>
        ))}
      </div>
    </aside>
  );
}

function ToolAuditWorkbenchPanel({
  results,
  stats,
  loading,
  error,
  selectedTraceId,
  tools,
  toolName,
  actorUserId,
  traceId,
  requestId,
  status,
  errorCode,
  replayed,
  createdAfter,
  createdBefore,
  order,
  onToolName,
  onActorUserId,
  onTraceId,
  onRequestId,
  onStatus,
  onErrorCode,
  onReplayed,
  onCreatedAfter,
  onCreatedBefore,
  onOrder,
  onSubmit,
  onSearch,
  onOpenRecord
}: {
  results: ToolAuditSearchResponse | null;
  stats: ToolAuditStats;
  loading: boolean;
  error: string | null;
  selectedTraceId: string | null;
  tools: ConsoleSnapshot["tools"];
  toolName: string;
  actorUserId: string;
  traceId: string;
  requestId: string;
  status: string;
  errorCode: string;
  replayed: string;
  createdAfter: string;
  createdBefore: string;
  order: "asc" | "desc";
  onToolName: (value: string) => void;
  onActorUserId: (value: string) => void;
  onTraceId: (value: string) => void;
  onRequestId: (value: string) => void;
  onStatus: (value: string) => void;
  onErrorCode: (value: string) => void;
  onReplayed: (value: string) => void;
  onCreatedAfter: (value: string) => void;
  onCreatedBefore: (value: string) => void;
  onOrder: (value: "asc" | "desc") => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onSearch: (overrides?: ToolAuditSearchOverrides) => void | Promise<void>;
  onOpenRecord: (record: ToolAuditRecord) => void;
}) {
  const records = results?.records ?? [];
  const summaryTools = results?.summary.tools ?? [];
  const toolDefinitions = new Map(tools.map((tool) => [tool.name, tool]));
  return (
    <aside className="alerts-panel run-workbench tool-workbench">
      <div className="panel-heading">
        <div>
          <span>Tool Audit</span>
          <strong>{stats.totalCalls} calls in scope</strong>
        </div>
      </div>

      <form className="run-search-form" onSubmit={onSubmit}>
        <label className="search-control">
          <Wrench size={14} />
          <input
            value={toolName}
            onChange={(event) => onToolName(event.target.value)}
            placeholder="shipping.track"
            aria-label="Filter audit by tool name"
          />
        </label>
        <div className="run-filter-grid">
          <label className="filter-control">
            <Filter size={14} />
            <select value={status} onChange={(event) => onStatus(event.target.value)} aria-label="Filter audit by status">
              <option value="">Any status</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
              <option value="skipped">Skipped</option>
            </select>
          </label>
          <label className="filter-control">
            <RefreshCw size={14} />
            <select value={replayed} onChange={(event) => onReplayed(event.target.value)} aria-label="Filter replayed tool calls">
              <option value="">Any replay</option>
              <option value="true">Replayed</option>
              <option value="false">Fresh calls</option>
            </select>
          </label>
          <label className="filter-control">
            <SlidersHorizontal size={14} />
            <select
              value={order}
              onChange={(event) => onOrder(event.target.value === "asc" ? "asc" : "desc")}
              aria-label="Sort audit records"
            >
              <option value="desc">Newest</option>
              <option value="asc">Oldest</option>
            </select>
          </label>
          <label className="field-label compact">
            Error
            <input value={errorCode} onChange={(event) => onErrorCode(event.target.value)} placeholder="TIMEOUT" />
          </label>
        </div>
        <label className="field-label">
          Trace
          <input value={traceId} onChange={(event) => onTraceId(event.target.value)} placeholder="run_..." />
        </label>
        <label className="field-label">
          Actor
          <input value={actorUserId} onChange={(event) => onActorUserId(event.target.value)} placeholder="user_demo" />
        </label>
        <label className="field-label">
          Request
          <input value={requestId} onChange={(event) => onRequestId(event.target.value)} placeholder="req_..." />
        </label>
        <div className="tool-window-grid">
          <label className="field-label compact">
            Since
            <input
              value={createdAfter}
              onChange={(event) => onCreatedAfter(event.target.value)}
              placeholder="2026-07-04T00:00:00Z"
            />
          </label>
          <label className="field-label compact">
            Before
            <input
              value={createdBefore}
              onChange={(event) => onCreatedBefore(event.target.value)}
              placeholder="2026-07-04T12:00:00Z"
            />
          </label>
        </div>
        <button className="primary-button" type="submit" disabled={loading}>
          {loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
          Search audit
        </button>
      </form>

      <div className="run-search-stats" aria-label="Tool audit SLA stats">
        <Metric label="Failed" value={String(stats.failedCalls)} />
        <Metric label="Fail rate" value={formatRate(stats.failureRate)} />
        <Metric label="Replay" value={String(stats.replayedCalls)} />
        <Metric label="Avg" value={stats.averageLatencyMs === null ? "n/a" : formatDurationMs(stats.averageLatencyMs)} />
      </div>

      {summaryTools.length ? (
        <div className="tool-summary-list" aria-label="Tool failure summary">
          {summaryTools.slice(0, 4).map((tool) => (
            <button
              type="button"
              key={tool.tool_name}
              onClick={() => void onSearch({ toolName: tool.tool_name })}
              className={tool.failed_calls ? "tool-summary-row has-failures" : "tool-summary-row"}
            >
              <span>{tool.tool_name}</span>
              <strong>{formatRate(tool.failure_rate)} fail</strong>
              <small>{tool.top_error_code ?? `${tool.total_calls} calls`}</small>
            </button>
          ))}
        </div>
      ) : null}

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? `${records.length} tool audit records loaded`}
      </div>

      <div className="run-result-list">
        {loading && !results ? <LoadingBlock /> : null}
        {!loading && results && !records.length ? (
          <PanelEmpty title="No audit records found" detail="Try a broader tool, trace, actor, or error filter." />
        ) : null}
        {!results && !loading ? (
          <PanelEmpty title="Search persisted audit" detail="Find real tool calls by tool, trace, actor, replay, or error." />
        ) : null}
        {records.map((record) => {
          const definition = toolDefinitions.get(record.tool_name);
          const breachedSla = definition ? record.latency_ms > definition.timeout_ms : false;
          return (
            <button
              type="button"
              className={`run-result-card tool-audit-card ${record.trace_id === selectedTraceId ? "is-selected" : ""}`}
              key={record.id}
              onClick={() => onOpenRecord(record)}
              aria-label={`Open run ${record.trace_id} for ${record.status} ${record.tool_name} audit record`}
              aria-pressed={record.trace_id === selectedTraceId}
            >
              <div className="run-result-top">
                <Badge tone={statusTone(record.status)}>{record.status}</Badge>
                <time title={record.created_at ?? undefined}>{ageLabel(record.created_at)}</time>
              </div>
              <strong>{record.tool_name}</strong>
              <span>{record.trace_id}</span>
              <div className="tag-row">
                {record.error_code ? <Badge tone="warn">{record.error_code}</Badge> : <Badge tone="success">clean</Badge>}
                {record.replayed ? <Badge>replayed</Badge> : null}
                {breachedSla ? <Badge tone="danger">SLA {definition?.timeout_ms}ms</Badge> : null}
              </div>
              <div className="run-result-meta">
                <span>{record.actor_user_id}</span>
                <span>{record.request_id}</span>
                <span>{formatDurationMs(record.latency_ms)}</span>
              </div>
              <small className="hash-line">args {record.argument_hash}</small>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function ContextItem({
  label,
  value,
  detail,
  state,
  icon: Icon
}: {
  label: string;
  value: string;
  detail: string;
  state?: "ok" | "warn";
  icon?: LucideIcon;
}) {
  return (
    <div className="context-item">
      <span>{label}</span>
      <strong>
        {state ? <i className={`state-dot ${state}`} /> : Icon ? <Icon size={16} /> : null}
        {value}
      </strong>
      <small>{detail}</small>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TimelineCard({
  step,
  index,
  expanded,
  onToggle
}: {
  step: TimelineStep;
  index: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <article className={`timeline-row step-${step.status}`}>
      <div className="timeline-marker">
        <span>{index + 1}</span>
      </div>
      <div className="timeline-time">
        <time>{step.time}</time>
        <small>{step.duration}</small>
      </div>
      <div className="step-card">
        <button type="button" className="step-summary" aria-expanded={expanded} onClick={onToggle}>
          <div>
            <span>{step.eyebrow}</span>
            <strong>{step.title}</strong>
          </div>
          <div className="chip-row">
            {step.chips.slice(0, 3).map((chip) => (
              <Badge key={chip}>{chip}</Badge>
            ))}
            {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
          </div>
        </button>
        {expanded ? <div className="step-body">{step.body}</div> : null}
      </div>
    </article>
  );
}

function EvidenceContent({
  tab,
  snapshot,
  expandedCitations,
  onToggleCitations,
  triageNote,
  onTriageNote,
  onSubmitTriage,
  assigneeUserId,
  onAssigneeUserId,
  actorUserId,
  activeAlert,
  incidentBrief,
  evalReport,
  onRunEval,
  onCopyBrief,
  copiedBrief,
  busy
}: {
  tab: EvidenceTab;
  snapshot: ConsoleSnapshot | null;
  expandedCitations: boolean;
  onToggleCitations: () => void;
  triageNote: string;
  onTriageNote: (value: string) => void;
  onSubmitTriage: (status: string, assigneeUserId?: string | null, note?: string) => void;
  assigneeUserId: string;
  onAssigneeUserId: (value: string) => void;
  actorUserId: string;
  activeAlert: MonitorAlert | null;
  incidentBrief: IncidentBrief;
  evalReport: EvalReport | null;
  onRunEval: () => void;
  onCopyBrief: () => void;
  copiedBrief: boolean;
  busy: string | null;
}) {
  const incident = snapshot?.incident ?? null;
  if (!incident) {
    if (tab === "brief") {
      return (
        <IncidentBriefPanel
          brief={incidentBrief}
          snapshot={snapshot}
          activeAlert={activeAlert}
          evalReport={evalReport}
          onRunEval={onRunEval}
          onCopyBrief={onCopyBrief}
          copiedBrief={copiedBrief}
          busy={busy}
        />
      );
    }
    return <PanelEmpty title="No incident selected" detail="Run a scenario or select a monitor alert." />;
  }

  if (tab === "brief") {
    return (
      <IncidentBriefPanel
        brief={incidentBrief}
        snapshot={snapshot}
        activeAlert={activeAlert}
        evalReport={evalReport}
        onRunEval={onRunEval}
        onCopyBrief={onCopyBrief}
        copiedBrief={copiedBrief}
        busy={busy}
      />
    );
  }
  if (tab === "tool-audit") {
    return <ToolAudit records={incident.tool_audit_records} tools={incident.run.tool_results} />;
  }
  if (tab === "memory") {
    return <MemoryPanel incident={incident} rawEvents={snapshot?.rawEvents ?? []} />;
  }
  if (tab === "triage") {
    return (
      <TriagePanel
        events={snapshot?.triageEvents ?? []}
        alertKey={snapshot?.activeAlertKey ?? null}
        note={triageNote}
        onNote={onTriageNote}
        assigneeUserId={assigneeUserId}
        onAssigneeUserId={onAssigneeUserId}
        actorUserId={actorUserId}
        onSubmit={onSubmitTriage}
        busy={busy}
      />
    );
  }
  return (
    <CitationsPanel
      hits={incident.run.retrieval?.selected_context ?? []}
      findings={incident.run.policy_findings}
      monitorEvents={incident.monitor_events}
      expanded={expandedCitations}
      onToggle={onToggleCitations}
    />
  );
}

function IncidentBriefPanel({
  brief,
  snapshot,
  activeAlert,
  evalReport,
  onRunEval,
  onCopyBrief,
  copiedBrief,
  busy
}: {
  brief: IncidentBrief;
  snapshot: ConsoleSnapshot | null;
  activeAlert: MonitorAlert | null;
  evalReport: EvalReport | null;
  onRunEval: () => void;
  onCopyBrief: () => void;
  copiedBrief: boolean;
  busy: string | null;
}) {
  const run = snapshot?.incident?.run ?? null;
  const readinessFailures = snapshot?.ready?.checks.filter((check) => check.status === "failed") ?? [];
  return (
    <div className="evidence-stack">
      <section className="evidence-card brief-card">
        <div className="evidence-card-head">
          <div>
            <span>Incident Brief</span>
            <strong>{brief.title}</strong>
          </div>
          <Badge tone={brief.riskLabel === "P0" || brief.riskLabel === "critical" ? "danger" : "warn"}>
            {brief.riskLabel}
          </Badge>
        </div>
        <p className="summary-copy">{brief.summary}</p>
        <div className="brief-grid">
          <Metric label="Owner" value={activeAlert?.assignee_user_id ?? "unassigned"} />
          <Metric label="Alert status" value={activeAlert?.status ?? "none"} />
          <Metric label="Run status" value={run?.status ?? "none"} />
          <Metric label="Eval gate" value={evalReport ? `${evalReport.passed}/${evalReport.total}` : "not run"} />
        </div>
        <div className="brief-actions">
          <button className="secondary-button" type="button" onClick={onCopyBrief} disabled={!snapshot}>
            <Copy size={16} />
            {copiedBrief ? "Copied" : "Copy brief"}
          </button>
          <button className="primary-button" type="button" onClick={onRunEval} disabled={busy === "eval"}>
            {busy === "eval" ? <Loader2 className="spin" size={16} /> : <FileCheck2 size={16} />}
            Run eval gate
          </button>
        </div>
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Recommended Next Actions</strong>
        </div>
        <div className="action-list">
          {brief.recommendedActions.map((action) => (
            <div key={action}>
              <Check size={15} />
              <span>{action}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Production Preflight</strong>
        </div>
        <div className="readiness-list">
          {(snapshot?.ready?.checks ?? []).map((check) => (
            <div key={check.name}>
              <Badge tone={check.status === "failed" ? "danger" : check.status === "ok" ? "success" : "neutral"}>
                {check.status}
              </Badge>
              <strong>{check.name}</strong>
              <span>{check.detail}</span>
            </div>
          ))}
          {!snapshot?.ready?.checks.length ? (
            <PanelEmpty title="Readiness unavailable" detail="The Agent API did not return readiness checks." />
          ) : null}
        </div>
        {readinessFailures.length ? (
          <p className="muted">Resolve readiness failures before promoting this environment.</p>
        ) : null}
      </section>

      {evalReport ? (
        <section className="evidence-card">
          <div className="evidence-card-head">
            <strong>Eval Failures</strong>
          </div>
          {evalReport.results.some((result) => !result.passed) ? (
            evalReport.results
              .filter((result) => !result.passed)
              .map((result) => (
                <article className="eval-row" key={result.case_id}>
                  <Badge tone="danger">{Math.round(result.score * 100)}%</Badge>
                  <div>
                    <strong>{result.case_id}</strong>
                    <span>{result.failures.join("; ") || "No failure detail returned."}</span>
                  </div>
                </article>
              ))
          ) : (
            <PanelEmpty title="Eval gate passed" detail="All bundled golden cases passed in this environment." />
          )}
        </section>
      ) : null}
    </div>
  );
}

function CitationsPanel({
  hits,
  findings,
  monitorEvents,
  expanded,
  onToggle
}: {
  hits: RetrievalHit[];
  findings: PolicyFinding[];
  monitorEvents: MonitorEvent[];
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Citations ({hits.length} used)</strong>
          <button type="button" onClick={onToggle}>
            {expanded ? "Collapse" : "Expand all"}
            {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
        </div>
        {hits.length ? (
          hits.map((hit, index) => (
            <article className="citation-row" key={`${hit.document_id}-${hit.chunk_id}`}>
              <div className="index-badge">{index + 1}</div>
              <div>
                <strong>{hit.title}</strong>
                <span>{hit.source_uri}</span>
                <p>{expanded ? hit.content : truncate(hit.content, 170)}</p>
              </div>
              <Metric label="Score" value={hit.score.toFixed(2)} />
            </article>
          ))
        ) : (
          <PanelEmpty title="No citations" detail="This run did not return retrieval context." />
        )}
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Policy Findings ({findings.length})</strong>
        </div>
        {findings.length ? (
          findings.map((finding) => (
            <article className={`policy-box risk-${finding.risk_level}`} key={finding.code}>
              <AlertTriangle size={18} />
              <div>
                <strong>{finding.code}</strong>
                <span>{finding.message}</span>
                <small>
                  {finding.risk_level} · block={String(finding.should_block)} · escalate=
                  {String(finding.should_escalate)}
                </small>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No policy findings" detail="The policy engine did not flag this run." />
        )}
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Monitor Signal</strong>
        </div>
        {monitorEvents.length ? (
          monitorEvents.map((event) => (
            <article className="signal-row" key={event.id}>
              <Badge tone={riskTone(event.risk_level)}>{event.risk_level}</Badge>
              <strong>{event.summary}</strong>
              <span>
                grounded={String(event.grounded)} · compliant={String(event.policy_compliant)}
              </span>
              {event.failure_types.length ? (
                <div className="tag-row">
                  {event.failure_types.map((failure) => (
                    <Badge tone="warn" key={failure}>
                      {failure}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </article>
          ))
        ) : (
          <PanelEmpty title="No monitor events" detail="The online monitor has not reviewed this run." />
        )}
      </section>
    </div>
  );
}

function ToolAudit({
  records,
  tools
}: {
  records: ToolAuditRecord[];
  tools: ToolResult[];
}) {
  const visible = records.length ? records : [];
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Tool Audit ({visible.length})</strong>
        </div>
        {visible.length ? (
          visible.map((record) => (
            <article className="audit-row" key={record.id}>
              <Wrench size={17} />
              <div>
                <strong>{record.tool_name}</strong>
                <span>
                  {record.status} · {record.latency_ms}ms · replayed={String(record.replayed)}
                </span>
                <small>{record.error_code ?? record.request_id}</small>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No persisted audit rows" detail="Tool results are still shown below." />
        )}
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Run Tool Results ({tools.length})</strong>
        </div>
        {tools.length ? (
          tools.map((tool) => (
            <article className="tool-result-row" key={tool.id}>
              <Badge tone={tool.status === "success" ? "success" : "warn"}>{tool.status}</Badge>
              <div>
                <strong>{tool.name}</strong>
                <span>{tool.error_code ?? `${tool.latency_ms}ms`}</span>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No tool calls" detail="This route did not invoke tools." />
        )}
      </section>
    </div>
  );
}

function MemoryPanel({
  incident,
  rawEvents
}: {
  incident: IncidentRunBundle;
  rawEvents: StoredEvent[];
}) {
  const replay = incident.memory_replay;
  if (!replay) {
    return <PanelEmpty title="Memory replay unavailable" detail="No replayable event stream was returned." />;
  }
  const facts = Object.entries(replay.state.facts);
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Replay Summary</strong>
        </div>
        <div className="memory-grid">
          <Metric label="Events" value={String(replay.event_count)} />
          <Metric label="Messages" value={String(replay.replayed_message_count)} />
          <Metric label="Runs" value={String(replay.replayed_run_count)} />
          <Metric label="Ignored" value={String(replay.ignored_event_count)} />
        </div>
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Conversation Memory</strong>
        </div>
        <p className="summary-copy">{replay.state.working_summary || "No working summary stored."}</p>
        <div className="key-value-list">
          {facts.length ? (
            facts.map(([key, value]) => (
              <div key={key}>
                <span>{key}</span>
                <strong>{stringifyValue(value)}</strong>
              </div>
            ))
          ) : (
            <span className="muted">No facts captured.</span>
          )}
        </div>
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Append-only Events ({rawEvents.length})</strong>
        </div>
        <div className="event-list">
          {rawEvents.slice(0, 8).map((event) => (
            <div key={event.id}>
              <Badge>{event.event_type}</Badge>
              <span>{formatTime(event.created_at)}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function TriagePanel({
  events,
  alertKey,
  note,
  onNote,
  assigneeUserId,
  onAssigneeUserId,
  actorUserId,
  onSubmit,
  busy
}: {
  events: Array<{
    id: string;
    status: string | null;
    actor_user_id: string;
    assignee_user_id: string | null;
    note: string;
    created_at: string;
  }>;
  alertKey: string | null;
  note: string;
  onNote: (value: string) => void;
  assigneeUserId: string;
  onAssigneeUserId: (value: string) => void;
  actorUserId: string;
  onSubmit: (status: string, assigneeUserId?: string | null, note?: string) => void;
  busy: string | null;
}) {
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Triage Action</strong>
        </div>
        <label className="field-label">
          Assignee
          <input
            value={assigneeUserId}
            onChange={(event) => onAssigneeUserId(event.target.value)}
            placeholder="console_operator"
          />
        </label>
        <textarea
          value={note}
          onChange={(event) => onNote(event.target.value)}
          placeholder="Add an operator note"
          rows={4}
        />
        <div className="triage-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={!alertKey || busy === "investigating"}
            onClick={() => onSubmit("investigating", actorUserId, "Assigned to current console operator")}
          >
            Assign to me
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!alertKey || busy === "investigating"}
            onClick={() => onSubmit("investigating", assigneeUserId || null)}
          >
            Investigating
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!alertKey || busy === "resolved"}
            onClick={() => onSubmit("resolved", assigneeUserId || null)}
          >
            Resolve
          </button>
        </div>
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>History ({events.length})</strong>
        </div>
        {events.length ? (
          events.map((event) => (
            <article className="triage-row" key={event.id}>
              <Badge tone={event.status === "resolved" ? "success" : "warn"}>
                {event.status ?? "note"}
              </Badge>
              <div>
                <strong>{event.actor_user_id}</strong>
                <span>{formatTime(event.created_at)}</span>
                <p>{event.note || "No note"}</p>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No triage history" detail="No operator has updated this alert yet." />
        )}
      </section>
    </div>
  );
}

function EmptyQueue({
  isDemo,
  scenarioText,
  onScenarioText,
  onRunScenario,
  busy
}: {
  isDemo: boolean;
  scenarioText: string;
  onScenarioText: (value: string) => void;
  onRunScenario: () => void;
  busy: boolean;
}) {
  return (
    <div className="empty-queue">
      <ShieldCheck size={25} />
      <strong>No monitor alerts</strong>
      <span>The backend returned an empty alert queue.</span>
      <textarea
        value={scenarioText}
        onChange={(event) => onScenarioText(event.target.value)}
        disabled={!isDemo}
        rows={4}
      />
      <button className="primary-button" type="button" disabled={!isDemo || busy} onClick={onRunScenario}>
        {busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
        Run local scenario
      </button>
    </div>
  );
}

function NoRunState(props: {
  isDemo: boolean;
  scenarioText: string;
  onScenarioText: (value: string) => void;
  onRunScenario: () => void;
  busy: boolean;
}) {
  return (
    <section className="no-run">
      <ClipboardList size={34} />
      <h2>No trace selected</h2>
      <p>The console is connected, but the backend did not return a run for the current selection.</p>
      <textarea
        value={props.scenarioText}
        onChange={(event) => props.onScenarioText(event.target.value)}
        disabled={!props.isDemo}
        rows={3}
      />
      <button
        className="primary-button"
        type="button"
        disabled={!props.isDemo || props.busy}
        onClick={props.onRunScenario}
      >
        {props.busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
        Run local scenario
      </button>
    </section>
  );
}

function LoadingBlock() {
  return (
    <div className="loading-block">
      <Loader2 className="spin" size={24} />
      <span>Loading agent events</span>
    </div>
  );
}

function PanelEmpty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="panel-empty">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function Badge({
  children,
  tone = "neutral"
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warn" | "danger";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function StatusPill({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <span className="status-pill">
      <small>{label}</small>
      {ok ? <i className="state-dot ok" /> : null}
      <strong>{value}</strong>
    </span>
  );
}

function buildTimeline(incident: IncidentRunBundle | null): TimelineStep[] {
  const run = incident?.run;
  const messages = incident?.memory_replay?.state.messages ?? [];
  const userMessage = [...messages].reverse().find((message) => message.role === "user");
  const assistantMessage = [...messages].reverse().find((message) => message.role === "assistant");

  if (!run) {
    return [];
  }

  const span = (name: string) => run.spans.find((item) => item.name === name);
  const toolFailures = run.tool_results.filter((tool) => tool.status !== "success");
  const findings = run.policy_findings.filter((finding) => finding.should_block || finding.should_escalate);

  return [
    {
      id: "message",
      title: userMessage ? truncate(userMessage.content, 96) : "No user message event",
      eyebrow: "Message",
      time: formatTime(userMessage?.created_at ?? run.created_at),
      duration: spanDuration(span("policy.input_check")),
      status: userMessage ? "ok" : "empty",
      chips: userMessage?.metadata.redacted ? ["redacted"] : ["received"],
      body: (
        <div className="detail-block">
          <p>{userMessage?.content ?? "The event stream did not include a user message."}</p>
          <KeyValues values={userMessage?.metadata ?? {}} />
        </div>
      )
    },
    {
      id: "intent",
      title: run.intent?.primary ?? "Intent unavailable",
      eyebrow: "Intent",
      time: formatTime(span("intent.detect")?.started_at ?? run.created_at),
      duration: spanDuration(span("intent.detect")),
      status: run.intent ? "ok" : "empty",
      chips: run.intent ? [`confidence ${run.intent.confidence.toFixed(2)}`, run.intent.sentiment] : [],
      body: (
        <div className="detail-block">
          <p>{run.intent?.rationale || "No intent rationale returned."}</p>
          <KeyValues
            values={{
              entities: run.intent?.entities ?? {},
              missing_slots: run.intent?.missing_slots ?? [],
              urgency: run.intent?.urgency ?? "unknown"
            }}
          />
        </div>
      )
    },
    {
      id: "route",
      title: run.route?.target ?? "Route unavailable",
      eyebrow: "Route",
      time: formatTime(span("route.decide")?.started_at ?? run.created_at),
      duration: spanDuration(span("route.decide")),
      status: run.route?.needs_human ? "warn" : run.route ? "ok" : "empty",
      chips: run.route ? [run.route.needs_human ? "human review" : "automated"] : [],
      body: (
        <div className="detail-block">
          <p>{run.route?.reason ?? "No routing decision returned."}</p>
          <div className="tag-row">
            {(run.route?.allowed_tools ?? []).map((tool) => (
              <Badge key={tool}>{tool}</Badge>
            ))}
          </div>
        </div>
      )
    },
    {
      id: "tools",
      title: `${run.tool_results.length} tool call${run.tool_results.length === 1 ? "" : "s"}`,
      eyebrow: "Tools",
      time: formatTime(span("tool.invoke")?.started_at ?? run.created_at),
      duration: `${sumLatency(run.tool_results)}ms`,
      status: toolFailures.length ? "error" : run.tool_results.length ? "ok" : "empty",
      chips: toolFailures.length ? [`${toolFailures.length} failed`] : [`${run.tool_results.length} total`],
      body: (
        <div className="tool-list">
          {run.tool_results.length ? (
            run.tool_results.map((tool) => (
              <div className="tool-line" key={tool.id}>
                <Badge tone={tool.status === "success" ? "success" : "warn"}>{tool.status}</Badge>
                <strong>{tool.name}</strong>
                <span>{tool.error_code ?? `${tool.latency_ms}ms`}</span>
              </div>
            ))
          ) : (
            <span className="muted">No tools were invoked for this route.</span>
          )}
        </div>
      )
    },
    {
      id: "retrieval",
      title: run.retrieval?.query ?? "No retrieval trace",
      eyebrow: "Retrieval",
      time: formatTime(span("knowledge.retrieve")?.started_at ?? run.created_at),
      duration: spanDuration(span("knowledge.retrieve")),
      status: run.retrieval ? "ok" : "empty",
      chips: run.retrieval
        ? [`${run.retrieval.selected_context.length} chunks`, `${run.retrieval.selected_sources.length} sources`]
        : [],
      body: (
        <div className="detail-block">
          <KeyValues values={run.retrieval?.candidates_by_stage ?? {}} />
          <div className="tag-row">
            {(run.retrieval?.selected_sources ?? []).map((source) => (
              <Badge key={source}>{source}</Badge>
            ))}
          </div>
        </div>
      )
    },
    {
      id: "answer",
      title: assistantMessage ? truncate(assistantMessage.content, 96) : "No assistant message event",
      eyebrow: "Answer",
      time: formatTime(assistantMessage?.created_at ?? run.completed_at ?? run.created_at),
      duration: run.llm_calls[0] ? `${run.llm_calls[0].latency_ms}ms` : "n/a",
      status: findings.length ? "warn" : assistantMessage ? "ok" : "empty",
      chips: run.llm_calls[0]
        ? [run.llm_calls[0].model, run.llm_calls[0].fallback_used ? "fallback" : "primary"]
        : [],
      body: (
        <div className="detail-block">
          <p>{assistantMessage?.content ?? "No assistant message was replayed from the event store."}</p>
          {run.llm_calls[0] ? (
            <KeyValues
              values={{
                provider: run.llm_calls[0].provider,
                input_tokens: run.llm_calls[0].input_tokens,
                output_tokens: run.llm_calls[0].output_tokens,
                cost_usd: run.llm_calls[0].cost_usd
              }}
            />
          ) : null}
        </div>
      )
    },
    {
      id: "monitor",
      title: incident.monitor_events[0]?.summary ?? "No monitor review",
      eyebrow: "Monitor Review",
      time: formatTime(incident.monitor_events[0]?.timestamp ?? run.completed_at ?? run.created_at),
      duration: "online",
      status: incident.monitor_events.some((event) => event.needs_human_review) ? "warn" : "ok",
      chips: incident.monitor_events[0]
        ? [incident.monitor_events[0].risk_level, `${incident.monitor_events.length} event(s)`]
        : [],
      body: (
        <div className="detail-block">
          {incident.monitor_events.length ? (
            incident.monitor_events.map((event) => (
              <div className="monitor-line" key={event.id}>
                <Badge tone={riskTone(event.risk_level)}>{event.risk_level}</Badge>
                <span>{event.summary}</span>
              </div>
            ))
          ) : (
            <span className="muted">No monitor review was returned for this run.</span>
          )}
        </div>
      )
    }
  ];
}

function KeyValues({ values }: { values: Record<string, JsonValue> }) {
  const entries = Object.entries(values);
  if (!entries.length) {
    return null;
  }
  return (
    <div className="key-values">
      {entries.map(([key, value]) => (
        <div key={key}>
          <span>{key}</span>
          <strong>{stringifyValue(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function stringifyValue(value: JsonValue): string {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function toKnowledgeSearchResponse(trace: RetrievalTrace): KnowledgeSearchResponse {
  return {
    query: trace.query,
    rewritten_queries: trace.rewritten_queries,
    selected_sources: trace.selected_sources,
    candidates_by_stage: trace.candidates_by_stage,
    dropped_candidates: trace.dropped_candidates,
    selected_context: trace.selected_context.map((hit) => ({
      document_id: hit.document_id,
      chunk_id: hit.chunk_id,
      title: hit.title,
      score: hit.score,
      source_uri: hit.source_uri,
      content_snippet: snippet(hit.content, 500)
    }))
  };
}

function snippet(value: string, max: number) {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > max ? `${compact.slice(0, max - 3)}...` : compact;
}

function truncate(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}...` : value;
}

function formatRate(value: number) {
  return `${Math.round(value * 100)}%`;
}

function ageLabel(value: string | null | undefined) {
  if (!value) {
    return "n/a";
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "n/a";
  }
  const diffMs = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) {
    return "now";
  }
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h`;
  }
  return `${Math.floor(hours / 24)}d`;
}

function formatTime(value: string | null | undefined) {
  if (!value) {
    return "n/a";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "n/a";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function spanDuration(span: TraceSpan | undefined) {
  if (!span?.ended_at) {
    return "n/a";
  }
  const start = new Date(span.started_at).getTime();
  const end = new Date(span.ended_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) {
    return "n/a";
  }
  const ms = Math.max(0, end - start);
  return ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function formatDurationMs(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return value > 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}

function runDuration(run: AgentRunTrace | null) {
  if (!run?.completed_at) {
    return run ? "running" : "n/a";
  }
  const start = new Date(run.created_at).getTime();
  const end = new Date(run.completed_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) {
    return "n/a";
  }
  const ms = Math.max(0, end - start);
  return ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function sumLatency(tools: ToolResult[]) {
  return tools.reduce((total, tool) => total + tool.latency_ms, 0);
}

function statusTone(status: string | undefined): "neutral" | "success" | "warn" | "danger" {
  if (status === "completed" || status === "success") {
    return "success";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "skipped") {
    return "warn";
  }
  return "neutral";
}

function riskTone(risk: string): "neutral" | "success" | "warn" | "danger" {
  if (risk === "critical" || risk === "high") {
    return "danger";
  }
  if (risk === "medium") {
    return "warn";
  }
  if (risk === "low") {
    return "success";
  }
  return "neutral";
}

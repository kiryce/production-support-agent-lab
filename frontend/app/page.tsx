"use client";

import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Rocket,
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
  buildAlertDispatchResultStats,
  buildIncidentBrief,
  canCloseAlertDelivery,
  canReplayAlertDelivery,
  buildKnowledgeSearchStats,
  buildMonitorAlertDeliveryStats,
  buildMonitorDrilldownStats,
  buildMonitorTriageHealthStats,
  buildOpsMetrics,
  buildPromotionGateStats,
  buildRunSearchStats,
  buildToolAuditStats,
  deliveryStatusTone,
  filterAndSortAlerts,
  formatEvalStatus,
  incidentBriefFromResponse,
  type AlertSort,
  type AlertStatusFilter,
  type IncidentBrief,
  type KnowledgeSearchStats,
  type MonitorAlertDeliveryStats,
  type MonitorDrilldownUiStats,
  type MonitorTriageHealthStats,
  type OpsMetrics,
  type RunSearchStats,
  type ToolAuditStats
} from "@/src/shared/ops";
import {
  DEFAULT_CONSOLE_URL_STATE,
  parseConsoleState,
  serializeConsoleState,
  type AlertSeverityFilter,
  type ConsoleUrlState,
  type EvidenceTab,
  type WorkspaceMode
} from "@/src/shared/consoleState";
import type {
  AgentRunSearchItem,
  AgentRunSearchResponse,
  AgentRunTrace,
  AgentFeedback,
  FeedbackReviewEvent,
  FeedbackReviewQueueStatus,
  FeedbackReviewStatus,
  AlertDispatchReport,
  AlertDeliveryRecord,
  AlertDeliveryStatus,
  ConsoleSnapshot,
  EvalGateRecord,
  EvalReport,
  EventStoreRetentionReport,
  FeedbackSearchResponse,
  IncidentBriefResponse,
  IncidentRunBundle,
  JsonValue,
  KnowledgeSearchResponse,
  MemoryReplayResult,
  MonitorAlert,
  MonitorDrilldownResponse,
  MonitorEvent,
  OperationsAutomationAction,
  OperationsAutomationPlan,
  PolicyFinding,
  PromotionDecision,
  PromotionDecisionRecord,
  PromotionGateResponse,
  RegressionDraftResponse,
  RetrievalHit,
  RetrievalTrace,
  SloObjectiveResult,
  SloReportResponse,
  SQLiteBackupReport,
  StoredEvent,
  ToolAuditRecord,
  ToolAuditSearchResponse,
  ToolResult,
  TraceSpan
} from "@/src/shared/types";

const LOCAL_SCENARIO =
  "My order A1001 headphones arrived broken. Can I return them or get help?";

const DEFAULT_MONITOR_DRILLDOWN_FILTERS: MonitorDrilldownFilters = {
  alertKey: null,
  intent: "",
  riskLevel: "",
  failureType: "",
  needsHumanReview: "",
  grounded: "",
  policyCompliant: "",
  includeHealthy: false,
  limit: "50"
};

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

type FeedbackSearchOverrides = Partial<{
  rating: string;
  runId: string;
  userId: string;
  conversationId: string;
  createdAfter: string;
  createdBefore: string;
  limit: string;
  order: "asc" | "desc";
}>;

type AlertWorkbenchView = "queue" | "drilldown" | "delivery";
type AlertDeliveryStatusFilter = AlertDeliveryStatus | "all";

type MonitorDrilldownFilters = {
  alertKey: string | null;
  intent: string;
  riskLevel: string;
  failureType: string;
  needsHumanReview: string;
  grounded: string;
  policyCompliant: string;
  includeHealthy: boolean;
  limit: string;
};

type MonitorDrilldownOverrides = Partial<MonitorDrilldownFilters>;

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
  const feedbackReviewRequestId = useRef(0);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(DEFAULT_CONSOLE_URL_STATE.runId);
  const [selectedAlertKey, setSelectedAlertKey] = useState<string | null>(DEFAULT_CONSOLE_URL_STATE.alertKey);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>(DEFAULT_CONSOLE_URL_STATE.workspace);
  const [runQuery, setRunQuery] = useState(DEFAULT_CONSOLE_URL_STATE.runId ?? "");
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
  const [memoryConversationId, setMemoryConversationId] = useState("");
  const [memoryLimit, setMemoryLimit] = useState("0");
  const [memoryReplay, setMemoryReplay] = useState<MemoryReplayResult | null>(null);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [feedbackRating, setFeedbackRating] = useState("");
  const [feedbackRunId, setFeedbackRunId] = useState("");
  const [feedbackUserId, setFeedbackUserId] = useState("");
  const [feedbackConversationId, setFeedbackConversationId] = useState("");
  const [feedbackCreatedAfter, setFeedbackCreatedAfter] = useState("");
  const [feedbackCreatedBefore, setFeedbackCreatedBefore] = useState("");
  const [feedbackLimit, setFeedbackLimit] = useState("50");
  const [feedbackOrder, setFeedbackOrder] = useState<"asc" | "desc">("desc");
  const [feedbackResults, setFeedbackResults] = useState<FeedbackSearchResponse | null>(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState<string | null>(null);
  const [feedbackReviews, setFeedbackReviews] = useState<FeedbackReviewEvent[]>([]);
  const [feedbackReviewStatus, setFeedbackReviewStatus] =
    useState<FeedbackReviewStatus>("acknowledged");
  const [feedbackReviewAssignee, setFeedbackReviewAssignee] = useState("");
  const [feedbackReviewNote, setFeedbackReviewNote] = useState("");
  const [feedbackReviewLoadingId, setFeedbackReviewLoadingId] = useState<string | null>(null);
  const [feedbackReviewError, setFeedbackReviewError] = useState<string | null>(null);
  const [eventBackupLabel, setEventBackupLabel] = useState("manual");
  const [eventBackupReport, setEventBackupReport] = useState<SQLiteBackupReport | null>(null);
  const [eventOpsBusy, setEventOpsBusy] = useState<string | null>(null);
  const [eventOpsError, setEventOpsError] = useState<string | null>(null);
  const [eventRetentionReport, setEventRetentionReport] = useState<EventStoreRetentionReport | null>(null);
  const [eventRetentionPreviewKey, setEventRetentionPreviewKey] = useState<string | null>(null);
  const [eventRetentionDays, setEventRetentionDays] = useState("365");
  const [toolAuditRetentionDays, setToolAuditRetentionDays] = useState("180");
  const [idempotencyRetentionDays, setIdempotencyRetentionDays] = useState("30");
  const [alertDeliveryRetentionDays, setAlertDeliveryRetentionDays] = useState("90");
  const [retentionIncludeEvents, setRetentionIncludeEvents] = useState(false);
  const [retentionVacuum, setRetentionVacuum] = useState(false);
  const [retentionApplyConfirmed, setRetentionApplyConfirmed] = useState(false);
  const [promotionTargetVersion, setPromotionTargetVersion] = useState("agent-next");
  const [promotionDecision, setPromotionDecision] = useState<PromotionDecision>("deferred");
  const [promotionDecisionNote, setPromotionDecisionNote] = useState("");
  const [promotionOverrideBlocked, setPromotionOverrideBlocked] = useState(false);
  const [promotionOverrideReason, setPromotionOverrideReason] = useState("");
  const [auditExportLimit, setAuditExportLimit] = useState("1000");
  const [auditExportIncludeEvents, setAuditExportIncludeEvents] = useState(true);
  const [auditExportIncludeToolAudit, setAuditExportIncludeToolAudit] = useState(true);
  const [auditExportStatus, setAuditExportStatus] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState(DEFAULT_CONSOLE_URL_STATE.severity);
  const [statusFilter, setStatusFilter] = useState<AlertStatusFilter>(DEFAULT_CONSOLE_URL_STATE.status);
  const [queueQuery, setQueueQuery] = useState(DEFAULT_CONSOLE_URL_STATE.query);
  const [queueSort, setQueueSort] = useState<AlertSort>(DEFAULT_CONSOLE_URL_STATE.sort);
  const [onlyNewAlerts, setOnlyNewAlerts] = useState(DEFAULT_CONSOLE_URL_STATE.onlyNew);
  const [alertWorkbenchView, setAlertWorkbenchView] = useState<AlertWorkbenchView>("queue");
  const [deliveryStatusFilter, setDeliveryStatusFilter] = useState<AlertDeliveryStatusFilter>("dead");
  const [alertDeliveries, setAlertDeliveries] = useState<AlertDeliveryRecord[]>([]);
  const [alertDeliveriesLoading, setAlertDeliveriesLoading] = useState(false);
  const [alertDeliveriesError, setAlertDeliveriesError] = useState<string | null>(null);
  const [alertDeliveryActionBusy, setAlertDeliveryActionBusy] = useState<string | null>(null);
  const [alertDeliveryDispatchReport, setAlertDeliveryDispatchReport] =
    useState<AlertDispatchReport | null>(null);
  const [monitorFilters, setMonitorFilters] = useState<MonitorDrilldownFilters>(
    DEFAULT_MONITOR_DRILLDOWN_FILTERS
  );
  const [monitorDrilldown, setMonitorDrilldown] = useState<MonitorDrilldownResponse | null>(null);
  const [monitorDrilldownLoading, setMonitorDrilldownLoading] = useState(false);
  const [monitorDrilldownError, setMonitorDrilldownError] = useState<string | null>(null);
  const [regressionDraft, setRegressionDraft] = useState<RegressionDraftResponse | null>(null);
  const [regressionDraftLoadingId, setRegressionDraftLoadingId] = useState<string | null>(null);
  const [regressionDraftError, setRegressionDraftError] = useState<string | null>(null);
  const [copiedRegressionDraft, setCopiedRegressionDraft] = useState(false);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>(DEFAULT_CONSOLE_URL_STATE.tab);
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
  const [urlStateReady, setUrlStateReady] = useState(false);

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
    if (typeof window === "undefined") {
      return;
    }
    const nextState = readInitialConsoleState();
    setSelectedRunId(nextState.runId);
    setSelectedAlertKey(nextState.alertKey);
    setWorkspaceMode(nextState.workspace);
    setEvidenceTab(nextState.tab);
    setSeverityFilter(nextState.severity);
    setStatusFilter(nextState.status);
    setQueueQuery(nextState.query);
    setQueueSort(nextState.sort);
    setOnlyNewAlerts(nextState.onlyNew);
    setRunQuery(nextState.runId ?? "");
    setUrlStateReady(true);
    void loadSnapshot({ runId: nextState.runId, alertKey: nextState.alertKey });
  }, [loadSnapshot]);

  useEffect(() => {
    if (!urlStateReady) {
      return;
    }
    syncConsoleUrl({
      runId: selectedRunId,
      alertKey: selectedAlertKey,
      workspace: workspaceMode,
      tab: evidenceTab,
      severity: severityFilter,
      status: statusFilter,
      query: queueQuery,
      sort: queueSort,
      onlyNew: onlyNewAlerts
    });
  }, [
    evidenceTab,
    onlyNewAlerts,
    queueQuery,
    queueSort,
    selectedAlertKey,
    selectedRunId,
    severityFilter,
    statusFilter,
    workspaceMode,
    urlStateReady
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    const handlePopState = () => {
      const nextState = readInitialConsoleState();
      setSelectedRunId(nextState.runId);
      setSelectedAlertKey(nextState.alertKey);
      setWorkspaceMode(nextState.workspace);
      setEvidenceTab(nextState.tab);
      setSeverityFilter(nextState.severity);
      setStatusFilter(nextState.status);
      setQueueQuery(nextState.query);
      setQueueSort(nextState.sort);
      setOnlyNewAlerts(nextState.onlyNew);
      setRunQuery(nextState.runId ?? "");
      void loadSnapshot({ runId: nextState.runId, alertKey: nextState.alertKey });
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
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
  const triageHealthStats = useMemo<MonitorTriageHealthStats>(
    () => buildMonitorTriageHealthStats(snapshot?.triageMetrics ?? null),
    [snapshot?.triageMetrics]
  );
  const alertDeliveryStats = useMemo<MonitorAlertDeliveryStats>(
    () => buildMonitorAlertDeliveryStats(snapshot?.monitorAlertDelivery ?? null),
    [snapshot?.monitorAlertDelivery]
  );

  const incidentBrief = useMemo<IncidentBrief>(
    () =>
      snapshot?.incidentBrief
        ? incidentBriefFromResponse(snapshot.incidentBrief)
        : buildIncidentBrief(snapshot, activeAlert, evalReport),
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

  const monitorDrilldownStats = useMemo<MonitorDrilldownUiStats>(
    () => buildMonitorDrilldownStats(monitorDrilldown),
    [monitorDrilldown]
  );
  const eventRetentionRequestKey = useMemo(
    () =>
      buildEventRetentionRequestKey({
        eventRetentionDays,
        toolAuditRetentionDays,
        idempotencyRetentionDays,
        alertDeliveryRetentionDays,
        includeEvents: retentionIncludeEvents,
        vacuum: retentionVacuum
      }),
    [
      alertDeliveryRetentionDays,
      eventRetentionDays,
      idempotencyRetentionDays,
      retentionIncludeEvents,
      retentionVacuum,
      toolAuditRetentionDays
    ]
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

  const searchFeedback = useCallback(async (overrides: FeedbackSearchOverrides = {}) => {
    const values = {
      rating: overrides.rating ?? feedbackRating,
      runId: overrides.runId ?? feedbackRunId,
      userId: overrides.userId ?? feedbackUserId,
      conversationId: overrides.conversationId ?? feedbackConversationId,
      createdAfter: overrides.createdAfter ?? feedbackCreatedAfter,
      createdBefore: overrides.createdBefore ?? feedbackCreatedBefore,
      limit: overrides.limit ?? feedbackLimit,
      order: overrides.order ?? feedbackOrder
    };
    if (overrides.runId !== undefined) {
      setFeedbackRunId(overrides.runId);
    }
    if (overrides.conversationId !== undefined) {
      setFeedbackConversationId(overrides.conversationId);
    }
    setFeedbackLoading(true);
    setFeedbackError(null);
    try {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(values)) {
        const trimmed = String(value).trim();
        if (trimmed) {
          params.set(key, trimmed);
        }
      }
      const response = await fetch(`/api/console/feedback?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Feedback search failed");
      }
      setFeedbackResults(data as FeedbackSearchResponse);
    } catch (nextError) {
      setFeedbackError(nextError instanceof Error ? nextError.message : "Feedback search failed");
    } finally {
      setFeedbackLoading(false);
    }
  }, [
    feedbackConversationId,
    feedbackCreatedAfter,
    feedbackCreatedBefore,
    feedbackLimit,
    feedbackOrder,
    feedbackRating,
    feedbackRunId,
    feedbackUserId
  ]);

  function submitFeedbackSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void searchFeedback();
  }

  const loadFeedbackReviews = useCallback(async (feedbackId: string) => {
    const requestId = feedbackReviewRequestId.current + 1;
    feedbackReviewRequestId.current = requestId;
    setFeedbackReviewLoadingId(feedbackId);
    setFeedbackReviewError(null);
    setFeedbackReviews([]);
    try {
      const params = new URLSearchParams({
        feedbackId,
        limit: "50",
        order: "asc"
      });
      const response = await fetch(`/api/console/feedback/reviews?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Feedback review trail failed");
      }
      if (feedbackReviewRequestId.current !== requestId) {
        return;
      }
      const reviews = data as FeedbackReviewEvent[];
      setFeedbackReviews(reviews);
      setFeedbackReviewAssignee(reviews.at(-1)?.assignee_user_id ?? "");
    } catch (nextError) {
      if (feedbackReviewRequestId.current !== requestId) {
        return;
      }
      setFeedbackReviewError(
        nextError instanceof Error ? nextError.message : "Feedback review trail failed"
      );
    } finally {
      if (feedbackReviewRequestId.current === requestId) {
        setFeedbackReviewLoadingId(null);
      }
    }
  }, []);

  useEffect(() => {
    if (!urlStateReady || workspaceMode !== "feedback" || feedbackResults || feedbackLoading) {
      return;
    }
    void searchFeedback(selectedRunId ? { runId: selectedRunId } : {});
  }, [
    feedbackLoading,
    feedbackResults,
    searchFeedback,
    selectedRunId,
    urlStateReady,
    workspaceMode
  ]);

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

  function updateMonitorFilter<Key extends keyof MonitorDrilldownFilters>(
    key: Key,
    value: MonitorDrilldownFilters[Key]
  ) {
    setMonitorFilters((current) => ({ ...current, [key]: value }));
  }

  async function searchMonitorDrilldown(overrides: MonitorDrilldownOverrides = {}) {
    const nextFilters = { ...monitorFilters, ...overrides };
    setMonitorFilters(nextFilters);
    setMonitorDrilldownLoading(true);
    setMonitorDrilldownError(null);
    setRegressionDraft(null);
    setRegressionDraftError(null);
    setCopiedRegressionDraft(false);
    try {
      const params = new URLSearchParams();
      params.set("source", snapshot?.monitorSource ?? "event_store");
      params.set("limit", nextFilters.limit || "50");
      params.set("order", "desc");
      const values: Record<string, string | null> = {
        alert_key: nextFilters.alertKey,
        intent: nextFilters.intent,
        risk_level: nextFilters.riskLevel,
        failure_type: nextFilters.failureType,
        needs_human_review: nextFilters.needsHumanReview,
        grounded: nextFilters.grounded,
        policy_compliant: nextFilters.policyCompliant
      };
      for (const [key, value] of Object.entries(values)) {
        const trimmed = value?.trim();
        if (trimmed) {
          params.set(key, trimmed);
        }
      }
      if (nextFilters.includeHealthy) {
        params.set("include_healthy", "true");
      }
      const response = await fetch(`/api/console/monitor/drilldown?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Monitor drilldown failed");
      }
      setMonitorDrilldown(data as MonitorDrilldownResponse);
    } catch (nextError) {
      setMonitorDrilldownError(nextError instanceof Error ? nextError.message : "Monitor drilldown failed");
    } finally {
      setMonitorDrilldownLoading(false);
    }
  }

  function submitMonitorDrilldown(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void searchMonitorDrilldown();
  }

  function showAlertWorkbenchView(view: AlertWorkbenchView) {
    setAlertWorkbenchView(view);
    if (view === "drilldown" && !monitorDrilldown && !monitorDrilldownLoading) {
      void searchMonitorDrilldown({
        alertKey: monitorFilters.alertKey ?? selectedAlertKey ?? activeAlert?.key ?? null
      });
    }
    if (view === "delivery" && !alertDeliveriesLoading && alertDeliveries.length === 0) {
      void loadAlertDeliveries(deliveryStatusFilter);
    }
  }

  async function loadAlertDeliveries(nextStatus = deliveryStatusFilter) {
    setAlertDeliveriesLoading(true);
    setAlertDeliveriesError(null);
    try {
      const params = new URLSearchParams({
        status: nextStatus,
        limit: "50",
        order: "desc"
      });
      const response = await fetch(`/api/console/monitor/alert-deliveries?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Alert delivery ledger failed");
      }
      setAlertDeliveries(data as AlertDeliveryRecord[]);
    } catch (nextError) {
      setAlertDeliveriesError(
        nextError instanceof Error ? nextError.message : "Alert delivery ledger failed"
      );
    } finally {
      setAlertDeliveriesLoading(false);
    }
  }

  function changeDeliveryStatusFilter(nextStatus: AlertDeliveryStatusFilter) {
    setDeliveryStatusFilter(nextStatus);
    void loadAlertDeliveries(nextStatus);
  }

  async function submitAlertDeliveryAction(record: AlertDeliveryRecord, action: "replay" | "close") {
    setAlertDeliveryActionBusy(`${action}:${record.id}`);
    setAlertDeliveriesError(null);
    try {
      const response = await fetch("/api/console/monitor/alert-deliveries/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          deliveryId: record.id,
          action,
          note: `${action} from PSA Lab Console`
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Alert delivery action failed");
      }
      await Promise.all([
        loadAlertDeliveries(deliveryStatusFilter),
        loadSnapshot({ runId: selectedRunId, alertKey: selectedAlertKey })
      ]);
    } catch (nextError) {
      setAlertDeliveriesError(
        nextError instanceof Error ? nextError.message : "Alert delivery action failed"
      );
    } finally {
      setAlertDeliveryActionBusy(null);
    }
  }

  async function submitAlertDeliveryDispatch() {
    setAlertDeliveryActionBusy("dispatch");
    setAlertDeliveriesError(null);
    setAlertDeliveryDispatchReport(null);
    try {
      const response = await fetch("/api/console/monitor/alert-deliveries/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          source: "event_store",
          monitorLimit: 500,
          dispatchLimit: 25
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Alert delivery dispatch failed");
      }
      setAlertDeliveryDispatchReport(data as AlertDispatchReport);
      await Promise.all([
        loadAlertDeliveries(deliveryStatusFilter),
        loadSnapshot({ runId: selectedRunId, alertKey: selectedAlertKey })
      ]);
    } catch (nextError) {
      setAlertDeliveriesError(
        nextError instanceof Error ? nextError.message : "Alert delivery dispatch failed"
      );
    } finally {
      setAlertDeliveryActionBusy(null);
    }
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

  async function replayConversationMemory(
    nextConversationId = memoryConversationId,
    nextLimit = memoryLimit
  ) {
    const trimmed = nextConversationId.trim();
    if (!trimmed) {
      setMemoryError("Enter a conversation id before replaying memory.");
      return;
    }
    setMemoryLoading(true);
    setMemoryError(null);
    try {
      const response = await fetch("/api/console/memory/replay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          conversationId: trimmed,
          limit: Number(nextLimit)
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Memory replay failed");
      }
      setMemoryConversationId(trimmed);
      setMemoryLimit(nextLimit);
      setMemoryReplay(data as MemoryReplayResult);
      setEvidenceTab("memory");
    } catch (nextError) {
      setMemoryError(nextError instanceof Error ? nextError.message : "Memory replay failed");
    } finally {
      setMemoryLoading(false);
    }
  }

  function submitMemoryReplay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void replayConversationMemory();
  }

  function loadCurrentRunMemory() {
    const conversationId = run?.conversation_id ?? snapshot?.incident?.run.conversation_id ?? "";
    if (!conversationId) {
      setMemoryError("The selected run has no conversation id.");
      return;
    }
    setMemoryConversationId(conversationId);
    void replayConversationMemory(conversationId, memoryLimit);
  }

  async function createEventStoreBackup() {
    setEventOpsBusy("backup");
    setEventOpsError(null);
    try {
      const response = await fetch("/api/console/event-store/backups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ label: eventBackupLabel })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Event-store backup failed");
      }
      setEventBackupReport(data as SQLiteBackupReport);
      setRetentionApplyConfirmed(false);
    } catch (nextError) {
      setEventOpsError(nextError instanceof Error ? nextError.message : "Event-store backup failed");
    } finally {
      setEventOpsBusy(null);
    }
  }

  async function runEventStoreRetention(dryRun: boolean) {
    const hasVerifiedBackup = eventBackupReport?.verified === true;
    const hasPreview =
      eventRetentionReport?.dry_run === true && eventRetentionPreviewKey === eventRetentionRequestKey;
    if (!dryRun && (!hasVerifiedBackup || !hasPreview || !retentionApplyConfirmed)) {
      setEventOpsError("Create a verified backup, run preview, then confirm before applying retention.");
      return;
    }
    setEventOpsBusy(dryRun ? "retention-preview" : "retention-apply");
    setEventOpsError(null);
    try {
      const response = await fetch("/api/console/event-store/retention", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          dry_run: dryRun,
          include_events: retentionIncludeEvents,
          vacuum: retentionVacuum,
          event_retention_days: Number(eventRetentionDays),
          tool_audit_retention_days: Number(toolAuditRetentionDays),
          idempotency_retention_days: Number(idempotencyRetentionDays),
          alert_delivery_retention_days: Number(alertDeliveryRetentionDays)
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Event-store retention failed");
      }
      setEventRetentionReport(data as EventStoreRetentionReport);
      setEventRetentionPreviewKey(dryRun ? eventRetentionRequestKey : null);
      if (!dryRun) {
        setRetentionApplyConfirmed(false);
        await loadSnapshot({ runId: selectedRunId, alertKey: selectedAlertKey });
      }
    } catch (nextError) {
      setEventOpsError(nextError instanceof Error ? nextError.message : "Event-store retention failed");
    } finally {
      setEventOpsBusy(null);
    }
  }

  async function recordPromotionDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const gate = snapshot?.promotionGate;
    const thresholds = gate?.thresholds;
    setEventOpsBusy("promotion-decision");
    setEventOpsError(null);
    try {
      const response = await fetch("/api/console/promotion/decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          target_version: promotionTargetVersion,
          decision: promotionDecision,
          note: promotionDecisionNote,
          override_blocked: promotionOverrideBlocked,
          override_reason: promotionOverrideReason,
          source: gate?.source ?? snapshot?.monitorSource ?? "event_store",
          deep: gate?.readiness.deep ?? false,
          window_hours: gate?.window_hours ?? 24,
          max_active_p0p1_alerts: thresholds?.max_active_p0p1_alerts ?? 0,
          max_active_alerts: thresholds?.max_active_alerts ?? 10,
          max_tool_failure_rate: thresholds?.max_tool_failure_rate ?? 0.05,
          max_feedback_negative_rate: thresholds?.max_feedback_negative_rate ?? 0.4,
          max_eval_age_hours: thresholds?.max_eval_age_hours ?? 24,
          min_tool_calls: thresholds?.min_tool_calls ?? 1,
          min_feedback_count: thresholds?.min_feedback_count ?? 5
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Promotion decision failed");
      }
      const record = data as PromotionDecisionRecord;
      setSnapshot((current) =>
        current
          ? {
              ...current,
              promotionGate: record.gate,
              promotionDecisions: [record, ...current.promotionDecisions].slice(0, 5)
            }
          : current
      );
      setPromotionDecisionNote("");
      setPromotionOverrideBlocked(false);
      setPromotionOverrideReason("");
    } catch (nextError) {
      setEventOpsError(nextError instanceof Error ? nextError.message : "Promotion decision failed");
    } finally {
      setEventOpsBusy(null);
    }
  }

  async function downloadAuditExport() {
    if (!auditExportIncludeEvents && !auditExportIncludeToolAudit) {
      setEventOpsError("Select at least one audit source.");
      return;
    }
    setEventOpsBusy("audit-export");
    setEventOpsError(null);
    setAuditExportStatus(null);
    try {
      const params = new URLSearchParams({
        limit: auditExportLimit,
        order: "asc",
        include_events: String(auditExportIncludeEvents),
        include_tool_audit: String(auditExportIncludeToolAudit)
      });
      const response = await fetch(`/api/console/audit/export?${params.toString()}`, {
        cache: "no-store"
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail ?? "Audit export failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `support-agent-audit-export-${new Date().toISOString().slice(0, 10)}.ndjson`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setAuditExportStatus(`Downloaded ${formatBytes(blob.size)}`);
    } catch (nextError) {
      setEventOpsError(nextError instanceof Error ? nextError.message : "Audit export failed");
    } finally {
      setEventOpsBusy(null);
    }
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

  function openFeedbackRecord(record: AgentFeedback) {
    setWorkspaceMode("feedback");
    setSelectedAlertKey(null);
    setSelectedRunId(record.run_id);
    setSelectedFeedbackId(record.id);
    setRunQuery(record.run_id);
    setFeedbackRunId(record.run_id);
    setFeedbackConversationId(record.conversation_id);
    setRegressionDraft((current) => (current?.source.feedback_id === record.id ? current : null));
    setRegressionDraftError(null);
    setCopiedRegressionDraft(false);
    setFeedbackReviewStatus("acknowledged");
    setFeedbackReviewAssignee("");
    setFeedbackReviewNote("");
    void loadFeedbackReviews(record.id);
    void loadSnapshot({ runId: record.run_id });
  }

  function openToolAuditRecord(record: ToolAuditRecord) {
    setWorkspaceMode("tools");
    setEvidenceTab("tool-audit");
    setSelectedAlertKey(null);
    setSelectedRunId(record.trace_id);
    setRunQuery(record.trace_id);
    void loadSnapshot({ runId: record.trace_id });
  }

  function openMonitorEvent(event: MonitorEvent) {
    const alertKey = event.alert_key ?? monitorFilters.alertKey ?? selectedAlertKey;
    setWorkspaceMode("alerts");
    setSelectedAlertKey(alertKey);
    setSelectedRunId(event.run_id);
    setRunQuery(event.run_id);
    setRegressionDraft((current) =>
      current?.source.monitor_event_ids.includes(event.id) ? current : null
    );
    setRegressionDraftError(null);
    setCopiedRegressionDraft(false);
    void loadSnapshot({ runId: event.run_id, alertKey });
  }

  async function createRegressionDraft(event: MonitorEvent) {
    setRegressionDraftLoadingId(event.id);
    setRegressionDraftError(null);
    setCopiedRegressionDraft(false);
    try {
      const response = await fetch("/api/console/evals/regression-drafts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          run_id: event.run_id,
          monitor_event_id: event.id,
          failure_type: event.failure_types[0] ?? null,
          source: snapshot?.monitorSource ?? "event_store"
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Regression draft failed");
      }
      setRegressionDraft(data as RegressionDraftResponse);
    } catch (nextError) {
      setRegressionDraftError(nextError instanceof Error ? nextError.message : "Regression draft failed");
    } finally {
      setRegressionDraftLoadingId(null);
    }
  }

  async function createFeedbackRegressionDraft(feedback: AgentFeedback) {
    setSelectedFeedbackId(feedback.id);
    setRegressionDraftLoadingId(feedback.id);
    setRegressionDraftError(null);
    setCopiedRegressionDraft(false);
    try {
      const response = await fetch("/api/console/evals/regression-drafts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          run_id: feedback.run_id,
          feedback_id: feedback.id,
          failure_type: feedback.reasons[0] ? `FEEDBACK_${feedback.reasons[0].toUpperCase()}` : null,
          source: "event_store"
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Feedback regression draft failed");
      }
      setRegressionDraft(data as RegressionDraftResponse);
    } catch (nextError) {
      setRegressionDraftError(
        nextError instanceof Error ? nextError.message : "Feedback regression draft failed"
      );
    } finally {
      setRegressionDraftLoadingId(null);
    }
  }

  async function submitFeedbackReview(feedback: AgentFeedback) {
    const requestId = feedbackReviewRequestId.current + 1;
    feedbackReviewRequestId.current = requestId;
    setFeedbackReviewLoadingId(feedback.id);
    setFeedbackReviewError(null);
    try {
      const response = await fetch("/api/console/feedback/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          feedbackId: feedback.id,
          status: feedbackReviewStatus,
          assigneeUserId: feedbackReviewAssignee,
          note: feedbackReviewNote
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Feedback review update failed");
      }
      if (feedbackReviewRequestId.current !== requestId) {
        return;
      }
      const review = data as FeedbackReviewEvent;
      setFeedbackReviews((current) =>
        current.some((item) => item.id === review.id) ? current : [...current, review]
      );
      setFeedbackReviewAssignee(review.assignee_user_id ?? "");
      setFeedbackReviewNote("");
      await Promise.all([
        loadSnapshot({ runId: feedback.run_id, alertKey: selectedAlertKey }),
        searchFeedback()
      ]);
    } catch (nextError) {
      if (feedbackReviewRequestId.current !== requestId) {
        return;
      }
      setFeedbackReviewError(
        nextError instanceof Error ? nextError.message : "Feedback review update failed"
      );
    } finally {
      if (feedbackReviewRequestId.current === requestId) {
        setFeedbackReviewLoadingId(null);
      }
    }
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

  async function runStagingEvalGate() {
    setActionBusy("eval");
    setError(null);
    const context = {
      run_id: selectedRunId ?? snapshot?.activeRunId ?? undefined,
      alert_key: selectedAlertKey ?? snapshot?.activeAlertKey ?? undefined,
      trigger: "console"
    };
    try {
      const response = await fetch("/api/console/run-eval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(context)
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Eval gate failed");
      }
      setEvalReport(data as EvalReport);
      setEvidenceTab("brief");
      await loadSnapshot({
        runId: context.run_id ?? null,
        alertKey: context.alert_key ?? null
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Eval gate failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function copyIncidentBrief() {
    setActionBusy("brief-copy");
    setError(null);
    try {
      await writeClipboardText(await loadIncidentBriefMarkdown());
      setCopiedBrief(true);
      window.setTimeout(() => setCopiedBrief(false), 1800);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Clipboard is not available in this browser session");
    } finally {
      setActionBusy(null);
    }
  }

  async function downloadIncidentBrief() {
    if (!snapshot) {
      return;
    }
    setActionBusy("brief-download");
    setError(null);
    try {
      const markdown = await loadIncidentBriefMarkdown();
      const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${snapshot.activeRunId ?? "support-agent-incident-brief"}.md`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Incident brief download failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function loadIncidentBriefMarkdown() {
    if (snapshot?.incidentBrief?.markdown) {
      return snapshot.incidentBrief.markdown;
    }
    if (!snapshot?.activeRunId) {
      return incidentBrief.markdown;
    }
    const params = new URLSearchParams({
      runId: snapshot.activeRunId,
      include_memory: "true",
      limit: "1000"
    });
    const response = await fetch(`/api/console/incidents/brief?${params.toString()}`, {
      cache: "no-store"
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail ?? "Incident brief failed");
    }
    return (data as IncidentBriefResponse).markdown;
  }

  async function copyRegressionDraft() {
    if (!regressionDraft) {
      return;
    }
    try {
      await writeClipboardText(regressionDraft.draft_json);
      setCopiedRegressionDraft(true);
      window.setTimeout(() => setCopiedRegressionDraft(false), 1800);
    } catch {
      setRegressionDraftError("Clipboard is not available in this browser session");
    }
  }

  function chooseAlert(alert: MonitorAlert) {
    const runId = alert.sample_run_ids[0] ?? null;
    setSelectedAlertKey(alert.key);
    setSelectedRunId(runId);
    setMonitorFilters((current) => ({ ...current, alertKey: alert.key }));
    setRegressionDraft(null);
    setRegressionDraftError(null);
    setCopiedRegressionDraft(false);
    if (alertWorkbenchView === "drilldown") {
      void searchMonitorDrilldown({ alertKey: alert.key });
    }
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
            setWorkspaceMode("memory");
            setEvidenceTab("memory");
            const currentConversationId = snapshot?.incident?.run.conversation_id ?? "";
            if (currentConversationId && !memoryReplay && !memoryLoading) {
              setMemoryConversationId(currentConversationId);
              void replayConversationMemory(currentConversationId, memoryLimit);
            }
          }
          if (target === "feedback") {
            setWorkspaceMode("feedback");
            const currentRunId = snapshot?.incident?.run.id ?? "";
            if (currentRunId && !feedbackRunId) {
              setFeedbackRunId(currentRunId);
            }
            if (!feedbackResults && !feedbackLoading) {
              void searchFeedback(currentRunId ? { runId: currentRunId } : {});
            }
          }
          if (target === "alerts") {
            setWorkspaceMode("alerts");
            setSeverityFilter("all");
          }
          if (target === "settings") {
            setWorkspaceMode("settings");
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
          ) : workspaceMode === "memory" ? (
            <MemoryReplayWorkbenchPanel
              replay={memoryReplay}
              loading={memoryLoading}
              error={memoryError}
              conversationId={memoryConversationId}
              limit={memoryLimit}
              currentConversationId={run?.conversation_id ?? null}
              onConversationId={setMemoryConversationId}
              onLimit={setMemoryLimit}
              onSubmit={submitMemoryReplay}
              onUseCurrent={loadCurrentRunMemory}
            />
          ) : workspaceMode === "feedback" ? (
            <FeedbackWorkbenchPanel
              results={feedbackResults}
              loading={feedbackLoading}
              error={feedbackError}
              rating={feedbackRating}
              runId={feedbackRunId}
              userId={feedbackUserId}
              conversationId={feedbackConversationId}
              createdAfter={feedbackCreatedAfter}
              createdBefore={feedbackCreatedBefore}
              limit={feedbackLimit}
              order={feedbackOrder}
              currentRunId={run?.id ?? null}
              selectedFeedbackId={selectedFeedbackId}
              feedbackReviews={feedbackReviews}
              feedbackReviewStatus={feedbackReviewStatus}
              feedbackReviewAssignee={feedbackReviewAssignee}
              feedbackReviewNote={feedbackReviewNote}
              feedbackReviewLoadingId={feedbackReviewLoadingId}
              feedbackReviewError={feedbackReviewError}
              regressionDraft={regressionDraft}
              regressionDraftLoadingId={regressionDraftLoadingId}
              regressionDraftError={regressionDraftError}
              copiedRegressionDraft={copiedRegressionDraft}
              onRating={setFeedbackRating}
              onRunId={setFeedbackRunId}
              onUserId={setFeedbackUserId}
              onConversationId={setFeedbackConversationId}
              onCreatedAfter={setFeedbackCreatedAfter}
              onCreatedBefore={setFeedbackCreatedBefore}
              onLimit={setFeedbackLimit}
              onOrder={setFeedbackOrder}
              onReviewStatus={setFeedbackReviewStatus}
              onReviewAssignee={setFeedbackReviewAssignee}
              onReviewNote={setFeedbackReviewNote}
              onSubmit={submitFeedbackSearch}
              onSearch={searchFeedback}
              onOpenFeedback={openFeedbackRecord}
              onSubmitReview={submitFeedbackReview}
              onDraftFeedback={createFeedbackRegressionDraft}
              onCopyRegressionDraft={() => void copyRegressionDraft()}
            />
          ) : workspaceMode === "settings" ? (
            <SettingsWorkbenchPanel
              backupLabel={eventBackupLabel}
              backupReport={eventBackupReport}
              retentionReport={eventRetentionReport}
              promotionGate={snapshot?.promotionGate ?? null}
              promotionDecisions={snapshot?.promotionDecisions ?? []}
              operationsAutomation={snapshot?.operationsAutomation ?? null}
              sloReport={snapshot?.sloReport ?? null}
              busy={eventOpsBusy}
              error={eventOpsError}
              promotionTargetVersion={promotionTargetVersion}
              promotionDecision={promotionDecision}
              promotionDecisionNote={promotionDecisionNote}
              promotionOverrideBlocked={promotionOverrideBlocked}
              promotionOverrideReason={promotionOverrideReason}
              auditExportLimit={auditExportLimit}
              auditExportIncludeEvents={auditExportIncludeEvents}
              auditExportIncludeToolAudit={auditExportIncludeToolAudit}
              auditExportStatus={auditExportStatus}
              eventRetentionDays={eventRetentionDays}
              toolAuditRetentionDays={toolAuditRetentionDays}
              idempotencyRetentionDays={idempotencyRetentionDays}
              alertDeliveryRetentionDays={alertDeliveryRetentionDays}
              includeEvents={retentionIncludeEvents}
              vacuum={retentionVacuum}
              applyConfirmed={retentionApplyConfirmed}
              previewReady={eventRetentionPreviewKey === eventRetentionRequestKey}
              onBackupLabel={setEventBackupLabel}
              onEventRetentionDays={setEventRetentionDays}
              onToolAuditRetentionDays={setToolAuditRetentionDays}
              onIdempotencyRetentionDays={setIdempotencyRetentionDays}
              onAlertDeliveryRetentionDays={setAlertDeliveryRetentionDays}
              onPromotionTargetVersion={setPromotionTargetVersion}
              onPromotionDecision={setPromotionDecision}
              onPromotionDecisionNote={setPromotionDecisionNote}
              onPromotionOverrideBlocked={setPromotionOverrideBlocked}
              onPromotionOverrideReason={setPromotionOverrideReason}
              onAuditExportLimit={setAuditExportLimit}
              onAuditExportIncludeEvents={setAuditExportIncludeEvents}
              onAuditExportIncludeToolAudit={setAuditExportIncludeToolAudit}
              onIncludeEvents={setRetentionIncludeEvents}
              onVacuum={setRetentionVacuum}
              onApplyConfirmed={setRetentionApplyConfirmed}
              onBackup={() => void createEventStoreBackup()}
              onPromotionDecisionSubmit={recordPromotionDecision}
              onAuditExport={() => void downloadAuditExport()}
              onRetention={(dryRun) => void runEventStoreRetention(dryRun)}
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
            <MonitorWorkbenchPanel
              view={alertWorkbenchView}
              onView={showAlertWorkbenchView}
              snapshot={snapshot}
              triageHealthStats={triageHealthStats}
              alertDeliveryStats={alertDeliveryStats}
              filteredAlerts={filteredAlerts}
              activeAlert={activeAlert}
              loading={loading}
              isDemo={isDemo}
              scenarioText={scenarioText}
              onScenarioText={setScenarioText}
              onRunScenario={() => void runScenario()}
              scenarioBusy={actionBusy === "scenario"}
              queueQuery={queueQuery}
              severityFilter={severityFilter}
              statusFilter={statusFilter}
              queueSort={queueSort}
              onlyNewAlerts={onlyNewAlerts}
              onQueueQuery={setQueueQuery}
              onSeverityFilter={setSeverityFilter}
              onStatusFilter={setStatusFilter}
              onQueueSort={setQueueSort}
              onOnlyNewAlerts={setOnlyNewAlerts}
              onChooseAlert={chooseAlert}
              filters={monitorFilters}
              onFilter={updateMonitorFilter}
              drilldown={monitorDrilldown}
              drilldownStats={monitorDrilldownStats}
              drilldownLoading={monitorDrilldownLoading}
              drilldownError={monitorDrilldownError}
              regressionDraft={regressionDraft}
              regressionDraftLoadingId={regressionDraftLoadingId}
              regressionDraftError={regressionDraftError}
              copiedRegressionDraft={copiedRegressionDraft}
              onSubmitDrilldown={submitMonitorDrilldown}
              onSearchDrilldown={searchMonitorDrilldown}
              onOpenMonitorEvent={openMonitorEvent}
              onDraftRegression={createRegressionDraft}
              onCopyRegressionDraft={() => void copyRegressionDraft()}
              deliveryRows={alertDeliveries}
              deliveryStatus={deliveryStatusFilter}
              deliveryLoading={alertDeliveriesLoading}
              deliveryError={alertDeliveriesError}
              deliveryActionBusy={alertDeliveryActionBusy}
              deliveryDispatchReport={alertDeliveryDispatchReport}
              onDeliveryStatus={changeDeliveryStatusFilter}
              onRefreshDeliveries={() => void loadAlertDeliveries(deliveryStatusFilter)}
              onDispatchDeliveries={() => void submitAlertDeliveryDispatch()}
              onDeliveryAction={(record, action) => void submitAlertDeliveryAction(record, action)}
            />
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
              onRunEval={() => void runStagingEvalGate()}
              onCopyBrief={() => void copyIncidentBrief()}
              onDownloadBrief={() => void downloadIncidentBrief()}
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
  const latestEvalGate = snapshot?.evalGateLatest ?? null;
  const promotionGate = snapshot?.promotionGate ?? null;
  const sloReport = snapshot?.sloReport ?? null;
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
        <strong>{evalGateTileLabel(evalReport, latestEvalGate)}</strong>
      </div>
      <div className={`ops-tile ${sloTileClass(sloReport?.status ?? null)}`}>
        <Activity size={16} />
        <span>SLO</span>
        <strong>{sloReport?.status ?? "unknown"}</strong>
      </div>
      <div className={`ops-tile ${promotionGateTileClass(promotionGate?.status ?? null)}`}>
        <Rocket size={16} />
        <span>Promotion</span>
        <strong>{promotionGate?.status ?? "unknown"}</strong>
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
    ["feedback", "Feedback", ClipboardList, null],
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

function MonitorWorkbenchPanel({
  view,
  onView,
  snapshot,
  triageHealthStats,
  alertDeliveryStats,
  filteredAlerts,
  activeAlert,
  loading,
  isDemo,
  scenarioText,
  onScenarioText,
  onRunScenario,
  scenarioBusy,
  queueQuery,
  severityFilter,
  statusFilter,
  queueSort,
  onlyNewAlerts,
  onQueueQuery,
  onSeverityFilter,
  onStatusFilter,
  onQueueSort,
  onOnlyNewAlerts,
  onChooseAlert,
  filters,
  onFilter,
  drilldown,
  drilldownStats,
  drilldownLoading,
  drilldownError,
  regressionDraft,
  regressionDraftLoadingId,
  regressionDraftError,
  copiedRegressionDraft,
  onSubmitDrilldown,
  onSearchDrilldown,
  onOpenMonitorEvent,
  onDraftRegression,
  onCopyRegressionDraft,
  deliveryRows,
  deliveryStatus,
  deliveryLoading,
  deliveryError,
  deliveryActionBusy,
  deliveryDispatchReport,
  onDeliveryStatus,
  onRefreshDeliveries,
  onDispatchDeliveries,
  onDeliveryAction
}: {
  view: AlertWorkbenchView;
  onView: (view: AlertWorkbenchView) => void;
  snapshot: ConsoleSnapshot | null;
  triageHealthStats: MonitorTriageHealthStats;
  alertDeliveryStats: MonitorAlertDeliveryStats;
  filteredAlerts: MonitorAlert[];
  activeAlert: MonitorAlert | null;
  loading: boolean;
  isDemo: boolean;
  scenarioText: string;
  onScenarioText: (value: string) => void;
  onRunScenario: () => void;
  scenarioBusy: boolean;
  queueQuery: string;
  severityFilter: AlertSeverityFilter;
  statusFilter: AlertStatusFilter;
  queueSort: AlertSort;
  onlyNewAlerts: boolean;
  onQueueQuery: (value: string) => void;
  onSeverityFilter: (value: AlertSeverityFilter) => void;
  onStatusFilter: (value: AlertStatusFilter) => void;
  onQueueSort: (value: AlertSort) => void;
  onOnlyNewAlerts: (value: boolean) => void;
  onChooseAlert: (alert: MonitorAlert) => void;
  filters: MonitorDrilldownFilters;
  onFilter: <Key extends keyof MonitorDrilldownFilters>(
    key: Key,
    value: MonitorDrilldownFilters[Key]
  ) => void;
  drilldown: MonitorDrilldownResponse | null;
  drilldownStats: MonitorDrilldownUiStats;
  drilldownLoading: boolean;
  drilldownError: string | null;
  regressionDraft: RegressionDraftResponse | null;
  regressionDraftLoadingId: string | null;
  regressionDraftError: string | null;
  copiedRegressionDraft: boolean;
  onSubmitDrilldown: (event: FormEvent<HTMLFormElement>) => void;
  onSearchDrilldown: (overrides?: MonitorDrilldownOverrides) => void | Promise<void>;
  onOpenMonitorEvent: (event: MonitorEvent) => void;
  onDraftRegression: (event: MonitorEvent) => void | Promise<void>;
  onCopyRegressionDraft: () => void | Promise<void>;
  deliveryRows: AlertDeliveryRecord[];
  deliveryStatus: AlertDeliveryStatusFilter;
  deliveryLoading: boolean;
  deliveryError: string | null;
  deliveryActionBusy: string | null;
  deliveryDispatchReport: AlertDispatchReport | null;
  onDeliveryStatus: (status: AlertDeliveryStatusFilter) => void;
  onRefreshDeliveries: () => void;
  onDispatchDeliveries: () => void;
  onDeliveryAction: (record: AlertDeliveryRecord, action: "replay" | "close") => void;
}) {
  return (
    <aside className="alerts-panel monitor-workbench">
      <div className="panel-heading">
        <div>
          <span>Monitor Workbench</span>
          <strong>
            {view === "queue"
              ? `${filteredAlerts.length} of ${snapshot?.summary.alerts.length ?? 0} alerts`
              : `${drilldownStats.matchingEvents} matching events`}
          </strong>
        </div>
      </div>

      <TriageHealthStrip stats={triageHealthStats} loading={loading} />
      <AlertDeliveryStrip stats={alertDeliveryStats} />

      <div className="workbench-switch" role="tablist" aria-label="Monitor workbench views">
        <button
          type="button"
          role="tab"
          aria-selected={view === "queue"}
          className={view === "queue" ? "is-active" : ""}
          onClick={() => onView("queue")}
        >
          Queue
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "drilldown"}
          className={view === "drilldown" ? "is-active" : ""}
          onClick={() => onView("drilldown")}
        >
          Drilldown
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "delivery"}
          className={view === "delivery" ? "is-active" : ""}
          onClick={() => onView("delivery")}
        >
          Delivery
        </button>
      </div>

      {view === "queue" ? (
        <>
          <div className="queue-controls" aria-label="Alert queue controls">
            <label className="search-control">
              <Search size={14} />
              <input
                value={queueQuery}
                onChange={(event) => onQueueQuery(event.target.value)}
                placeholder="Search run, reason, owner"
                aria-label="Search alert queue"
              />
            </label>
            <label className="filter-control">
              <Filter size={14} />
              <select
                value={severityFilter}
                onChange={(event) => onSeverityFilter(event.target.value as AlertSeverityFilter)}
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
                onChange={(event) => onStatusFilter(event.target.value as AlertStatusFilter)}
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
                onChange={(event) => onQueueSort(event.target.value as AlertSort)}
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
                onChange={(event) => onOnlyNewAlerts(event.target.checked)}
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
                onScenarioText={onScenarioText}
                onRunScenario={onRunScenario}
                busy={scenarioBusy}
              />
            ) : null}
            {filteredAlerts.map((alert) => (
              <button
                type="button"
                className={`alert-card severity-${alert.severity.toLowerCase()} ${
                  alert.key === activeAlert?.key ? "is-selected" : ""
                }`}
                key={alert.key}
                onClick={() => onChooseAlert(alert)}
                aria-pressed={alert.key === activeAlert?.key}
              >
                <div className="alert-card-top">
                  <span className="severity-dot" />
                  <strong>{alert.severity}</strong>
                  <time title={alert.last_seen_at}>{ageLabel(alert.last_seen_at)}</time>
                </div>
                <span className="alert-title">{alert.reason}</span>
                <span className="alert-meta">
                  {alert.sample_run_ids[0] ?? "no run"} - {alert.count} event
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
            <button type="button" onClick={() => onSeverityFilter("all")}>
              View all
              <ChevronRight size={15} />
            </button>
          </div>
        </>
      ) : view === "drilldown" ? (
        <MonitorDrilldownPanel
          activeAlert={activeAlert}
          filters={filters}
          drilldown={drilldown}
          stats={drilldownStats}
          loading={drilldownLoading}
          error={drilldownError}
          activeRunId={snapshot?.activeRunId ?? null}
          onFilter={onFilter}
          onSubmit={onSubmitDrilldown}
          onSearch={onSearchDrilldown}
          onOpenMonitorEvent={onOpenMonitorEvent}
          regressionDraft={regressionDraft}
          regressionDraftLoadingId={regressionDraftLoadingId}
          regressionDraftError={regressionDraftError}
          copiedRegressionDraft={copiedRegressionDraft}
          onDraftRegression={onDraftRegression}
          onCopyRegressionDraft={onCopyRegressionDraft}
        />
      ) : (
        <AlertDeliveryLedger
          rows={deliveryRows}
          status={deliveryStatus}
          loading={deliveryLoading}
          error={deliveryError}
          actionBusy={deliveryActionBusy}
          dispatchReport={deliveryDispatchReport}
          onStatus={onDeliveryStatus}
          onRefresh={onRefreshDeliveries}
          onDispatch={onDispatchDeliveries}
          onAction={onDeliveryAction}
        />
      )}
    </aside>
  );
}

function TriageHealthStrip({
  stats,
  loading
}: {
  stats: MonitorTriageHealthStats;
  loading: boolean;
}) {
  const tone = stats.healthStatus === "critical" ? "danger" : stats.healthStatus === "degraded" ? "warn" : "success";
  return (
    <section className={`triage-health-strip state-${stats.healthStatus}`} aria-label="Triage health metrics">
      <div className="triage-health-head">
        <span>
          <Gauge size={15} />
          Triage Health
        </span>
        <Badge tone={stats.healthStatus === "unknown" && loading ? "neutral" : tone}>
          {stats.healthStatus === "unknown" && loading ? "loading" : stats.healthStatus}
        </Badge>
      </div>
      <div className="run-search-stats triage-health-stats">
        <Metric label="Active" value={String(stats.activeAlerts)} />
        <Metric label="Unassigned" value={String(stats.unassignedActiveAlerts)} />
        <Metric label="New" value={String(stats.newEventsSinceTriage)} />
        <Metric label="MTTA" value={formatDurationSeconds(stats.mttaSeconds)} />
      </div>
      <div className="triage-health-meta">
        <span>P0/P1 {stats.p0p1Alerts}</span>
        <span>Stale {stats.staleActiveAlerts}</span>
        <span>Oldest {ageLabel(stats.oldestActiveAlertAt)}</span>
      </div>
    </section>
  );
}

function AlertDeliveryStrip({ stats }: { stats: MonitorAlertDeliveryStats }) {
  const timingLabel = stats.inProgressCount
    ? `${stats.inProgressCount} claimed`
    : stats.nextAttemptAt
      ? `Next ${relativeTimeLabel(stats.nextAttemptAt)}`
      : `Oldest ${ageLabel(stats.oldestPendingAt)}`;

  return (
    <section className={`alert-delivery-strip state-${stats.status}`} aria-label="Alert delivery health">
      <div className="triage-health-head">
        <span>
          <Bell size={15} />
          Alert Delivery
        </span>
        <Badge tone={stats.tone}>{stats.badgeLabel}</Badge>
      </div>
      <div className="run-search-stats triage-health-stats">
        <Metric label="State" value={stats.value} />
        <Metric label="Pending" value={String(stats.pendingCount)} />
        <Metric label="Failed" value={String(stats.failedCount)} />
        <Metric label="Dead" value={String(stats.deadCount)} />
        <Metric label="Closed" value={String(stats.closedCount)} />
      </div>
      <div className="triage-health-meta">
        <span>{stats.detail}</span>
        <span>{timingLabel}</span>
      </div>
    </section>
  );
}

function AlertDeliveryLedger({
  rows,
  status,
  loading,
  error,
  actionBusy,
  dispatchReport,
  onStatus,
  onRefresh,
  onDispatch,
  onAction
}: {
  rows: AlertDeliveryRecord[];
  status: AlertDeliveryStatusFilter;
  loading: boolean;
  error: string | null;
  actionBusy: string | null;
  dispatchReport: AlertDispatchReport | null;
  onStatus: (status: AlertDeliveryStatusFilter) => void;
  onRefresh: () => void;
  onDispatch: () => void;
  onAction: (record: AlertDeliveryRecord, action: "replay" | "close") => void;
}) {
  const dispatchStats = buildAlertDispatchResultStats(dispatchReport);
  const dispatchBusy = actionBusy === "dispatch";
  return (
    <div className="delivery-ledger">
      <div className="queue-controls delivery-controls" aria-label="Alert delivery controls">
        <label className="filter-control">
          <Filter size={14} />
          <select
            value={status}
            onChange={(event) => onStatus(event.target.value as AlertDeliveryStatusFilter)}
            aria-label="Filter deliveries by status"
          >
            <option value="dead">Dead</option>
            <option value="failed">Failed</option>
            <option value="pending">Pending</option>
            <option value="in_progress">Claimed</option>
            <option value="sent">Sent</option>
            <option value="closed">Closed</option>
            <option value="all">All</option>
          </select>
        </label>
        <div className="delivery-control-actions">
          <button
            className="secondary-button compact-action"
            type="button"
            onClick={onDispatch}
            disabled={loading || Boolean(actionBusy)}
            title="Dispatch due alert deliveries"
          >
            {dispatchBusy ? <Loader2 className="spin" size={15} /> : <Rocket size={15} />}
            Dispatch now
          </button>
          <button
            className="secondary-button compact-action"
            type="button"
            onClick={onRefresh}
            disabled={loading || Boolean(actionBusy)}
          >
            {loading ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
            Refresh
          </button>
        </div>
      </div>

      {error ? <div className="inline-error">{error}</div> : null}
      {dispatchStats ? (
        <div className={`delivery-dispatch-result state-${dispatchStats.tone}`}>
          <div className="delivery-dispatch-copy">
            <strong>{dispatchStats.title}</strong>
            <span>{dispatchStats.detail}</span>
          </div>
          <div className="delivery-dispatch-metrics">
            <Metric label="Attempted" value={String(dispatchStats.attemptedCount)} />
            <Metric label="Sent" value={String(dispatchStats.sentCount)} />
            <Metric label="Failed" value={String(dispatchStats.failedCount)} />
            <Metric label="Dead" value={String(dispatchStats.deadCount)} />
          </div>
        </div>
      ) : null}
      {loading && rows.length === 0 ? <LoadingBlock /> : null}
      {!loading && rows.length === 0 ? (
        <PanelEmpty title="No delivery rows" detail="No alert deliveries match this filter." />
      ) : null}

      <div className="delivery-list">
        {rows.map((record) => {
          const replayBusy = actionBusy === `replay:${record.id}`;
          const closeBusy = actionBusy === `close:${record.id}`;
          const nextTiming = record.next_attempt_at
            ? `Next ${relativeTimeLabel(record.next_attempt_at)}`
            : record.last_attempt_at
              ? `Last ${ageLabel(record.last_attempt_at)}`
              : "No attempt";
          return (
            <article className={`delivery-row state-${record.status}`} key={record.id}>
              <div className="delivery-row-main">
                <div className="delivery-row-title">
                  <Badge tone={deliveryStatusTone(record.status)}>{record.status}</Badge>
                  <strong>{record.alert_key}</strong>
                </div>
                <span>{record.reason}</span>
                <div className="delivery-row-meta">
                  <span>{record.id}</span>
                  <span>{record.sample_run_ids[0] ?? "no run"}</span>
                  <span>{nextTiming}</span>
                  <span>{record.operator_action_by ?? record.locked_by ?? "no operator"}</span>
                </div>
                {record.last_error || record.operator_action_note ? (
                  <small>{record.last_error ?? record.operator_action_note}</small>
                ) : null}
              </div>
              <div className="delivery-row-side">
                <Metric label="Attempts" value={String(record.attempt_count)} />
                <Metric label="Code" value={record.response_status_code ? String(record.response_status_code) : "n/a"} />
                <div className="delivery-actions">
                  <button
                    className="secondary-button icon-command"
                    type="button"
                    onClick={() => onAction(record, "replay")}
                    disabled={!canReplayAlertDelivery(record) || Boolean(actionBusy)}
                    title="Replay delivery"
                  >
                    {replayBusy ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
                    Replay
                  </button>
                  <button
                    className="secondary-button icon-command danger-command"
                    type="button"
                    onClick={() => onAction(record, "close")}
                    disabled={!canCloseAlertDelivery(record) || Boolean(actionBusy)}
                    title="Close dead-letter"
                  >
                    {closeBusy ? <Loader2 className="spin" size={15} /> : <X size={15} />}
                    Close
                  </button>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function MemoryReplayWorkbenchPanel({
  replay,
  loading,
  error,
  conversationId,
  limit,
  currentConversationId,
  onConversationId,
  onLimit,
  onSubmit,
  onUseCurrent
}: {
  replay: MemoryReplayResult | null;
  loading: boolean;
  error: string | null;
  conversationId: string;
  limit: string;
  currentConversationId: string | null;
  onConversationId: (value: string) => void;
  onLimit: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onUseCurrent: () => void;
}) {
  const messages = replay?.state.messages ?? [];
  const facts = Object.entries(replay?.state.facts ?? {});
  return (
    <aside className="alerts-panel run-workbench memory-workbench">
      <div className="panel-heading">
        <div>
          <span>Memory Replay</span>
          <strong>{replay ? replay.conversation_id : currentConversationId ?? "No conversation selected"}</strong>
        </div>
      </div>

      <form className="run-search-form memory-replay-form" onSubmit={onSubmit}>
        <label className="field-label compact">
          Conversation ID
          <input
            value={conversationId}
            onChange={(event) => onConversationId(event.target.value)}
            placeholder="conv_..."
          />
        </label>
        <div className="tool-window-grid">
          <label className="field-label compact">
            Event limit
            <input
              value={limit}
              onChange={(event) => onLimit(event.target.value)}
              placeholder="0 = backend default"
            />
          </label>
          <button
            className="secondary-button"
            type="button"
            disabled={!currentConversationId || loading}
            onClick={onUseCurrent}
          >
            <Database size={16} />
            Current run
          </button>
        </div>
        <button className="primary-button" type="submit" disabled={loading || !conversationId.trim()}>
          {loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
          Replay memory
        </button>
      </form>

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? (replay ? `${messages.length} memory messages replayed` : "Memory replay status")}
      </div>

      {loading && !replay ? <LoadingBlock /> : null}
      {replay ? (
        <>
          <div className="run-search-stats memory-replay-stats" aria-label="Memory replay stats">
            <Metric label="Events" value={String(replay.event_count)} />
            <Metric label="Messages" value={String(replay.replayed_message_count)} />
            <Metric label="Runs" value={String(replay.replayed_run_count)} />
            <Metric label="Ignored" value={String(replay.ignored_event_count)} />
          </div>

          <section className="memory-replay-section">
            <div className="settings-section-head">
              <strong>Facts</strong>
              <Badge tone={facts.length ? "success" : "neutral"}>{facts.length}</Badge>
            </div>
            {facts.length ? (
              <div className="memory-fact-list">
                {facts.map(([key, value]) => (
                  <div key={key}>
                    <span>{key}</span>
                    <strong>{stringifyValue(value as JsonValue)}</strong>
                  </div>
                ))}
              </div>
            ) : (
              <PanelEmpty title="No facts" detail="Replay returned no extracted conversation facts." />
            )}
          </section>

          <section className="memory-replay-section">
            <div className="settings-section-head">
              <strong>Working Summary</strong>
              <Badge tone={replay.state.working_summary ? "success" : "neutral"}>
                {replay.state.last_intent ?? "unknown"}
              </Badge>
            </div>
            <p className="summary-copy">{replay.state.working_summary || "No working summary stored."}</p>
            {replay.state.open_questions.length ? (
              <div className="memory-open-questions">
                {replay.state.open_questions.map((question) => (
                  <span key={question}>{question}</span>
                ))}
              </div>
            ) : null}
          </section>

          <section className="memory-replay-section">
            <div className="settings-section-head">
              <strong>Messages</strong>
              <Badge>{messages.length}</Badge>
            </div>
            {messages.length ? (
              <div className="memory-message-list">
                {messages.slice(-8).map((message, index) => (
                  <article
                    className={`memory-message-row role-${message.role}`}
                    key={`${message.created_at}-${index}`}
                  >
                    <Badge tone={message.role === "assistant" ? "success" : message.role === "user" ? "warn" : "neutral"}>
                      {message.role}
                    </Badge>
                    <span>{message.content}</span>
                    <time>{formatTime(message.created_at)}</time>
                  </article>
                ))}
              </div>
            ) : (
              <PanelEmpty title="No messages" detail="Replay found no stored user or assistant messages." />
            )}
          </section>
        </>
      ) : !loading ? (
        <PanelEmpty title="Replay conversation memory" detail="Enter a conversation id or use the selected run." />
      ) : null}
    </aside>
  );
}

function SettingsWorkbenchPanel({
  backupLabel,
  backupReport,
  retentionReport,
  promotionGate,
  promotionDecisions,
  operationsAutomation,
  sloReport,
  busy,
  error,
  promotionTargetVersion,
  promotionDecision,
  promotionDecisionNote,
  promotionOverrideBlocked,
  promotionOverrideReason,
  auditExportLimit,
  auditExportIncludeEvents,
  auditExportIncludeToolAudit,
  auditExportStatus,
  eventRetentionDays,
  toolAuditRetentionDays,
  idempotencyRetentionDays,
  alertDeliveryRetentionDays,
  includeEvents,
  vacuum,
  applyConfirmed,
  previewReady,
  onBackupLabel,
  onEventRetentionDays,
  onToolAuditRetentionDays,
  onIdempotencyRetentionDays,
  onAlertDeliveryRetentionDays,
  onPromotionTargetVersion,
  onPromotionDecision,
  onPromotionDecisionNote,
  onPromotionOverrideBlocked,
  onPromotionOverrideReason,
  onAuditExportLimit,
  onAuditExportIncludeEvents,
  onAuditExportIncludeToolAudit,
  onIncludeEvents,
  onVacuum,
  onApplyConfirmed,
  onBackup,
  onPromotionDecisionSubmit,
  onAuditExport,
  onRetention
}: {
  backupLabel: string;
  backupReport: SQLiteBackupReport | null;
  retentionReport: EventStoreRetentionReport | null;
  promotionGate: PromotionGateResponse | null;
  promotionDecisions: PromotionDecisionRecord[];
  operationsAutomation: OperationsAutomationPlan | null;
  sloReport: SloReportResponse | null;
  busy: string | null;
  error: string | null;
  promotionTargetVersion: string;
  promotionDecision: PromotionDecision;
  promotionDecisionNote: string;
  promotionOverrideBlocked: boolean;
  promotionOverrideReason: string;
  auditExportLimit: string;
  auditExportIncludeEvents: boolean;
  auditExportIncludeToolAudit: boolean;
  auditExportStatus: string | null;
  eventRetentionDays: string;
  toolAuditRetentionDays: string;
  idempotencyRetentionDays: string;
  alertDeliveryRetentionDays: string;
  includeEvents: boolean;
  vacuum: boolean;
  applyConfirmed: boolean;
  previewReady: boolean;
  onBackupLabel: (value: string) => void;
  onEventRetentionDays: (value: string) => void;
  onToolAuditRetentionDays: (value: string) => void;
  onIdempotencyRetentionDays: (value: string) => void;
  onAlertDeliveryRetentionDays: (value: string) => void;
  onPromotionTargetVersion: (value: string) => void;
  onPromotionDecision: (value: PromotionDecision) => void;
  onPromotionDecisionNote: (value: string) => void;
  onPromotionOverrideBlocked: (value: boolean) => void;
  onPromotionOverrideReason: (value: string) => void;
  onAuditExportLimit: (value: string) => void;
  onAuditExportIncludeEvents: (value: boolean) => void;
  onAuditExportIncludeToolAudit: (value: boolean) => void;
  onIncludeEvents: (value: boolean) => void;
  onVacuum: (value: boolean) => void;
  onApplyConfirmed: (value: boolean) => void;
  onBackup: () => void;
  onPromotionDecisionSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onAuditExport: () => void;
  onRetention: (dryRun: boolean) => void;
}) {
  const backupBusy = busy === "backup";
  const previewBusy = busy === "retention-preview";
  const applyBusy = busy === "retention-apply";
  const decisionBusy = busy === "promotion-decision";
  const auditExportBusy = busy === "audit-export";
  const canApply = backupReport?.verified === true && previewReady && applyConfirmed;
  const promotionStats = buildPromotionGateStats(promotionGate);
  const automationTone = automationPlanTone(operationsAutomation);
  const automationActions = operationsAutomation?.actions ?? [];
  const sloTone = sloReportTone(sloReport);
  return (
    <aside className="alerts-panel run-workbench settings-workbench">
      <div className="panel-heading">
        <div>
          <span>Settings</span>
          <strong>Event Store Operations</strong>
        </div>
      </div>

      <section className={`settings-section release-preflight state-${promotionStats.tone}`}>
        <div className="settings-section-head">
          <strong>Release Preflight</strong>
          <Badge tone={promotionStats.tone}>{promotionStats.value}</Badge>
        </div>
        <div className="run-search-stats event-op-stats">
          <Metric label="Blocked" value={String(promotionStats.blockedCount)} />
          <Metric label="Warnings" value={String(promotionStats.warnCount)} />
          <Metric label="Passed" value={String(promotionStats.passedCount)} />
          <Metric label="Window" value={promotionGate ? `${promotionGate.window_hours}h` : "n/a"} />
        </div>
        {promotionGate ? (
          <>
            <div className="preflight-meta">
              <span>{promotionStats.detail}</span>
              <span>{promotionGate.environment}</span>
              <span>{promotionGate.source}</span>
              <span>{formatTime(promotionGate.generated_at)}</span>
            </div>
            <div className="preflight-check-list">
              {promotionGate.checks.map((check) => (
                <div className={`preflight-check-row state-${check.status}`} key={check.name}>
                  <div>
                    <strong>{check.name}</strong>
                    <span>{check.detail}</span>
                  </div>
                  <Badge tone={promotionGateBadgeTone(check.status)}>{check.status}</Badge>
                  {Object.entries(check.evidence).length ? (
                    <div className="preflight-evidence">
                      {Object.entries(check.evidence)
                        .slice(0, 4)
                        .map(([key, value]) => (
                          <span key={key}>
                            <b>{key}</b>
                            {stringifyValue(value as JsonValue)}
                          </span>
                        ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : (
          <PanelEmpty title="Preflight unavailable" detail="Check admin scopes or the Agent API connection." />
        )}
      </section>

      <section className={`settings-section slo-report-section state-${sloTone}`}>
        <div className="settings-section-head">
          <strong>Service Objectives</strong>
          <Badge tone={sloTone}>{sloReport?.status ?? "unavailable"}</Badge>
        </div>
        <div className="run-search-stats event-op-stats slo-report-stats">
          <Metric label="Breached" value={sloReport ? String(sloReport.breached_count) : "n/a"} />
          <Metric label="Watch" value={sloReport ? String(sloReport.at_risk_count) : "n/a"} />
          <Metric label="No data" value={sloReport ? String(sloReport.no_data_count) : "n/a"} />
          <Metric label="Window" value={sloReport ? `${sloReport.window_hours}h` : "n/a"} />
        </div>
        {sloReport ? (
          <>
            <div className="preflight-meta">
              <span>{sloReport.environment}</span>
              <span>{sloReport.source}</span>
              <span>{formatTime(sloReport.generated_at)}</span>
              {sloReport.guardrails.slice(0, 2).map((guardrail) => (
                <span key={guardrail}>{guardrail}</span>
              ))}
            </div>
            <div className="slo-objective-list">
              {sloReport.objectives.map((objective) => (
                <div className={`slo-objective-row state-${sloObjectiveTone(objective)}`} key={objective.name}>
                  <div>
                    <strong>{objective.name}</strong>
                    <span>{objective.detail}</span>
                    <small>{sloBudgetLabel(objective)}</small>
                  </div>
                  <Badge tone={sloObjectiveTone(objective)}>{objective.status}</Badge>
                  <div className="preflight-evidence">
                    {Object.entries(objective.observed)
                      .slice(0, 3)
                      .map(([key, value]) => (
                        <span key={key}>
                          <b>{key}</b>
                          {stringifyValue(value as JsonValue)}
                        </span>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <PanelEmpty title="SLO report unavailable" detail="Check admin scopes or the Agent API connection." />
        )}
      </section>

      <section className={`settings-section automation-plan-section state-${automationTone}`}>
        <div className="settings-section-head">
          <strong>Operations Automation</strong>
          <Badge tone={automationTone}>
            {operationsAutomation ? operationsAutomation.health_status : "unavailable"}
          </Badge>
        </div>
        <div className="run-search-stats event-op-stats automation-plan-stats">
          <Metric label="Actions" value={operationsAutomation ? String(operationsAutomation.action_count) : "n/a"} />
          <Metric
            label="Auto-safe"
            value={operationsAutomation ? String(operationsAutomation.auto_executable_count) : "n/a"}
          />
          <Metric label="Window" value={operationsAutomation ? `${operationsAutomation.window_hours}h` : "n/a"} />
          <Metric label="Source" value={operationsAutomation?.source ?? "n/a"} />
        </div>
        {operationsAutomation ? (
          <>
            <div className="preflight-meta">
              <span>{operationsAutomation.environment}</span>
              <span>{formatTime(operationsAutomation.generated_at)}</span>
              {operationsAutomation.guardrails.slice(0, 2).map((guardrail) => (
                <span key={guardrail}>{guardrail}</span>
              ))}
            </div>
            <div className="automation-action-list">
              {automationActions.slice(0, 6).map((action) => (
                <div className={`automation-action-row state-${automationActionTone(action)}`} key={action.id}>
                  <div className="automation-action-copy">
                    <div className="automation-action-title">
                      <Badge tone={automationPriorityTone(action.priority)}>{action.priority}</Badge>
                      <strong>{action.title}</strong>
                    </div>
                    <span>{action.detail}</span>
                    {action.command ? (
                      <code>
                        {action.command.method} {action.command.path}
                      </code>
                    ) : null}
                  </div>
                  <div className="automation-action-meta">
                    <Badge tone={action.safe_to_auto_execute ? "success" : "warn"}>
                      {action.safe_to_auto_execute ? "auto-safe" : "manual"}
                    </Badge>
                    {action.required_scopes.slice(0, 3).map((scope) => (
                      <span key={scope}>{scope}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <PanelEmpty title="Automation unavailable" detail="Check admin scopes or the Agent API connection." />
        )}
      </section>

      <section className="settings-section promotion-decision-section">
        <div className="settings-section-head">
          <strong>Release Decision</strong>
          {promotionDecisions[0] ? (
            <Badge tone={promotionDecisionBadgeTone(promotionDecisions[0].decision)}>
              {promotionDecisions[0].decision}
            </Badge>
          ) : null}
        </div>
        <form className="promotion-decision-form" onSubmit={onPromotionDecisionSubmit}>
          <div className="settings-action-row">
            <label className="field-label compact">
              Target
              <input
                value={promotionTargetVersion}
                onChange={(event) => onPromotionTargetVersion(event.target.value)}
                maxLength={128}
                required
              />
            </label>
            <label className="field-label compact">
              Decision
              <select
                value={promotionDecision}
                onChange={(event) => onPromotionDecision(event.target.value as PromotionDecision)}
              >
                <option value="deferred">deferred</option>
                <option value="approved">approved</option>
                <option value="rejected">rejected</option>
              </select>
            </label>
          </div>
          <label className="field-label compact">
            Note
            <textarea
              value={promotionDecisionNote}
              onChange={(event) => onPromotionDecisionNote(event.target.value)}
              maxLength={1000}
              rows={3}
              required
            />
          </label>
          <div className="settings-check-grid promotion-override-grid">
            <label className="check-control">
              <input
                type="checkbox"
                checked={promotionOverrideBlocked}
                onChange={(event) => onPromotionOverrideBlocked(event.target.checked)}
              />
              Override blocked
            </label>
            <label className="field-label compact promotion-override-reason">
              Reason
              <input
                value={promotionOverrideReason}
                onChange={(event) => onPromotionOverrideReason(event.target.value)}
                maxLength={500}
                disabled={!promotionOverrideBlocked}
                required={promotionOverrideBlocked}
              />
            </label>
            <button className="primary-button" type="submit" disabled={Boolean(busy)}>
              {decisionBusy ? <Loader2 className="spin" size={16} /> : <Rocket size={16} />}
              Record
            </button>
          </div>
        </form>
        {promotionDecision === "approved" && promotionGate?.status === "blocked" && !promotionOverrideBlocked ? (
          <div className="event-op-result state-danger">
            <div className="event-op-copy">
              <strong>Blocked gate</strong>
              <span>Approval will be rejected unless override is recorded.</span>
            </div>
          </div>
        ) : null}
        {promotionDecisions.length ? (
          <div className="promotion-decision-list">
            {promotionDecisions.slice(0, 5).map((record) => (
              <div className={`promotion-decision-row state-${record.gate_status}`} key={record.id}>
                <div>
                  <strong>{record.target_version}</strong>
                  <span>{record.note}</span>
                  {record.override_blocked ? <small>{record.override_reason}</small> : null}
                </div>
                <div className="promotion-decision-meta">
                  <Badge tone={promotionDecisionBadgeTone(record.decision)}>{record.decision}</Badge>
                  <Badge tone={promotionGateBadgeTone(record.gate_status)}>{record.gate_status}</Badge>
                  <span>{record.actor_user_id}</span>
                  <span>{formatTime(record.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <PanelEmpty title="No release decisions" detail="No persisted decision records." />
        )}
      </section>

      <section className="settings-section audit-export-section">
        <div className="settings-section-head">
          <strong>Audit Export</strong>
          <Badge>NDJSON</Badge>
        </div>
        <div className="settings-action-row">
          <label className="field-label compact">
            Limit
            <input
              value={auditExportLimit}
              onChange={(event) => onAuditExportLimit(event.target.value)}
              inputMode="numeric"
            />
          </label>
          <button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={onAuditExport}>
            {auditExportBusy ? <Loader2 className="spin" size={16} /> : <Download size={16} />}
            Download
          </button>
        </div>
        <div className="settings-check-grid audit-export-grid">
          <label className="check-control">
            <input
              type="checkbox"
              checked={auditExportIncludeEvents}
              onChange={(event) => onAuditExportIncludeEvents(event.target.checked)}
            />
            Events
          </label>
          <label className="check-control">
            <input
              type="checkbox"
              checked={auditExportIncludeToolAudit}
              onChange={(event) => onAuditExportIncludeToolAudit(event.target.checked)}
            />
            Tool audit
          </label>
        </div>
        {auditExportStatus ? (
          <div className="event-op-result state-success">
            <div className="event-op-copy">
              <strong>{auditExportStatus}</strong>
              <span>support-agent-audit-export.ndjson</span>
            </div>
          </div>
        ) : null}
      </section>

      <section className="settings-section">
        <div className="settings-section-head">
          <strong>Backup</strong>
          {backupReport ? (
            <Badge tone={backupReport.verified ? "success" : "danger"}>
              {backupReport.verified ? "verified" : "failed"}
            </Badge>
          ) : null}
        </div>
        <div className="settings-action-row">
          <label className="field-label compact">
            Label
            <input
              value={backupLabel}
              onChange={(event) => onBackupLabel(event.target.value)}
              maxLength={80}
              placeholder="release-2026-07-05"
            />
          </label>
          <button className="primary-button" type="button" disabled={Boolean(busy)} onClick={onBackup}>
            {backupBusy ? <Loader2 className="spin" size={16} /> : <Database size={16} />}
            Create backup
          </button>
        </div>
        {backupReport ? (
          <div className="event-op-result state-success">
            <div className="event-op-copy">
              <strong>{backupReport.backup_path}</strong>
              <span>{backupReport.verification_detail}</span>
            </div>
            <div className="run-search-stats event-op-stats">
              <Metric label="Size" value={formatBytes(backupReport.size_bytes)} />
              <Metric label="Pages" value={String(backupReport.page_count)} />
              <Metric label="Started" value={formatTime(backupReport.started_at)} />
              <Metric label="Done" value={formatTime(backupReport.completed_at)} />
            </div>
          </div>
        ) : null}
      </section>

      <section className="settings-section">
        <div className="settings-section-head">
          <strong>Retention</strong>
          {retentionReport ? (
            <Badge tone={retentionReport.dry_run ? "warn" : "success"}>
              {retentionReport.dry_run ? "preview" : "applied"}
            </Badge>
          ) : null}
        </div>
        <div className="tool-window-grid">
          <label className="field-label compact">
            Events
            <input value={eventRetentionDays} onChange={(event) => onEventRetentionDays(event.target.value)} />
          </label>
          <label className="field-label compact">
            Tool audit
            <input value={toolAuditRetentionDays} onChange={(event) => onToolAuditRetentionDays(event.target.value)} />
          </label>
          <label className="field-label compact">
            Idempotency
            <input value={idempotencyRetentionDays} onChange={(event) => onIdempotencyRetentionDays(event.target.value)} />
          </label>
          <label className="field-label compact">
            Alert outbox
            <input value={alertDeliveryRetentionDays} onChange={(event) => onAlertDeliveryRetentionDays(event.target.value)} />
          </label>
        </div>
        <div className="settings-check-grid">
          <label className="check-control">
            <input
              type="checkbox"
              checked={includeEvents}
              onChange={(event) => onIncludeEvents(event.target.checked)}
            />
            Include events
          </label>
          <label className="check-control">
            <input type="checkbox" checked={vacuum} onChange={(event) => onVacuum(event.target.checked)} />
            Vacuum
          </label>
          <label className="check-control">
            <input
              type="checkbox"
              checked={applyConfirmed}
              onChange={(event) => onApplyConfirmed(event.target.checked)}
            />
            Backup reviewed
          </label>
        </div>
        <div className="settings-command-row">
          <button
            className="secondary-button"
            type="button"
            disabled={Boolean(busy)}
            onClick={() => onRetention(true)}
          >
            {previewBusy ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
            Preview retention
          </button>
          <button
            className="primary-button danger-primary"
            type="button"
            disabled={Boolean(busy) || !canApply}
            onClick={() => onRetention(false)}
            title="Requires a verified backup, dry-run preview, and confirmation"
          >
            {applyBusy ? <Loader2 className="spin" size={16} /> : <ShieldCheck size={16} />}
            Apply retention
          </button>
        </div>
        {retentionReport ? <RetentionReportView report={retentionReport} /> : null}
      </section>

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? "Event-store operation status"}
      </div>
    </aside>
  );
}

function RetentionReportView({ report }: { report: EventStoreRetentionReport }) {
  return (
    <div className={`event-op-result ${report.total_deleted ? "state-warn" : "state-success"}`}>
      <div className="event-op-copy">
        <strong>
          {report.total_candidates} candidate{report.total_candidates === 1 ? "" : "s"}
        </strong>
        <span>
          {report.dry_run
            ? `${report.total_deleted} deleted in preview`
            : `${report.total_deleted} deleted for ${report.tenant_id}`}
        </span>
      </div>
      <div className="run-search-stats event-op-stats">
        <Metric label="Candidates" value={String(report.total_candidates)} />
        <Metric label="Deleted" value={String(report.total_deleted)} />
        <Metric label="Events" value={report.include_events ? "included" : "kept"} />
        <Metric label="Vacuum" value={report.vacuum_performed ? "done" : "no"} />
      </div>
      <div className="retention-table-list">
        {report.tables.map((table) => (
          <div className={table.deleted_count ? "retention-table-row has-delete" : "retention-table-row"} key={table.table_name}>
            <strong>{table.table_name}</strong>
            <Badge tone={table.deleted_count ? "warn" : "neutral"}>{table.action}</Badge>
            <span>
              {table.candidate_count} candidate{table.candidate_count === 1 ? "" : "s"} / {table.deleted_count} deleted
            </span>
            <small>{table.cutoff_at ? `Cutoff ${formatTime(table.cutoff_at)}` : table.reason || "No cutoff"}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function MonitorDrilldownPanel({
  activeAlert,
  filters,
  drilldown,
  stats,
  loading,
  error,
  activeRunId,
  onFilter,
  onSubmit,
  onSearch,
  onOpenMonitorEvent,
  regressionDraft,
  regressionDraftLoadingId,
  regressionDraftError,
  copiedRegressionDraft,
  onDraftRegression,
  onCopyRegressionDraft
}: {
  activeAlert: MonitorAlert | null;
  filters: MonitorDrilldownFilters;
  drilldown: MonitorDrilldownResponse | null;
  stats: MonitorDrilldownUiStats;
  loading: boolean;
  error: string | null;
  activeRunId: string | null;
  onFilter: <Key extends keyof MonitorDrilldownFilters>(
    key: Key,
    value: MonitorDrilldownFilters[Key]
  ) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onSearch: (overrides?: MonitorDrilldownOverrides) => void | Promise<void>;
  onOpenMonitorEvent: (event: MonitorEvent) => void;
  regressionDraft: RegressionDraftResponse | null;
  regressionDraftLoadingId: string | null;
  regressionDraftError: string | null;
  copiedRegressionDraft: boolean;
  onDraftRegression: (event: MonitorEvent) => void | Promise<void>;
  onCopyRegressionDraft: () => void | Promise<void>;
}) {
  const events = drilldown?.events ?? [];
  const activeAlertKey = filters.alertKey ?? "";
  return (
    <div className="monitor-drilldown">
      <form className="run-search-form monitor-drilldown-form" onSubmit={onSubmit}>
        <label className="field-label">
          Alert key
          <input
            value={activeAlertKey}
            onChange={(event) => onFilter("alertKey", event.target.value || null)}
            placeholder="agent:order_status:TIMEOUT"
          />
        </label>
        <div className="drilldown-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={!activeAlert}
            onClick={() => {
              onFilter("alertKey", activeAlert?.key ?? null);
              void onSearch({ alertKey: activeAlert?.key ?? null });
            }}
          >
            <Bell size={16} />
            Active alert
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              onFilter("alertKey", null);
              void onSearch({ alertKey: null });
            }}
          >
            <Layers size={16} />
            All events
          </button>
        </div>
        <div className="run-filter-grid">
          <label className="filter-control">
            <Filter size={14} />
            <select value={filters.intent} onChange={(event) => onFilter("intent", event.target.value)}>
              <option value="">Any intent</option>
              <option value="order_status">Order status</option>
              <option value="refund_or_return">Refund/return</option>
              <option value="billing">Billing</option>
              <option value="technical_support">Tech support</option>
              <option value="account_safety">Safety</option>
              <option value="smalltalk">Smalltalk</option>
            </select>
          </label>
          <label className="filter-control">
            <AlertTriangle size={14} />
            <select value={filters.riskLevel} onChange={(event) => onFilter("riskLevel", event.target.value)}>
              <option value="">Any risk</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>
          <label className="field-label compact">
            Failure
            <input
              value={filters.failureType}
              onChange={(event) => onFilter("failureType", event.target.value)}
              placeholder="TIMEOUT"
            />
          </label>
          <label className="filter-control">
            <User size={14} />
            <select
              value={filters.needsHumanReview}
              onChange={(event) => onFilter("needsHumanReview", event.target.value)}
            >
              <option value="">Any human</option>
              <option value="true">Needs human</option>
              <option value="false">No human</option>
            </select>
          </label>
          <label className="filter-control">
            <BookOpen size={14} />
            <select value={filters.grounded} onChange={(event) => onFilter("grounded", event.target.value)}>
              <option value="">Any grounding</option>
              <option value="true">Grounded</option>
              <option value="false">Ungrounded</option>
            </select>
          </label>
          <label className="filter-control">
            <ShieldCheck size={14} />
            <select
              value={filters.policyCompliant}
              onChange={(event) => onFilter("policyCompliant", event.target.value)}
            >
              <option value="">Any policy</option>
              <option value="true">Compliant</option>
              <option value="false">Violation</option>
            </select>
          </label>
          <label className="filter-control">
            <SlidersHorizontal size={14} />
            <select value={filters.limit} onChange={(event) => onFilter("limit", event.target.value)}>
              <option value="25">25 events</option>
              <option value="50">50 events</option>
              <option value="100">100 events</option>
              <option value="200">200 events</option>
            </select>
          </label>
          <label className="check-control">
            <input
              type="checkbox"
              checked={filters.includeHealthy}
              onChange={(event) => onFilter("includeHealthy", event.target.checked)}
            />
            Include healthy
          </label>
        </div>
        <button className="primary-button" type="submit" disabled={loading}>
          {loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
          Search events
        </button>
      </form>

      <div className="run-search-stats monitor-stats" aria-label="Monitor drilldown stats">
        <Metric label="Total" value={String(stats.totalEvents)} />
        <Metric label="Matching" value={String(stats.matchingEvents)} />
        <Metric label="Alerted" value={formatRate(stats.alertRate)} />
        <Metric label="Human" value={formatRate(stats.humanReviewRate)} />
        <Metric label="Policy" value={formatRate(stats.policyViolationRate)} />
        <Metric label="Failure" value={stats.topFailure} />
      </div>

      {drilldown ? (
        <div className="monitor-bucket-list" aria-label="Monitor failure buckets">
          {drilldown.failure_buckets.slice(0, 5).map((bucket) => (
            <button
              type="button"
              key={bucket.key}
              onClick={() => {
                onFilter("failureType", bucket.key === "none" ? "" : bucket.key);
                void onSearch({ failureType: bucket.key === "none" ? "" : bucket.key });
              }}
            >
              <span>{bucket.key}</span>
              <strong>{bucket.count}</strong>
              <small>{formatRate(bucket.rate)} - {ageLabel(bucket.latest_at)}</small>
            </button>
          ))}
        </div>
      ) : null}

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? `${events.length} monitor events loaded`}
      </div>

      <div className="run-result-list">
        {loading && !drilldown ? <LoadingBlock /> : null}
        {!drilldown && !loading ? (
          <PanelEmpty title="Search monitor events" detail="Use the active alert, a failure type, or a risk filter." />
        ) : null}
        {drilldown && !events.length && !loading ? (
          <PanelEmpty title="No events found" detail="Broaden the alert key or include healthy events." />
        ) : null}
        {events.map((event) => {
          const isSelected = event.run_id === activeRunId;
          return (
            <div className="monitor-event-result" key={event.id}>
              <button
                type="button"
                className={`run-result-card monitor-event-card ${isSelected ? "is-selected" : ""}`}
                onClick={() => onOpenMonitorEvent(event)}
                aria-pressed={isSelected}
              >
                <div className="run-result-top">
                  <Badge tone={riskTone(event.risk_level)}>{event.risk_level}</Badge>
                  <time title={event.timestamp}>{ageLabel(event.timestamp)}</time>
                </div>
                <strong>{event.summary || event.id}</strong>
                <span>{event.run_id}</span>
                <div className="tag-row">
                  <Badge>{event.user_intent}</Badge>
                  <Badge>{event.conversation_id}</Badge>
                  {event.failure_types.slice(0, 3).map((failure, index) => (
                    <Badge tone="warn" key={`${event.id}-${failure}-${index}`}>
                      {failure}
                    </Badge>
                  ))}
                  {!event.grounded ? <Badge tone="warn">ungrounded</Badge> : null}
                  {!event.policy_compliant ? <Badge tone="danger">policy</Badge> : null}
                  {event.needs_human_review ? <Badge tone="warn">human</Badge> : null}
                  {event.pii_leak ? <Badge tone="danger">pii</Badge> : null}
                </div>
                <small>{event.alert_key ?? "no alert key"}</small>
              </button>
              {isSelected ? (
                <RegressionDraftPanel
                  event={event}
                  draft={regressionDraft}
                  loading={regressionDraftLoadingId === event.id}
                  error={regressionDraftError}
                  copied={copiedRegressionDraft}
                  onDraft={() => void onDraftRegression(event)}
                  onCopy={() => void onCopyRegressionDraft()}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RegressionDraftPanel({
  event,
  draft,
  loading,
  error,
  copied,
  onDraft,
  onCopy
}: {
  event: MonitorEvent;
  draft: RegressionDraftResponse | null;
  loading: boolean;
  error: string | null;
  copied: boolean;
  onDraft: () => void;
  onCopy: () => void;
}) {
  const currentDraft = draft?.source.monitor_event_ids.includes(event.id) ? draft : null;
  return (
    <section className="regression-draft-card" aria-label="Regression eval draft">
      <div className="regression-draft-actions">
        <button type="button" className="secondary-button" onClick={onDraft} disabled={loading}>
          {loading ? <Loader2 className="spin" size={15} /> : <FileCheck2 size={15} />}
          Draft eval
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={onCopy}
          disabled={!currentDraft}
        >
          {copied ? <Check size={15} /> : <Copy size={15} />}
          {copied ? "Copied" : "Copy JSON"}
        </button>
      </div>
      {error ? <div className="inline-error">{error}</div> : null}
      {currentDraft ? (
        <>
          <div className="regression-draft-meta">
            <Badge>{currentDraft.target_file}</Badge>
            <Badge>{currentDraft.draft.case_id}</Badge>
            {currentDraft.redactions.length ? <Badge tone="warn">redacted</Badge> : null}
          </div>
          {currentDraft.warnings.length ? (
            <div className="regression-draft-warnings">
              {currentDraft.warnings.slice(0, 3).map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
            </div>
          ) : null}
          <pre>{currentDraft.draft_json}</pre>
        </>
      ) : null}
    </section>
  );
}

function FeedbackWorkbenchPanel({
  results,
  loading,
  error,
  rating,
  runId,
  userId,
  conversationId,
  createdAfter,
  createdBefore,
  limit,
  order,
  currentRunId,
  selectedFeedbackId,
  feedbackReviews,
  feedbackReviewStatus,
  feedbackReviewAssignee,
  feedbackReviewNote,
  feedbackReviewLoadingId,
  feedbackReviewError,
  regressionDraft,
  regressionDraftLoadingId,
  regressionDraftError,
  copiedRegressionDraft,
  onRating,
  onRunId,
  onUserId,
  onConversationId,
  onCreatedAfter,
  onCreatedBefore,
  onLimit,
  onOrder,
  onReviewStatus,
  onReviewAssignee,
  onReviewNote,
  onSubmit,
  onSearch,
  onOpenFeedback,
  onSubmitReview,
  onDraftFeedback,
  onCopyRegressionDraft
}: {
  results: FeedbackSearchResponse | null;
  loading: boolean;
  error: string | null;
  rating: string;
  runId: string;
  userId: string;
  conversationId: string;
  createdAfter: string;
  createdBefore: string;
  limit: string;
  order: "asc" | "desc";
  currentRunId: string | null;
  selectedFeedbackId: string | null;
  feedbackReviews: FeedbackReviewEvent[];
  feedbackReviewStatus: FeedbackReviewStatus;
  feedbackReviewAssignee: string;
  feedbackReviewNote: string;
  feedbackReviewLoadingId: string | null;
  feedbackReviewError: string | null;
  regressionDraft: RegressionDraftResponse | null;
  regressionDraftLoadingId: string | null;
  regressionDraftError: string | null;
  copiedRegressionDraft: boolean;
  onRating: (value: string) => void;
  onRunId: (value: string) => void;
  onUserId: (value: string) => void;
  onConversationId: (value: string) => void;
  onCreatedAfter: (value: string) => void;
  onCreatedBefore: (value: string) => void;
  onLimit: (value: string) => void;
  onOrder: (value: "asc" | "desc") => void;
  onReviewStatus: (value: FeedbackReviewStatus) => void;
  onReviewAssignee: (value: string) => void;
  onReviewNote: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onSearch: (overrides?: FeedbackSearchOverrides) => void | Promise<void>;
  onOpenFeedback: (feedback: AgentFeedback) => void;
  onSubmitReview: (feedback: AgentFeedback) => void | Promise<void>;
  onDraftFeedback: (feedback: AgentFeedback) => void | Promise<void>;
  onCopyRegressionDraft: () => void | Promise<void>;
}) {
  const items = results?.items ?? [];
  const summary = results?.summary ?? null;
  const reviewQueue = results?.review_queue ?? null;
  const topReasons = summary?.counts_by_reason.slice(0, 4) ?? [];
  const reviewQueueByFeedbackId = useMemo(
    () => new Map((reviewQueue?.items ?? []).map((item) => [item.feedback_id, item])),
    [reviewQueue]
  );
  return (
    <aside className="alerts-panel run-workbench feedback-workbench">
      <div className="panel-heading">
        <div>
          <span>Feedback Loop</span>
          <strong>{summary ? `${summary.total_count} response ratings` : "Human signal"}</strong>
        </div>
      </div>

      <form className="run-search-form" onSubmit={onSubmit}>
        <div className="run-filter-grid">
          <label className="filter-control">
            <Filter size={14} />
            <select value={rating} onChange={(event) => onRating(event.target.value)} aria-label="Filter feedback rating">
              <option value="">Any rating</option>
              <option value="negative">Negative</option>
              <option value="positive">Positive</option>
            </select>
          </label>
          <label className="filter-control">
            <SlidersHorizontal size={14} />
            <select value={order} onChange={(event) => onOrder(event.target.value as "asc" | "desc")} aria-label="Feedback order">
              <option value="desc">Newest first</option>
              <option value="asc">Oldest first</option>
            </select>
          </label>
        </div>
        <label className="search-control">
          <Search size={14} />
          <input value={runId} onChange={(event) => onRunId(event.target.value)} placeholder="run_id" aria-label="Filter feedback by run id" />
        </label>
        <label className="field-label">
          User
          <input value={userId} onChange={(event) => onUserId(event.target.value)} placeholder="user id" />
        </label>
        <label className="field-label">
          Conversation
          <input value={conversationId} onChange={(event) => onConversationId(event.target.value)} placeholder="conv_..." />
        </label>
        <div className="run-filter-grid">
          <label className="field-label compact">
            From
            <input value={createdAfter} onChange={(event) => onCreatedAfter(event.target.value)} placeholder="2026-07-01T00:00:00Z" />
          </label>
          <label className="field-label compact">
            To
            <input value={createdBefore} onChange={(event) => onCreatedBefore(event.target.value)} placeholder="2026-07-05T23:59:59Z" />
          </label>
        </div>
        <div className="run-filter-grid">
          <label className="filter-control">
            <SlidersHorizontal size={14} />
            <select value={limit} onChange={(event) => onLimit(event.target.value)} aria-label="Feedback result limit">
              <option value="25">25 rows</option>
              <option value="50">50 rows</option>
              <option value="100">100 rows</option>
              <option value="200">200 rows</option>
            </select>
          </label>
          <button className="primary-button compact-action" type="submit" disabled={loading}>
            {loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
            Search
          </button>
        </div>
        {currentRunId ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => onSearch({ runId: currentRunId })}
            disabled={loading}
          >
            <ClipboardList size={16} />
            Use selected run
          </button>
        ) : null}
      </form>

      <div className="run-search-stats" aria-label="Feedback summary stats">
        <Metric label="Negative" value={String(summary?.negative_count ?? 0)} />
        <Metric label="Positive" value={String(summary?.positive_count ?? 0)} />
        <Metric label="Neg rate" value={formatRate(summary?.negative_rate ?? 0)} />
        <Metric label="Window" value={summary?.window_start ? "filtered" : "all"} />
      </div>

      {reviewQueue ? (
        <div className="run-search-stats feedback-review-stats" aria-label="Feedback review backlog stats">
          <Metric label="Open" value={String(reviewQueue.summary.unresolved_count)} />
          <Metric label="Unassigned" value={String(reviewQueue.summary.unassigned_unresolved_count)} />
          <Metric label="Stale" value={String(reviewQueue.summary.stale_unresolved_count)} />
          <Metric label="Reviewed" value={String(reviewQueue.summary.reviewed_count)} />
        </div>
      ) : null}

      {topReasons.length ? (
        <div className="tool-summary-list">
          {topReasons.map((reason) => (
            <div className="tool-summary-row" key={reason.reason}>
              <span>{reason.reason}</span>
              <strong>{reason.count}</strong>
            </div>
          ))}
        </div>
      ) : null}

      <div className={error ? "inline-error" : "sr-only"} role="status" aria-live="polite">
        {error ?? `${items.length} feedback records loaded`}
      </div>

      <div className="run-result-list">
        {loading && !results ? <LoadingBlock /> : null}
        {!loading && results && !items.length ? (
          <PanelEmpty title="No feedback found" detail="Try a broader filter or wait for users to rate responses." />
        ) : null}
        {!results && !loading ? (
          <PanelEmpty title="Search response feedback" detail="Review thumbs up/down reasons linked to real agent runs." />
        ) : null}
        {items.map((feedback) => {
          const isSelected = feedback.id === selectedFeedbackId;
          const reviewState = reviewQueueByFeedbackId.get(feedback.id) ?? null;
          return (
            <div className="monitor-event-result" key={feedback.id}>
              <button
                type="button"
                className={`run-result-card ${isSelected ? "is-selected" : ""}`}
                onClick={() => onOpenFeedback(feedback)}
                aria-pressed={isSelected}
              >
                <div className="run-result-top">
                  <div className="feedback-card-badges">
                    <Badge tone={feedback.rating === "positive" ? "success" : "danger"}>{feedback.rating}</Badge>
                    {reviewState ? (
                      <Badge tone={feedbackReviewQueueTone(reviewState.current_status)}>
                        {reviewState.current_status}
                      </Badge>
                    ) : null}
                  </div>
                  <time title={feedback.created_at}>{ageLabel(feedback.created_at)}</time>
                </div>
                <strong>{feedback.run_id}</strong>
                <span>{feedback.conversation_id}</span>
                {feedback.reasons.length || reviewState ? (
                  <div className="tag-row">
                    {feedback.reasons.slice(0, 4).map((reason) => (
                      <Badge tone={feedback.rating === "negative" ? "warn" : "success"} key={reason}>
                        {reason}
                      </Badge>
                    ))}
                    {reviewState?.is_unassigned ? <Badge tone="warn">unassigned</Badge> : null}
                    {reviewState?.is_stale ? <Badge tone="danger">stale</Badge> : null}
                  </div>
                ) : null}
                {feedback.comment ? <p className="feedback-comment">{feedback.comment}</p> : null}
                <div className="run-result-meta">
                  <span>{feedback.user_id}</span>
                  <span>{feedback.source}</span>
                  <span>{feedback.id}</span>
                  {reviewState ? <span>{reviewState.review_count} review events</span> : null}
                  {reviewState?.assignee_user_id ? <span>assignee {reviewState.assignee_user_id}</span> : null}
                </div>
              </button>
              {isSelected ? (
                <>
                  <FeedbackRegressionDraftPanel
                    feedback={feedback}
                    draft={regressionDraft}
                    loading={regressionDraftLoadingId === feedback.id}
                    error={regressionDraftError}
                    copied={copiedRegressionDraft}
                    onDraft={() => void onDraftFeedback(feedback)}
                    onCopy={() => void onCopyRegressionDraft()}
                  />
                  <FeedbackReviewPanel
                    feedback={feedback}
                    reviews={feedbackReviews}
                    status={feedbackReviewStatus}
                    assignee={feedbackReviewAssignee}
                    note={feedbackReviewNote}
                    loading={feedbackReviewLoadingId === feedback.id}
                    error={feedbackReviewError}
                    onStatus={onReviewStatus}
                    onAssignee={onReviewAssignee}
                    onNote={onReviewNote}
                    onSubmit={() => void onSubmitReview(feedback)}
                  />
                </>
              ) : null}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function FeedbackRegressionDraftPanel({
  feedback,
  draft,
  loading,
  error,
  copied,
  onDraft,
  onCopy
}: {
  feedback: AgentFeedback;
  draft: RegressionDraftResponse | null;
  loading: boolean;
  error: string | null;
  copied: boolean;
  onDraft: () => void;
  onCopy: () => void;
}) {
  const currentDraft = draft?.source.feedback_id === feedback.id ? draft : null;
  return (
    <section className="regression-draft-card" aria-label="Feedback regression eval draft">
      <div className="regression-draft-actions">
        <button type="button" className="secondary-button" onClick={onDraft} disabled={loading}>
          {loading ? <Loader2 className="spin" size={15} /> : <FileCheck2 size={15} />}
          Draft eval
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={onCopy}
          disabled={!currentDraft}
        >
          {copied ? <Check size={15} /> : <Copy size={15} />}
          {copied ? "Copied" : "Copy JSON"}
        </button>
      </div>
      {error ? <div className="inline-error">{error}</div> : null}
      {currentDraft ? (
        <>
          <div className="regression-draft-meta">
            <Badge>{currentDraft.target_file}</Badge>
            <Badge>{currentDraft.draft.case_id}</Badge>
            {currentDraft.source.feedback_reasons?.slice(0, 2).map((reason) => (
              <Badge tone="warn" key={reason}>
                {reason}
              </Badge>
            ))}
          </div>
          {currentDraft.warnings.length ? (
            <div className="regression-draft-warnings">
              {currentDraft.warnings.slice(0, 3).map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
            </div>
          ) : null}
          <pre>{currentDraft.draft_json}</pre>
        </>
      ) : null}
    </section>
  );
}

function FeedbackReviewPanel({
  feedback,
  reviews,
  status,
  assignee,
  note,
  loading,
  error,
  onStatus,
  onAssignee,
  onNote,
  onSubmit
}: {
  feedback: AgentFeedback;
  reviews: FeedbackReviewEvent[];
  status: FeedbackReviewStatus;
  assignee: string;
  note: string;
  loading: boolean;
  error: string | null;
  onStatus: (value: FeedbackReviewStatus) => void;
  onAssignee: (value: string) => void;
  onNote: (value: string) => void;
  onSubmit: () => void;
}) {
  const visibleReviews = reviews.filter((review) => review.feedback_id === feedback.id);
  const latestReview = visibleReviews.at(-1);
  return (
    <section className="feedback-review-card" aria-label="Feedback review trail">
      <div className="feedback-review-heading">
        <div>
          <span>Review Trail</span>
          <strong>{latestReview ? `${visibleReviews.length} events` : "Awaiting review"}</strong>
        </div>
        {latestReview ? <Badge tone={feedbackReviewTone(latestReview.status)}>{latestReview.status}</Badge> : null}
      </div>
      <form
        className="feedback-review-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="run-filter-grid">
          <label className="field-label compact">
            Status
            <select
              value={status}
              onChange={(event) => onStatus(event.target.value as FeedbackReviewStatus)}
            >
              <option value="acknowledged">Acknowledged</option>
              <option value="investigating">Investigating</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
            </select>
          </label>
          <label className="field-label compact">
            Assignee
            <input
              value={assignee}
              onChange={(event) => onAssignee(event.target.value)}
              placeholder="operator id"
              maxLength={128}
            />
          </label>
        </div>
        <label className="field-label compact">
          Note
          <textarea
            value={note}
            onChange={(event) => onNote(event.target.value)}
            rows={3}
            maxLength={1000}
            placeholder="Investigation note"
          />
        </label>
        <button className="primary-button compact-action" type="submit" disabled={loading}>
          {loading ? <Loader2 className="spin" size={16} /> : <UserPlus size={16} />}
          Record review
        </button>
      </form>
      {error ? <div className="inline-error">{error}</div> : null}
      <div className="feedback-review-list">
        {loading && !visibleReviews.length ? <span className="feedback-review-empty">Loading trail</span> : null}
        {!loading && !visibleReviews.length ? (
          <span className="feedback-review-empty">No review events</span>
        ) : null}
        {visibleReviews.map((review) => (
          <article className="feedback-review-row" key={review.id}>
            <div className="feedback-review-row-top">
              <Badge tone={feedbackReviewTone(review.status)}>{review.status}</Badge>
              <time title={review.created_at}>{formatTime(review.created_at)}</time>
            </div>
            <span>{review.assignee_user_id ? `Assignee ${review.assignee_user_id}` : "Unassigned"}</span>
            {review.note ? <p>{truncate(review.note, 180)}</p> : null}
            <small>Actor {review.actor_user_id}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function feedbackReviewTone(
  status: FeedbackReviewStatus
): "neutral" | "success" | "warn" | "danger" {
  if (status === "resolved") {
    return "success";
  }
  if (status === "dismissed") {
    return "neutral";
  }
  return "warn";
}

function feedbackReviewQueueTone(
  status: FeedbackReviewQueueStatus
): "neutral" | "success" | "warn" | "danger" {
  if (status === "unreviewed") {
    return "danger";
  }
  return feedbackReviewTone(status);
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
        {hits.map((hit, index) => (
          <article
            className="run-result-card knowledge-hit-card"
            key={`${hit.document_id}-${hit.chunk_id}-${index}`}
          >
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
            {step.chips.slice(0, 3).map((chip, index) => (
              <Badge key={`${step.id}-${chip}-${index}`}>{chip}</Badge>
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
  onDownloadBrief,
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
  onDownloadBrief: () => void;
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
          onDownloadBrief={onDownloadBrief}
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
        onDownloadBrief={onDownloadBrief}
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
  onDownloadBrief,
  copiedBrief,
  busy
}: {
  brief: IncidentBrief;
  snapshot: ConsoleSnapshot | null;
  activeAlert: MonitorAlert | null;
  evalReport: EvalReport | null;
  onRunEval: () => void;
  onCopyBrief: () => void;
  onDownloadBrief: () => void;
  copiedBrief: boolean;
  busy: string | null;
}) {
  const run = snapshot?.incident?.run ?? null;
  const readinessFailures = snapshot?.ready?.checks.filter((check) => check.status === "failed") ?? [];
  const promotionGate = snapshot?.promotionGate ?? null;
  const latestEvalGate = snapshot?.evalGateLatest ?? null;
  const evalGateRecords = snapshot?.evalGateRecords ?? [];
  const incidentTimeline = snapshot?.incidentTimeline ?? null;
  const timelineEntries = incidentTimeline?.entries.slice(0, 12) ?? [];
  const evalFailureRows = evalReport
    ? evalReport.results
        .filter((result) => !result.passed)
        .map((result) => ({
          key: result.case_id,
          score: result.score,
          title: result.case_id,
          detail: result.failures.join("; ") || "No failure detail returned."
        }))
    : latestEvalGate?.case_results
        .filter((result) => !result.passed)
        .map((result) => ({
          key: result.case_id,
          score: result.score,
          title: result.case_id,
          detail: result.failures.join("; ") || "No failure detail returned."
        })) ?? [];
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
          <Metric label="Eval gate" value={evalGateTileLabel(evalReport, latestEvalGate)} />
        </div>
        <div className="brief-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onCopyBrief}
            disabled={!snapshot || busy === "brief-copy"}
          >
            {busy === "brief-copy" ? <Loader2 className="spin" size={16} /> : <Copy size={16} />}
            {copiedBrief ? "Copied" : "Copy brief"}
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={onDownloadBrief}
            disabled={!snapshot || busy === "brief-download"}
          >
            {busy === "brief-download" ? <Loader2 className="spin" size={16} /> : <Download size={16} />}
            Download .md
          </button>
          <button className="primary-button" type="button" onClick={onRunEval} disabled={busy === "eval"}>
            {busy === "eval" ? <Loader2 className="spin" size={16} /> : <FileCheck2 size={16} />}
            Run eval gate
          </button>
        </div>
      </section>

      <section className="evidence-card incident-timeline-card">
        <div className="evidence-card-head">
          <div>
            <span>Incident Timeline</span>
            <strong>{incidentTimeline ? `${incidentTimeline.entry_count} sanitized events` : "Unavailable"}</strong>
          </div>
          <Badge tone={incidentTimeline ? "success" : "neutral"}>
            {incidentTimeline?.run_source ?? "none"}
          </Badge>
        </div>
        {incidentTimeline ? (
          <>
            <div className="timeline-redactions">
              {incidentTimeline.redactions.slice(0, 4).map((redaction) => (
                <span key={redaction}>{redaction}</span>
              ))}
            </div>
            <div className="incident-timeline-list">
              {timelineEntries.map((entry) => (
                <article className={`incident-timeline-row state-${entry.tone}`} key={`${entry.event_type}-${entry.sequence}`}>
                  <div className="incident-timeline-marker" />
                  <div className="incident-timeline-copy">
                    <div>
                      <strong>{entry.title}</strong>
                      <Badge tone={entry.tone}>{entry.source}</Badge>
                    </div>
                    <span>{entry.detail}</span>
                    <time title={entry.occurred_at}>{formatTime(entry.occurred_at)}</time>
                    {Object.entries(entry.evidence).length ? (
                      <div className="preflight-evidence incident-timeline-evidence">
                        {Object.entries(entry.evidence)
                          .slice(0, 4)
                          .map(([key, value]) => (
                            <span key={key}>
                              <b>{key}</b>
                              {stringifyValue(value as JsonValue)}
                            </span>
                          ))}
                      </div>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          </>
        ) : (
          <PanelEmpty title="Timeline unavailable" detail="Check feedback, audit, monitor, and events scopes." />
        )}
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <div>
            <span>Latest Eval Gate</span>
            <strong>{latestEvalGate?.suite_id ?? "staging_release_gate"}</strong>
          </div>
          <Badge tone={evalGateBadgeTone(latestEvalGate)}>{latestEvalGate?.status ?? "not run"}</Badge>
        </div>
        {latestEvalGate ? (
          <>
            <div className="brief-grid">
              <Metric label="Result" value={formatEvalStatus(evalReport, latestEvalGate)} />
              <Metric label="Actor" value={latestEvalGate.actor_user_id ?? "unknown"} />
              <Metric label="Trigger" value={latestEvalGate.trigger} />
              <Metric label="Runtime" value={`${latestEvalGate.duration_ms}ms`} />
            </div>
            <div className="gate-meta">
              <span>{latestEvalGate.environment}</span>
              <span>{latestEvalGate.run_id ?? "no run context"}</span>
              <span>{latestEvalGate.alert_key ?? "no alert context"}</span>
              <time title={latestEvalGate.completed_at}>{ageLabel(latestEvalGate.completed_at)}</time>
            </div>
          </>
        ) : (
          <PanelEmpty title="Eval gate not run" detail="Run the staging gate to persist an audit record." />
        )}
      </section>

      {evalGateRecords.length > 1 ? (
        <section className="evidence-card">
          <div className="evidence-card-head">
            <strong>Recent Gate History</strong>
          </div>
          <div className="eval-history-list">
            {evalGateRecords.map((record) => (
              <article className="eval-row" key={record.id}>
                <Badge tone={evalGateBadgeTone(record)}>{record.status}</Badge>
                <div>
                  <strong>{gateHistoryTitle(record)}</strong>
                  <span>
                    {record.failed_case_ids.length
                      ? `Failed: ${record.failed_case_ids.join(", ")}`
                      : record.error_message ?? `${record.passed ?? 0}/${record.total ?? 0} passed`}
                  </span>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}

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
        {promotionGate ? (
          <>
            <p className="muted">
              Promotion gate is {promotionGate.status} from {promotionGate.source} evidence over{" "}
              {promotionGate.window_hours}h.
            </p>
            <div className="readiness-list">
              {promotionGate.checks.map((check) => (
                <div key={check.name}>
                  <Badge tone={promotionGateBadgeTone(check.status)}>{check.status}</Badge>
                  <strong>{check.name}</strong>
                  <span>{check.detail}</span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="muted">Promotion gate unavailable; check admin scopes and the Agent API connection.</p>
        )}
      </section>

      {evalReport || latestEvalGate?.case_results.length ? (
        <section className="evidence-card">
          <div className="evidence-card-head">
            <strong>Eval Failures</strong>
          </div>
          {evalFailureRows.length ? (
            evalFailureRows.map((result) => (
              <article className="eval-row" key={result.key}>
                <Badge tone="danger">{Math.round(result.score * 100)}%</Badge>
                <div>
                  <strong>{result.title}</strong>
                  <span>{result.detail}</span>
                </div>
              </article>
            ))
          ) : (
            <PanelEmpty title="Eval gate passed" detail="All bundled staging suites passed in this environment." />
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
            <article className="citation-row" key={`${hit.document_id}-${hit.chunk_id}-${index}`}>
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
                  {finding.risk_level} - block={String(finding.should_block)} - escalate=
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
                grounded={String(event.grounded)} - compliant={String(event.policy_compliant)}
              </span>
              {event.failure_types.length ? (
                <div className="tag-row">
                  {event.failure_types.map((failure, index) => (
                    <Badge tone="warn" key={`${event.id}-${failure}-${index}`}>
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
                  {record.status} - {record.latency_ms}ms - replayed={String(record.replayed)}
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

async function writeClipboardText(value: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Embedded browsers can expose clipboard APIs but deny writes.
    }
  }

  const textArea = document.createElement("textarea");
  textArea.value = value;
  textArea.setAttribute("readonly", "true");
  textArea.style.position = "fixed";
  textArea.style.left = "-9999px";
  textArea.style.opacity = "0";
  document.body.appendChild(textArea);
  textArea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textArea);
  if (!copied) {
    throw new Error("Clipboard is not available");
  }
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
            {(run.retrieval?.selected_sources ?? []).map((source, index) => (
              <Badge key={`${source}-${index}`}>{source}</Badge>
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

function buildEventRetentionRequestKey(input: {
  eventRetentionDays: string;
  toolAuditRetentionDays: string;
  idempotencyRetentionDays: string;
  alertDeliveryRetentionDays: string;
  includeEvents: boolean;
  vacuum: boolean;
}) {
  return [
    input.eventRetentionDays,
    input.toolAuditRetentionDays,
    input.idempotencyRetentionDays,
    input.alertDeliveryRetentionDays,
    input.includeEvents ? "events" : "no-events",
    input.vacuum ? "vacuum" : "no-vacuum"
  ].join("|");
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

function formatBytes(value: number) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB"];
  let next = value / 1024;
  for (const unit of units) {
    if (next < 1024 || unit === units[units.length - 1]) {
      return `${next.toFixed(next >= 10 ? 0 : 1)} ${unit}`;
    }
    next /= 1024;
  }
  return `${value} B`;
}

function evalGateTileLabel(report: EvalReport | null, gate: EvalGateRecord | null) {
  if (report) {
    return `${report.passed}/${report.total}`;
  }
  if (!gate) {
    return "not run";
  }
  if (gate.status === "error") {
    return "error";
  }
  if (typeof gate.passed === "number" && typeof gate.total === "number") {
    return `${gate.passed}/${gate.total}`;
  }
  return gate.status;
}

function evalGateBadgeTone(record: EvalGateRecord | null): "neutral" | "success" | "warn" | "danger" {
  if (!record) {
    return "neutral";
  }
  if (record.status === "passed") {
    return "success";
  }
  if (record.status === "failed") {
    return "danger";
  }
  return "warn";
}

function promotionGateBadgeTone(status: "passed" | "warn" | "blocked" | null): "neutral" | "success" | "warn" | "danger" {
  if (status === "passed") {
    return "success";
  }
  if (status === "warn") {
    return "warn";
  }
  if (status === "blocked") {
    return "danger";
  }
  return "neutral";
}

function promotionDecisionBadgeTone(decision: PromotionDecision): "neutral" | "success" | "warn" | "danger" {
  if (decision === "approved") {
    return "success";
  }
  if (decision === "rejected") {
    return "danger";
  }
  return "warn";
}

function automationPlanTone(plan: OperationsAutomationPlan | null): "neutral" | "success" | "warn" | "danger" {
  if (!plan) {
    return "neutral";
  }
  if (plan.health_status === "critical") {
    return "danger";
  }
  if (plan.health_status === "degraded") {
    return "warn";
  }
  return plan.action_count > 0 && !plan.actions.some((action) => action.kind === "no_action_required")
    ? "warn"
    : "success";
}

function automationPriorityTone(priority: OperationsAutomationAction["priority"]): "neutral" | "success" | "warn" | "danger" {
  if (priority === "P0") {
    return "danger";
  }
  if (priority === "P1") {
    return "warn";
  }
  return priority === "P2" ? "neutral" : "success";
}

function automationActionTone(action: OperationsAutomationAction): "neutral" | "success" | "warn" | "danger" {
  if (action.priority === "P0") {
    return "danger";
  }
  if (action.priority === "P1" || !action.safe_to_auto_execute) {
    return "warn";
  }
  return action.kind === "no_action_required" ? "success" : "neutral";
}

function sloReportTone(report: SloReportResponse | null): "neutral" | "success" | "warn" | "danger" {
  if (!report || report.status === "unknown") {
    return "neutral";
  }
  if (report.status === "breached") {
    return "danger";
  }
  return report.status === "watch" ? "warn" : "success";
}

function sloObjectiveTone(objective: SloObjectiveResult): "neutral" | "success" | "warn" | "danger" {
  if (objective.status === "breached") {
    return "danger";
  }
  if (objective.status === "at_risk") {
    return "warn";
  }
  if (objective.status === "met") {
    return "success";
  }
  return "neutral";
}

function sloTileClass(status: SloReportResponse["status"] | null) {
  if (status === "breached") {
    return "is-bad";
  }
  if (status === "watch") {
    return "is-warn";
  }
  return "";
}

function sloBudgetLabel(objective: SloObjectiveResult) {
  if (objective.error_budget_remaining === null) {
    return "Budget n/a";
  }
  return `Budget ${Math.round(objective.error_budget_remaining * 100)}%`;
}

function promotionGateTileClass(status: "passed" | "warn" | "blocked" | null) {
  if (status === "blocked") {
    return "is-bad";
  }
  if (status === "warn") {
    return "is-warn";
  }
  return "";
}

function gateHistoryTitle(record: EvalGateRecord) {
  const result =
    typeof record.passed === "number" && typeof record.total === "number"
      ? `${record.passed}/${record.total}`
      : record.status;
  return `${record.gate_name}:${record.runner} - ${result} - ${ageLabel(record.completed_at)}`;
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

function relativeTimeLabel(value: string | null | undefined) {
  if (!value) {
    return "n/a";
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "n/a";
  }
  const diffMs = timestamp - Date.now();
  if (diffMs <= 0) {
    return ageLabel(value);
  }
  const minutes = Math.ceil(diffMs / 60000);
  if (minutes < 60) {
    return `in ${Math.max(1, minutes)}m`;
  }
  const hours = Math.ceil(minutes / 60);
  if (hours < 48) {
    return `in ${hours}h`;
  }
  return `in ${Math.ceil(hours / 24)}d`;
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

function formatDurationSeconds(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  if (value < 60) {
    return `${value}s`;
  }
  const minutes = Math.round(value / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  return `${(value / 3600).toFixed(1)}h`;
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

function readInitialConsoleState(): ConsoleUrlState {
  if (typeof window === "undefined") {
    return DEFAULT_CONSOLE_URL_STATE;
  }
  return parseConsoleState(window.location.search);
}

function syncConsoleUrl(state: ConsoleUrlState) {
  if (typeof window === "undefined") {
    return;
  }
  const query = serializeConsoleState(state);
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextUrl !== currentUrl) {
    window.history.replaceState(window.history.state, "", nextUrl);
  }
}

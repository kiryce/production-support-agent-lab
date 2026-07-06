import { describe, expect, it } from "vitest";
import {
  alertSnapshotFingerprint,
  buildAlertDispatchResultStats,
  buildAlertWebhookReceiptStats,
  buildIncidentBrief,
  buildKnowledgeSearchStats,
  buildMonitorAlertDeliveryStats,
  buildMonitorDrilldownStats,
  buildMonitorTriageHealthStats,
  buildOpsMetrics,
  buildPromotionGateStats,
  buildRunSearchStats,
  buildSnapshotFreshness,
  buildToolAuditStats,
  canCloseAlertDelivery,
  canReplayAlertDelivery,
  alertWebhookReceiptTone,
  deliveryStatusTone,
  diffAlertQueue,
  filterAndSortAlerts,
  formatEvalStatus,
  feedbackReviewSnapshotFingerprint,
  incidentBriefFromResponse,
  latestEvalGateRecord,
  reconcileSnapshotSelection
} from "../src/shared/ops";
import type {
  AlertDispatchReport,
  AlertDeliveryRecord,
  AlertWebhookReceiptRecord,
  ConsoleSnapshot,
  EvalGateRecord,
  FeedbackReviewEvent,
  FeedbackReviewQueueItem,
  KnowledgeSearchResponse,
  MonitorAlert,
  MonitorAlertDeliverySummary,
  MonitorDrilldownResponse,
  MonitorTriageMetricsResponse
} from "../src/shared/types";

function alert(overrides: Partial<MonitorAlert>): MonitorAlert {
  return {
    severity: "P2",
    key: "agent:order:TIMEOUT",
    count: 1,
    reason: "TIMEOUT clustered across 1 event(s)",
    first_seen_at: "2026-07-04T00:00:00.000Z",
    last_seen_at: "2026-07-04T00:05:00.000Z",
    sample_event_ids: ["mon_1"],
    sample_run_ids: ["run_1"],
    status: "open",
    assignee_user_id: null,
    last_triage_event_id: null,
    last_triage_at: null,
    last_triage_note: null,
    new_events_since_triage: false,
    ...overrides
  };
}

function feedbackReview(overrides: Partial<FeedbackReviewEvent>): FeedbackReviewEvent {
  return {
    id: "fdbrv_1",
    tenant_id: "demo_tenant",
    feedback_id: "fdbk_1",
    conversation_id: "conv_1",
    run_id: "run_1",
    status: "acknowledged",
    assignee_user_id: null,
    actor_user_id: "operator",
    note: "reviewing",
    created_at: "2026-07-04T00:00:00.000Z",
    ...overrides
  };
}

function feedbackQueueItem(
  overrides: Partial<FeedbackReviewQueueItem>
): FeedbackReviewQueueItem {
  return {
    feedback_id: "fdbk_1",
    run_id: "run_1",
    conversation_id: "conv_1",
    user_id: "user_1",
    rating: "negative",
    reasons: ["not_helpful"],
    source: "user",
    feedback_created_at: "2026-07-04T00:00:00.000Z",
    current_status: "unreviewed",
    review_count: 0,
    latest_review_id: null,
    latest_review_at: null,
    assignee_user_id: null,
    is_unresolved: true,
    is_unassigned: true,
    is_stale: false,
    age_hours: 1,
    ...overrides
  };
}

function triageMetrics(
  overrides: Partial<Omit<MonitorTriageMetricsResponse, "window">> & {
    window?: Partial<MonitorTriageMetricsResponse["window"]>;
  } = {}
): MonitorTriageMetricsResponse {
  const window = {
    conversation_id: null,
    created_after: null,
    created_before: null,
    limit: 500,
    order: "desc" as const,
    first_seen_at: "2026-07-04T00:00:00.000Z",
    last_seen_at: "2026-07-04T00:05:00.000Z",
    ...overrides.window
  };
  return {
    source: "event_store",
    generated_at: "2026-07-04T00:06:00.000Z",
    total_events: 2,
    healthy_events: 0,
    alerted_events: 2,
    alert_rate: 1,
    grounded_rate: 0.5,
    policy_compliance_rate: 0.5,
    human_review_rate: 1,
    high_risk_events: 1,
    critical_events: 0,
    ungrounded_events: 1,
    policy_violations: 1,
    human_review_events: 2,
    pii_leak_events: 0,
    by_risk_level: { high: 1 },
    by_intent: { order_status: 2 },
    by_failure_type: { TIMEOUT: 2 },
    by_alert_failure_type: { TIMEOUT: 2 },
    alert_count: 2,
    active_alert_count: 1,
    resolved_alert_count: 1,
    silenced_alert_count: 0,
    assigned_alert_count: 1,
    untriaged_alert_count: 0,
    unassigned_active_alert_count: 1,
    new_events_since_triage_count: 1,
    stale_active_alert_count: 1,
    stale_threshold_seconds: 3600,
    by_severity: { P0: 0, P1: 1, P2: 1, P3: 0 },
    active_by_severity: { P0: 0, P1: 1, P2: 0, P3: 0 },
    by_status: { open: 1, acknowledged: 0, investigating: 0, resolved: 1, silenced: 0 },
    worst_active_severity: "P1",
    health_status: "degraded",
    mtta_seconds: 120,
    mttr_seconds: 240,
    oldest_active_alert_at: "2026-07-04T00:00:00.000Z",
    latest_triage_at: "2026-07-04T00:04:00.000Z",
    ...overrides,
    window
  };
}

function gateRecord(overrides: Partial<EvalGateRecord> = {}): EvalGateRecord {
  return {
    id: "evalgate_1",
    tenant_id: "demo_tenant",
    gate_name: "golden",
    runner: "agent",
    suite_id: "golden_core",
    suite_path: "examples/evals/golden_core.json",
    environment: "staging",
    actor_user_id: "user_demo",
    trigger: "console",
    status: "passed",
    total: 2,
    passed: 2,
    score: 1,
    failed_case_ids: [],
    case_results: [],
    error_message: null,
    run_id: "run_1",
    alert_key: "agent:order:TIMEOUT",
    started_at: "2026-07-04T00:00:00.000Z",
    completed_at: "2026-07-04T00:02:00.000Z",
    duration_ms: 120000,
    metadata: {},
    created_at: "2026-07-04T00:02:00.000Z",
    ...overrides
  };
}

function snapshotWithAlerts(
  alerts: MonitorAlert[],
  overrides: Partial<ConsoleSnapshot> = {}
): ConsoleSnapshot {
  return {
    health: { status: "ok" },
    ready: null,
    summary: {
      total_events: alerts.length,
      by_risk_level: {},
      by_intent: {},
      by_failure_type: {},
      grounded_rate: 1,
      policy_compliance_rate: 1,
      human_review_rate: 0,
      alerts
    },
    monitorSource: "event_store",
    activeAlertKey: alerts[0]?.key ?? null,
    activeRunId: alerts[0]?.sample_run_ids[0] ?? null,
    incident: null,
    incidentBrief: null,
    incidentTimeline: null,
    triageMetrics: null,
    monitorReviewWorker: null,
    auditExportBatch: null,
    promotionGate: null,
    promotionDecisions: [],
    operationsAutomation: null,
    operationsAutomationExecutionSummary: null,
    sloReport: null,
    monitorAlertDelivery: null,
    triageEvents: [],
    evalGateLatest: null,
    evalGateRecords: [],
    rawEvents: [],
    tools: [],
    issues: [],
    connection: {
      label: "local",
      authMode: "demo",
      actorUserId: "console",
      actorRole: "admin"
    },
    ...overrides
  };
}

describe("ops workbench helpers", () => {
  it("classifies snapshot freshness and blocks stale or failed mutations", () => {
    expect(
      buildSnapshotFreshness({
        fetchedAtMs: null,
        nowMs: 0,
        loading: true,
        error: null,
        liveEnabled: true,
        staleAfterMs: 90_000
      })
    ).toMatchObject({
      status: "loading",
      isStale: false,
      canMutate: false
    });

    expect(
      buildSnapshotFreshness({
        fetchedAtMs: 1_000,
        nowMs: 35_000,
        loading: false,
        error: null,
        liveEnabled: true,
        staleAfterMs: 90_000
      })
    ).toMatchObject({
      status: "fresh",
      label: "just now",
      isStale: false,
      canMutate: true
    });

    expect(
      buildSnapshotFreshness({
        fetchedAtMs: 1_000,
        nowMs: 35_000,
        loading: false,
        error: null,
        liveEnabled: true,
        staleAfterMs: 90_000,
        issues: [{ status: 503, detail: "SLO report unavailable" }]
      })
    ).toMatchObject({
      status: "degraded",
      tone: "warn",
      label: "1 issue",
      isStale: false,
      canMutate: true
    });

    expect(
      buildSnapshotFreshness({
        fetchedAtMs: 1_000,
        nowMs: 121_000,
        loading: false,
        error: null,
        liveEnabled: true,
        staleAfterMs: 90_000,
        issues: [{ status: 503, detail: "SLO report unavailable" }]
      })
    ).toMatchObject({
      status: "stale",
      label: "2m ago",
      isStale: true,
      canMutate: false
    });

    expect(
      buildSnapshotFreshness({
        fetchedAtMs: 1_000,
        nowMs: 20_000,
        loading: false,
        error: "Console snapshot failed",
        liveEnabled: true,
        staleAfterMs: 90_000
      })
    ).toMatchObject({
      status: "failed",
      canMutate: false
    });
  });

  it("diffs alert queues without marking the first snapshot as changed", () => {
    const previous = [
      alert({ key: "agent:order:TIMEOUT", count: 1, last_seen_at: "2026-07-04T00:05:00.000Z" })
    ];
    const next = [
      alert({ key: "agent:order:TIMEOUT", count: 2, last_seen_at: "2026-07-04T00:06:00.000Z" }),
      alert({ key: "agent:billing:PII", severity: "P0", reason: "PII_IN_OUTPUT clustered across 1 event(s)" })
    ];

    expect(diffAlertQueue(null, next)).toEqual({
      newAlertKeys: [],
      updatedAlertKeys: [],
      changedAlertKeys: []
    });
    expect(diffAlertQueue(previous, next)).toEqual({
      newAlertKeys: ["agent:billing:PII"],
      updatedAlertKeys: ["agent:order:TIMEOUT"],
      changedAlertKeys: ["agent:billing:PII", "agent:order:TIMEOUT"]
    });
  });

  it("preserves operator selection during silent snapshot refresh", () => {
    const firstAlert = alert({ key: "agent:order:TIMEOUT", sample_run_ids: ["run_1"] });
    const selectedAlert = alert({
      key: "agent:billing:PII",
      severity: "P0",
      sample_run_ids: ["run_2"]
    });
    const snapshot = snapshotWithAlerts([firstAlert, selectedAlert], {
      activeAlertKey: firstAlert.key,
      activeRunId: "run_1"
    });

    expect(
      reconcileSnapshotSelection(
        snapshot,
        { runId: "run_2", alertKey: selectedAlert.key, runQuery: "run_2" },
        { preserve: true }
      )
    ).toEqual({
      runId: "run_2",
      alertKey: selectedAlert.key,
      runQuery: "run_2"
    });

    expect(
      reconcileSnapshotSelection(
        snapshot,
        { runId: "run_2", alertKey: selectedAlert.key, runQuery: "run_2" },
        { preserve: false }
      )
    ).toEqual({
      runId: "run_1",
      alertKey: firstAlert.key,
      runQuery: "run_1"
    });
  });

  it("falls back to a real alert key when the preserved alert disappeared", () => {
    const fallbackAlert = alert({ key: "agent:order:TIMEOUT", sample_run_ids: ["run_1"] });
    const snapshot = snapshotWithAlerts([fallbackAlert], {
      activeAlertKey: "agent:missing:STALE",
      activeRunId: "run_1"
    });

    expect(
      reconcileSnapshotSelection(
        snapshot,
        { runId: "run_2", alertKey: "agent:missing:STALE", runQuery: "run_2" },
        { preserve: true }
      )
    ).toEqual({
      runId: "run_2",
      alertKey: fallbackAlert.key,
      runQuery: "run_2"
    });
  });

  it("builds compact alert fingerprints for guarded triage writes", () => {
    expect(
      alertSnapshotFingerprint(
        alert({
          status: "acknowledged",
          assignee_user_id: "ops",
          count: 3,
          last_seen_at: "2026-07-04T00:10:00.000Z",
          last_triage_event_id: "triage_1",
          new_events_since_triage: true
        })
      )
    ).toEqual({
      status: "acknowledged",
      assigneeUserId: "ops",
      count: 3,
      lastSeenAt: "2026-07-04T00:10:00.000Z",
      lastTriageEventId: "triage_1",
      newEventsSinceTriage: true
    });
    expect(alertSnapshotFingerprint(null)).toBeNull();
  });

  it("builds guarded feedback review fingerprints from queue state or trail state", () => {
    expect(feedbackReviewSnapshotFingerprint("fdbk_1", [], null)).toEqual({
      currentStatus: "unreviewed",
      reviewCount: 0,
      latestReviewId: null,
      latestReviewAt: null,
      assigneeUserId: null
    });

    expect(
      feedbackReviewSnapshotFingerprint(
        "fdbk_1",
        [feedbackReview({ id: "fdbrv_old", status: "acknowledged" })],
        feedbackQueueItem({
          current_status: "resolved",
          review_count: 2,
          latest_review_id: "fdbrv_latest",
          latest_review_at: "2026-07-04T00:10:00.000Z",
          assignee_user_id: "ops"
        })
      )
    ).toEqual({
      currentStatus: "resolved",
      reviewCount: 2,
      latestReviewId: "fdbrv_latest",
      latestReviewAt: "2026-07-04T00:10:00.000Z",
      assigneeUserId: "ops"
    });

    expect(
      feedbackReviewSnapshotFingerprint(
        "fdbk_1",
        [
          feedbackReview({ id: "fdbrv_1", status: "acknowledged" }),
          feedbackReview({
            id: "fdbrv_2",
            status: "investigating",
            assignee_user_id: "qa",
            created_at: "2026-07-04T00:12:00.000Z"
          })
        ],
        feedbackQueueItem({ review_count: 1, latest_review_id: "fdbrv_1" })
      )
    ).toEqual({
      currentStatus: "investigating",
      reviewCount: 2,
      latestReviewId: "fdbrv_2",
      latestReviewAt: "2026-07-04T00:12:00.000Z",
      assigneeUserId: "qa"
    });
  });

  it("filters active alerts by severity, owner text, and new-event flag", () => {
    const alerts = [
      alert({
        severity: "P0",
        key: "agent:billing:PII",
        reason: "PII_IN_OUTPUT clustered across 2 event(s)",
        assignee_user_id: "lin",
        new_events_since_triage: true,
        count: 2
      }),
      alert({
        severity: "P2",
        key: "agent:order:TIMEOUT",
        status: "resolved",
        assignee_user_id: "kai"
      })
    ];

    const result = filterAndSortAlerts(alerts, {
      severity: "P0",
      status: "active",
      query: "lin",
      onlyNew: true,
      sort: "severity"
    });

    expect(result).toHaveLength(1);
    expect(result[0].key).toBe("agent:billing:PII");
  });

  it("keeps resolved alerts with fresh monitor events in the active queue", () => {
    const alerts = [
      alert({
        key: "agent:order:TIMEOUT",
        status: "resolved",
        assignee_user_id: "kai",
        new_events_since_triage: true
      }),
      alert({
        key: "agent:billing:PII",
        status: "silenced",
        new_events_since_triage: true
      })
    ];

    const result = filterAndSortAlerts(alerts, {
      severity: "all",
      status: "active",
      query: "",
      onlyNew: false,
      sort: "severity"
    });

    expect(result.map((item) => item.key)).toEqual(["agent:order:TIMEOUT"]);
  });

  it("builds metrics and incident actions from real snapshot shape", () => {
    const snapshot = {
      health: { status: "ok" },
      ready: {
        status: "not_ready",
        environment: "staging",
        deep: false,
        checks: [{ name: "event_store", status: "failed", detail: "schema missing" }]
      },
      summary: {
        total_events: 2,
        by_risk_level: { critical: 1 },
        by_intent: { order_status: 1 },
        by_failure_type: { TIMEOUT: 1 },
        grounded_rate: 0.5,
        policy_compliance_rate: 0.5,
        human_review_rate: 1,
        alerts: [
          alert({
            severity: "P1",
            assignee_user_id: null,
            new_events_since_triage: true
          })
        ]
      },
      monitorSource: "event_store",
      activeAlertKey: "agent:order:TIMEOUT",
      activeRunId: "run_1",
      incident: {
        run: {
          id: "run_1",
          tenant_id: "demo",
          conversation_id: "conv_1",
          user_id: "user_demo",
          request_id: null,
          parent_trace_id: null,
          agent_version: "agent_test",
          intent: null,
          route: null,
          retrieval: null,
          tool_results: [
            {
              id: "tool_1",
              name: "shipping.track",
              status: "failed",
              data: null,
              error_code: "TIMEOUT",
              error_message: "slow",
              retryable: true,
              latency_ms: 3000
            }
          ],
          llm_calls: [],
          policy_findings: [],
          spans: [],
          created_at: "2026-07-04T00:00:00.000Z",
          completed_at: "2026-07-04T00:00:02.000Z",
          status: "completed"
        },
        run_source: "event_store",
        monitor_events: [],
        tool_audit_records: [],
        memory_replay: null
      },
      incidentBrief: null,
      incidentTimeline: null,
      triageEvents: [],
      triageMetrics: null,
      monitorReviewWorker: null,
      auditExportBatch: null,
      promotionGate: null,
      promotionDecisions: [],
      operationsAutomation: null,
      operationsAutomationExecutionSummary: null,
      sloReport: null,
      monitorAlertDelivery: null,
      evalGateLatest: null,
      evalGateRecords: [],
      rawEvents: [],
      tools: [],
      issues: [],
      connection: {
        label: "Local API",
        authMode: "demo",
        actorUserId: "user_demo",
        actorRole: "admin"
      }
    } as ConsoleSnapshot;

    const metrics = buildOpsMetrics(snapshot);
    const brief = buildIncidentBrief(snapshot, snapshot.summary.alerts[0], null);

    expect(metrics.readinessFailed).toBe(1);
    expect(metrics.newSinceTriage).toBe(1);
    expect(brief.markdown).toContain("run_1");
    expect(brief.recommendedActions.join(" ")).toContain("Assign an owner");
    expect(brief.recommendedActions.join(" ")).toContain("TIMEOUT");
  });

  it("uses latest persisted eval gate records for brief promotion guidance", () => {
    const older = gateRecord({
      id: "evalgate_old",
      status: "passed",
      completed_at: "2026-07-04T00:01:00.000Z",
      created_at: "2026-07-04T00:01:00.000Z"
    });
    const failed = gateRecord({
      id: "evalgate_failed",
      status: "failed",
      passed: 1,
      total: 2,
      score: 0.5,
      failed_case_ids: ["case_shipping"],
      completed_at: "2026-07-04T00:05:00.000Z",
      created_at: "2026-07-04T00:05:00.000Z"
    });
    const latest = latestEvalGateRecord([older, failed]);
    const snapshot = {
      health: null,
      ready: null,
      summary: {
        total_events: 0,
        by_risk_level: {},
        by_intent: {},
        by_failure_type: {},
        grounded_rate: 1,
        policy_compliance_rate: 1,
        human_review_rate: 0,
        alerts: []
      },
      monitorSource: "event_store",
      activeAlertKey: null,
      activeRunId: null,
      incident: null,
      incidentBrief: null,
      incidentTimeline: null,
      triageEvents: [],
      triageMetrics: null,
      monitorReviewWorker: null,
      auditExportBatch: null,
      promotionGate: null,
      promotionDecisions: [],
      operationsAutomation: null,
      operationsAutomationExecutionSummary: null,
      sloReport: null,
      monitorAlertDelivery: null,
      evalGateLatest: latest,
      evalGateRecords: [older, failed],
      rawEvents: [],
      tools: [],
      issues: [],
      connection: {
        label: "Local API",
        authMode: "demo",
        actorUserId: "user_demo",
        actorRole: "admin"
      }
    } as ConsoleSnapshot;

    const brief = buildIncidentBrief(snapshot, null, null);

    expect(latest?.id).toBe("evalgate_failed");
    expect(formatEvalStatus(null, latest)).toBe("1/2 passed (50%)");
    expect(brief.recommendedActions.join(" ")).toContain("Do not promote");
  });

  it("maps backend-generated incident briefs into the existing UI card shape", () => {
    const brief = incidentBriefFromResponse({
      schema_version: "incident_brief.v1",
      generated_at: "2026-07-05T00:00:00.000Z",
      title: "TIMEOUT clustered across 1 event(s)",
      risk_label: "P1",
      summary: "Run run_1 handled order_status via order_agent.",
      run_id: "run_1",
      conversation_id: "conv_1",
      run_source: "event_store",
      alert_key: "agent:order:TIMEOUT",
      recommended_actions: ["Inspect tool audit."],
      evidence: {},
      redactions: ["message_content"],
      markdown: "# PSA Lab Incident Brief"
    });

    expect(brief.title).toContain("TIMEOUT");
    expect(brief.riskLabel).toBe("P1");
    expect(brief.recommendedActions).toEqual(["Inspect tool audit."]);
    expect(brief.markdown).toContain("Incident Brief");
  });

  it("adds promotion gate status to incident recommendations", () => {
    const snapshot = {
      health: null,
      ready: null,
      summary: {
        total_events: 0,
        by_risk_level: {},
        by_intent: {},
        by_failure_type: {},
        grounded_rate: 1,
        policy_compliance_rate: 1,
        human_review_rate: 0,
        alerts: []
      },
      monitorSource: "event_store",
      activeAlertKey: null,
      activeRunId: null,
      incident: null,
      incidentBrief: null,
      incidentTimeline: null,
      triageEvents: [],
      triageMetrics: null,
      monitorReviewWorker: null,
      auditExportBatch: null,
      promotionGate: {
        status: "blocked",
        generated_at: "2026-07-04T00:06:00.000Z",
        environment: "staging",
        source: "event_store",
        window_hours: 24,
        thresholds: {
          max_active_p0p1_alerts: 0,
          max_active_alerts: 10,
          max_tool_failure_rate: 0.05,
          max_feedback_negative_rate: 0.4,
          max_eval_age_hours: 24,
          min_tool_calls: 1,
          min_feedback_count: 5
        },
        checks: [
          {
            name: "staging_eval_gate",
            status: "blocked",
            detail: "Latest aggregate staging eval gate is failed.",
            evidence: {}
          }
        ],
        readiness: {
          status: "ok",
          environment: "staging",
          deep: true,
          checks: []
        },
        monitor: triageMetrics(),
        tool_audit: {
          total_calls: 1,
          failed_calls: 0,
          replayed_calls: 0,
          failure_rate: 0,
          average_latency_ms: 42,
          max_latency_ms: 42,
          window_start: "2026-07-04T00:00:00.000Z",
          window_end: "2026-07-04T00:01:00.000Z",
          top_error_codes: [],
          tools: []
        },
        feedback: {
          total_count: 5,
          positive_count: 5,
          negative_count: 0,
          negative_rate: 0,
          counts_by_reason: [],
          window_start: "2026-07-04T00:00:00.000Z",
          window_end: "2026-07-04T00:01:00.000Z"
        },
        latest_eval_gate: null
      },
      promotionDecisions: [],
      operationsAutomation: null,
      operationsAutomationExecutionSummary: null,
      sloReport: null,
      monitorAlertDelivery: null,
      evalGateLatest: null,
      evalGateRecords: [],
      rawEvents: [],
      tools: [],
      issues: [],
      connection: {
        label: "Local API",
        authMode: "demo",
        actorUserId: "user_demo",
        actorRole: "admin"
      }
    } as ConsoleSnapshot;

    const brief = buildIncidentBrief(snapshot, null, null);

    expect(brief.recommendedActions.join(" ")).toContain("promotion gate is blocked");
  });

  it("summarizes run search results without needing full traces", () => {
    const stats = buildRunSearchStats({
      total: 3,
      limit: 20,
      offset: 0,
      has_more: false,
      items: [
        {
          id: "run_1",
          conversation_id: "conv_1",
          user_id: "user_demo",
          request_id: "gateway_req_1",
          parent_trace_id: "gateway_trace_1",
          agent_version: "agent_test",
          intent: "order_status",
          route: "order_agent",
          status: "completed",
          created_at: "2026-07-04T00:00:00.000Z",
          completed_at: "2026-07-04T00:00:01.000Z",
          duration_ms: 1000,
          tool_count: 2,
          failed_tool_count: 0,
          tool_error_codes: [],
          policy_codes: [],
          citation_count: 2,
          llm_call_count: 1,
          needs_human: false
        },
        {
          id: "run_2",
          conversation_id: "conv_2",
          user_id: "user_guest",
          request_id: null,
          parent_trace_id: null,
          agent_version: "agent_test",
          intent: "order_status",
          route: "order_agent",
          status: "failed",
          created_at: "2026-07-04T00:01:00.000Z",
          completed_at: null,
          duration_ms: null,
          tool_count: 1,
          failed_tool_count: 1,
          tool_error_codes: ["FORBIDDEN"],
          policy_codes: [],
          citation_count: 0,
          llm_call_count: 0,
          needs_human: true
        }
      ]
    });

    expect(stats.total).toBe(3);
    expect(stats.failedRuns).toBe(1);
    expect(stats.toolFailureRuns).toBe(1);
    expect(stats.humanReviewRuns).toBe(1);
    expect(stats.averageDurationMs).toBe(1000);
  });

  it("summarizes persisted tool audit SLA metrics", () => {
    const stats = buildToolAuditStats({
      total_calls: 5,
      failed_calls: 3,
      replayed_calls: 1,
      failure_rate: 0.6,
      average_latency_ms: 264,
      max_latency_ms: 500,
      window_start: "2026-07-04T00:00:00.000Z",
      window_end: "2026-07-04T00:05:00.000Z",
      top_error_codes: [
        { error_code: "TIMEOUT", count: 2 },
        { error_code: "BAD_REQUEST", count: 1 }
      ],
      tools: [
        {
          tool_name: "order.get",
          total_calls: 2,
          failed_calls: 1,
          replayed_calls: 1,
          failure_rate: 0.5,
          average_latency_ms: 150,
          max_latency_ms: 200,
          top_error_code: "BAD_REQUEST",
          last_seen_at: "2026-07-04T00:04:00.000Z"
        },
        {
          tool_name: "shipping.track",
          total_calls: 3,
          failed_calls: 2,
          replayed_calls: 0,
          failure_rate: 0.6667,
          average_latency_ms: 340,
          max_latency_ms: 500,
          top_error_code: "TIMEOUT",
          last_seen_at: "2026-07-04T00:03:00.000Z"
        }
      ]
    });

    expect(stats.totalCalls).toBe(5);
    expect(stats.failedCalls).toBe(3);
    expect(stats.replayedCalls).toBe(1);
    expect(stats.failureRate).toBe(0.6);
    expect(stats.averageLatencyMs).toBe(264);
    expect(stats.worstToolName).toBe("shipping.track");
    expect(stats.topErrorCode).toBe("TIMEOUT");
  });

  it("returns stable empty tool audit stats", () => {
    const stats = buildToolAuditStats(null);

    expect(stats.totalCalls).toBe(0);
    expect(stats.failedCalls).toBe(0);
    expect(stats.averageLatencyMs).toBeNull();
    expect(stats.worstToolName).toBe("none");
    expect(stats.topErrorCode).toBe("none");
  });

  it("summarizes knowledge retrieval diagnostics", () => {
    const trace: KnowledgeSearchResponse = {
      query: "broken headphones return",
      rewritten_queries: ["broken headphones return", "damaged item refund"],
      selected_sources: ["kb://returns", "kb://returns", "kb://shipping"],
      candidates_by_stage: { bm25: 14, vector: 9, reranked: 4, selected: 2 },
      dropped_candidates: ["returns:old", "shipping:slow"],
      selected_context: [
        {
          document_id: "returns",
          chunk_id: "returns:2",
          title: "Returns",
          score: 0.92,
          source_uri: "kb://returns",
          content_snippet: "Damaged goods can be returned within 30 days."
        },
        {
          document_id: "shipping",
          chunk_id: "shipping:1",
          title: "Shipping",
          score: 0.74,
          source_uri: "kb://shipping",
          content_snippet: "Keep packaging when filing a damage claim."
        }
      ]
    };

    const stats = buildKnowledgeSearchStats(trace);

    expect(stats.selectedChunks).toBe(2);
    expect(stats.sourceCount).toBe(2);
    expect(stats.candidateCount).toBe(14);
    expect(stats.droppedCandidates).toBe(2);
    expect(stats.topScore).toBe(0.92);
    expect(stats.topSource).toBe("kb://returns");
  });

  it("returns stable empty knowledge retrieval stats", () => {
    const stats = buildKnowledgeSearchStats(null);

    expect(stats.selectedChunks).toBe(0);
    expect(stats.sourceCount).toBe(0);
    expect(stats.candidateCount).toBe(0);
    expect(stats.droppedCandidates).toBe(0);
    expect(stats.topScore).toBeNull();
    expect(stats.topSource).toBe("none");
  });

  it("summarizes monitor drilldown rates and top buckets", () => {
    const response: MonitorDrilldownResponse = {
      source: "event_store",
      summary: {
        total_events: 4,
        by_risk_level: { high: 2 },
        by_intent: { order_status: 3 },
        by_failure_type: { TIMEOUT: 2 },
        grounded_rate: 0.5,
        policy_compliance_rate: 0.75,
        human_review_rate: 0.5,
        alerts: []
      },
      active_alert: null,
      stats: {
        total_events: 4,
        matching_events: 2,
        alerted_events: 2,
        high_risk_events: 1,
        ungrounded_events: 1,
        policy_violations: 1,
        human_review_events: 1,
        pii_leak_events: 0,
        first_seen_at: "2026-07-04T00:00:00.000Z",
        last_seen_at: "2026-07-04T00:05:00.000Z"
      },
      events: [],
      failure_buckets: [{ key: "TIMEOUT", count: 2, rate: 1, latest_at: "2026-07-04T00:05:00.000Z", sample_run_ids: ["run_1"] }],
      intent_buckets: [{ key: "order_status", count: 2, rate: 1, latest_at: "2026-07-04T00:05:00.000Z", sample_run_ids: ["run_1"] }],
      risk_buckets: [{ key: "high", count: 1, rate: 0.5, latest_at: "2026-07-04T00:05:00.000Z", sample_run_ids: ["run_1"] }]
    };

    const stats = buildMonitorDrilldownStats(response);

    expect(stats.totalEvents).toBe(4);
    expect(stats.matchingEvents).toBe(2);
    expect(stats.alertRate).toBe(1);
    expect(stats.policyViolationRate).toBe(0.5);
    expect(stats.humanReviewRate).toBe(0.5);
    expect(stats.topFailure).toBe("TIMEOUT");
    expect(stats.topIntent).toBe("order_status");
    expect(stats.topRisk).toBe("high");
  });

  it("returns stable empty monitor drilldown stats", () => {
    const stats = buildMonitorDrilldownStats(null);

    expect(stats.totalEvents).toBe(0);
    expect(stats.matchingEvents).toBe(0);
    expect(stats.alertRate).toBe(0);
    expect(stats.topFailure).toBe("none");
  });

  it("normalizes monitor triage health metrics for the workbench strip", () => {
    const stats = buildMonitorTriageHealthStats(
      triageMetrics({
        active_alert_count: 3,
        unassigned_active_alert_count: 2,
        new_events_since_triage_count: 1,
        stale_active_alert_count: 1,
        by_severity: { P0: 1, P1: 2, P2: 0, P3: 0 },
        active_by_severity: { P0: 1, P1: 2, P2: 0, P3: 0 },
        health_status: "critical",
        mtta_seconds: 90,
        mttr_seconds: null,
        oldest_active_alert_at: "2026-07-04T00:00:00.000Z"
      })
    );

    expect(stats.healthStatus).toBe("critical");
    expect(stats.activeAlerts).toBe(3);
    expect(stats.unassignedActiveAlerts).toBe(2);
    expect(stats.newEventsSinceTriage).toBe(1);
    expect(stats.staleActiveAlerts).toBe(1);
    expect(stats.p0p1Alerts).toBe(3);
    expect(stats.mttaSeconds).toBe(90);
    expect(stats.mttrSeconds).toBeNull();
    expect(stats.oldestActiveAlertAt).toBe("2026-07-04T00:00:00.000Z");
  });

  it("returns stable empty monitor triage health metrics", () => {
    const stats = buildMonitorTriageHealthStats(null);

    expect(stats.healthStatus).toBe("unknown");
    expect(stats.activeAlerts).toBe(0);
    expect(stats.unassignedActiveAlerts).toBe(0);
    expect(stats.newEventsSinceTriage).toBe(0);
    expect(stats.p0p1Alerts).toBe(0);
    expect(stats.mttaSeconds).toBeNull();
  });

  it("keeps monitor triage health stable when severity buckets are omitted", () => {
    const stats = buildMonitorTriageHealthStats(
      triageMetrics({
        active_alert_count: 2,
        active_by_severity: undefined
      })
    );

    expect(stats.activeAlerts).toBe(2);
    expect(stats.p0p1Alerts).toBe(0);
  });

  it("normalizes alert delivery summary for monitor workbench display", () => {
    expect(buildMonitorAlertDeliveryStats(null)).toMatchObject({
      status: "unknown",
      tone: "neutral",
      badgeLabel: "Unavailable",
      receiptCoverageValue: "n/a",
      receiptDetail: "Receipt evidence unavailable."
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "disabled",
        webhook_enabled: false,
        pending_count: 0,
        in_progress_count: 0,
        failed_count: 0,
        dead_count: 0,
        closed_count: 0,
        oldest_pending_at: null,
        next_attempt_at: null,
        last_attempt_at: null,
        last_success_at: null,
        last_error: null
      }))
    ).toMatchObject({
      status: "disabled",
      value: "disabled",
      badgeLabel: "Webhook off",
      receiptCoverageValue: "off",
      receiptDetail: "Receipt tracking off."
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "queued",
        webhook_enabled: true,
        pending_count: 2,
        in_progress_count: 1,
        failed_count: 0,
        dead_count: 0,
        closed_count: 0,
        oldest_pending_at: "2026-07-04T00:00:00.000Z",
        next_attempt_at: null,
        last_attempt_at: null,
        last_success_at: null,
        last_error: null
      }))
    ).toMatchObject({
      status: "queued",
      tone: "warn",
      value: "3 queued",
      detail: "1 claimed by dispatcher.",
      dispatcherLabel: "active"
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "failed",
        webhook_enabled: true,
        pending_count: 1,
        in_progress_count: 0,
        failed_count: 3,
        dead_count: 0,
        closed_count: 1,
        oldest_pending_at: "2026-07-04T00:00:00.000Z",
        next_attempt_at: "2026-07-04T00:03:00.000Z",
        last_attempt_at: "2026-07-04T00:01:00.000Z",
        last_success_at: null,
        last_error: "HTTP_503"
      }))
    ).toMatchObject({
      status: "failed",
      tone: "danger",
      value: "3 failed",
      detail: "HTTP_503"
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "failed",
        webhook_enabled: true,
        pending_count: 0,
        in_progress_count: 0,
        failed_count: 0,
        dead_count: 2,
        closed_count: 0,
        oldest_pending_at: null,
        next_attempt_at: null,
        last_attempt_at: "2026-07-04T00:01:00.000Z",
        last_success_at: null,
        last_error: "HTTP_503"
      }))
    ).toMatchObject({
      tone: "danger",
      badgeLabel: "Dead-letter",
      value: "2 dead",
      deadCount: 2
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "degraded",
        webhook_enabled: true,
        dispatcher_status: "stale",
        dispatcher_active_worker_count: 0,
        dispatcher_stale_worker_count: 1,
        dispatcher_last_seen_at: "2026-07-04T00:00:00.000Z"
      }))
    ).toMatchObject({
      status: "degraded",
      tone: "warn",
      dispatcherStatus: "stale",
      dispatcherLabel: "stale",
      dispatcherLastSeenAt: "2026-07-04T00:00:00.000Z"
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "degraded",
        webhook_enabled: true,
        receipt_tracking_enabled: true,
        receipt_received_count: 3,
        receipt_duplicate_count: 2,
        sent_with_receipt_count: 3,
        sent_without_receipt_count: 1,
        recent_sent_pending_receipt_count: 0,
        receipt_grace_seconds: 60,
        last_receipt_at: "2026-07-04T00:02:00.000Z",
        oldest_unconfirmed_sent_at: "2026-07-04T00:00:00.000Z"
      }))
    ).toMatchObject({
      receiptTrackingEnabled: true,
      receiptReceivedCount: 3,
      receiptDuplicateCount: 2,
      sentWithReceiptCount: 3,
      sentWithoutReceiptCount: 1,
      recentSentPendingReceiptCount: 0,
      receiptGraceSeconds: 60,
      receiptCoverageValue: "3/4",
      receiptDetail: "1 sent without receipt."
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "ok",
        webhook_enabled: true,
        receipt_tracking_enabled: true,
        sent_with_receipt_count: 0,
        sent_without_receipt_count: 0,
        recent_sent_pending_receipt_count: 1
      }))
    ).toMatchObject({
      receiptCoverageValue: "n/a",
      receiptDetail: "1 inside receipt grace."
    });
    expect(
      buildMonitorAlertDeliveryStats(deliverySummary({
        status: "ok",
        webhook_enabled: true,
        receipt_tracking_enabled: true,
        receipt_duplicate_count: 1,
        sent_with_receipt_count: 2,
        sent_without_receipt_count: 0,
        recent_sent_pending_receipt_count: 0
      }))
    ).toMatchObject({
      receiptCoverageValue: "2/2",
      receiptDetail: "1 duplicate receipt."
    });
  });

  it("marks only dead alert delivery rows as operator actionable", () => {
    const dead = deliveryRecord({ status: "dead" });
    const failed = deliveryRecord({ status: "failed" });
    const closed = deliveryRecord({ status: "closed" });

    expect(canReplayAlertDelivery(dead)).toBe(true);
    expect(canCloseAlertDelivery(dead)).toBe(true);
    expect(canReplayAlertDelivery(failed)).toBe(false);
    expect(canCloseAlertDelivery(closed)).toBe(false);
    expect(deliveryStatusTone(dead.status)).toBe("danger");
    expect(deliveryStatusTone(closed.status)).toBe("neutral");
  });

  it("summarizes alert webhook receipts without raw payload data", () => {
    expect(buildAlertWebhookReceiptStats([])).toEqual({
      receiptCount: 0,
      duplicateCount: 0,
      sampleEventCount: 0,
      sampleRunCount: 0,
      latestReceivedAt: null,
      newestDeliveryId: "none"
    });

    const first = receiptRecord({
      delivery_id: "deliv_older",
      duplicate_count: 0,
      sample_event_count: 2,
      sample_run_count: 1,
      last_received_at: "2026-07-04T00:01:00.000Z"
    });
    const latest = receiptRecord({
      delivery_id: "deliv_latest",
      duplicate_count: 2,
      sample_event_count: 1,
      sample_run_count: 1,
      last_received_at: "2026-07-04T00:05:00.000Z"
    });

    expect(alertWebhookReceiptTone(first)).toBe("success");
    expect(alertWebhookReceiptTone(latest)).toBe("warn");
    expect(buildAlertWebhookReceiptStats([first, latest])).toEqual({
      receiptCount: 2,
      duplicateCount: 2,
      sampleEventCount: 3,
      sampleRunCount: 2,
      latestReceivedAt: "2026-07-04T00:05:00.000Z",
      newestDeliveryId: "deliv_latest"
    });
  });

  it("summarizes alert delivery dispatch reports", () => {
    expect(buildAlertDispatchResultStats(null)).toBeNull();
    expect(
      buildAlertDispatchResultStats(
        dispatchReport({
          webhook_enabled: false,
          skipped_count: 2
        })
      )
    ).toMatchObject({
      tone: "neutral",
      title: "Webhook disabled",
      skippedCount: 2
    });
    expect(
      buildAlertDispatchResultStats(
        dispatchReport({
          enqueued_count: 2,
          existing_count: 1,
          claimed_count: 3,
          attempted_count: 3,
          sent_count: 2,
          failed_count: 1,
          dead_count: 0
        })
      )
    ).toMatchObject({
      tone: "danger",
      title: "2/3 sent",
      attemptedCount: 3,
      failedCount: 1
    });
    expect(
      buildAlertDispatchResultStats(
        dispatchReport({
          enqueued_count: 1,
          claimed_count: 1,
          attempted_count: 1,
          sent_count: 1
        })
      )
    ).toMatchObject({
      tone: "success",
      title: "1/1 sent",
      sentCount: 1
    });
  });

  it("summarizes promotion gate checks for release preflight", () => {
    expect(buildPromotionGateStats(null)).toMatchObject({
      status: "unknown",
      tone: "neutral",
      blockedCount: 0
    });

    const stats = buildPromotionGateStats({
      status: "blocked",
      generated_at: "2026-07-04T00:06:00.000Z",
      environment: "staging",
      source: "event_store",
      window_hours: 24,
      thresholds: {
        max_active_p0p1_alerts: 0,
        max_active_alerts: 10,
        max_tool_failure_rate: 0.05,
        max_feedback_negative_rate: 0.4,
        max_eval_age_hours: 24,
        min_tool_calls: 1,
        min_feedback_count: 5
      },
      checks: [
        { name: "readiness", status: "passed", detail: "Ready.", evidence: {} },
        { name: "monitor", status: "warn", detail: "Active alerts.", evidence: { active: 1 } },
        { name: "eval", status: "blocked", detail: "Latest eval failed.", evidence: { score: 0.5 } }
      ],
      readiness: {
        status: "ok",
        environment: "staging",
        deep: true,
        checks: []
      },
      monitor: triageMetrics(),
      tool_audit: {
        total_calls: 1,
        failed_calls: 0,
        replayed_calls: 0,
        failure_rate: 0,
        average_latency_ms: 42,
        max_latency_ms: 42,
        window_start: "2026-07-04T00:00:00.000Z",
        window_end: "2026-07-04T00:01:00.000Z",
        top_error_codes: [],
        tools: []
      },
      feedback: {
        total_count: 5,
        positive_count: 5,
        negative_count: 0,
        negative_rate: 0,
        counts_by_reason: [],
        window_start: "2026-07-04T00:00:00.000Z",
        window_end: "2026-07-04T00:01:00.000Z"
      },
      latest_eval_gate: null
    });

    expect(stats).toMatchObject({
      status: "blocked",
      tone: "danger",
      blockedCount: 1,
      warnCount: 1,
      passedCount: 1
    });
    expect(stats.detail).toContain("1 blocked");
  });
});

function dispatchReport(overrides: Partial<AlertDispatchReport> = {}): AlertDispatchReport {
  return {
    webhook_enabled: true,
    enqueued_count: 0,
    existing_count: 0,
    skipped_count: 0,
    claimed_count: 0,
    attempted_count: 0,
    sent_count: 0,
    failed_count: 0,
    dead_count: 0,
    deliveries: [],
    ...overrides
  };
}

function deliveryRecord(overrides: Partial<AlertDeliveryRecord>): AlertDeliveryRecord {
  return {
    id: "deliv_1",
    tenant_id: "demo_tenant",
    alert_key: "agent:order:TIMEOUT",
    severity: "P1",
    channel: "webhook",
    destination_hash: "hash",
    status: "dead",
    alert_first_seen_at: "2026-07-04T00:00:00.000Z",
    alert_last_seen_at: "2026-07-04T00:01:00.000Z",
    alert_count: 1,
    reason: "TIMEOUT clustered across 1 event(s)",
    sample_event_ids: ["mon_1"],
    sample_run_ids: ["run_1"],
    payload_hash: "payload",
    attempt_count: 3,
    next_attempt_at: null,
    last_attempt_at: "2026-07-04T00:02:00.000Z",
    delivered_at: null,
    dead_lettered_at: "2026-07-04T00:03:00.000Z",
    locked_until: null,
    locked_by: null,
    operator_action: null,
    operator_action_at: null,
    operator_action_by: null,
    operator_action_note: null,
    response_status_code: 503,
    last_error: "HTTP_503",
    created_at: "2026-07-04T00:00:00.000Z",
    updated_at: "2026-07-04T00:03:00.000Z",
    ...overrides
  };
}

function receiptRecord(overrides: Partial<AlertWebhookReceiptRecord> = {}): AlertWebhookReceiptRecord {
  return {
    delivery_id: "deliv_1",
    alert_key: "agent:order:TIMEOUT",
    severity: "P1",
    body_hash: "body_hash_1",
    alert_count: 1,
    sample_event_count: 1,
    sample_run_count: 1,
    duplicate_count: 0,
    first_received_at: "2026-07-04T00:00:00.000Z",
    last_received_at: "2026-07-04T00:00:00.000Z",
    created_at: "2026-07-04T00:00:00.000Z",
    updated_at: "2026-07-04T00:00:00.000Z",
    ...overrides
  };
}

function deliverySummary(overrides: Partial<MonitorAlertDeliverySummary>): MonitorAlertDeliverySummary {
  return {
    status: "ok",
    webhook_enabled: true,
    pending_count: 0,
    in_progress_count: 0,
    failed_count: 0,
    dead_count: 0,
    closed_count: 0,
    oldest_pending_at: null,
    next_attempt_at: null,
    last_attempt_at: null,
    last_success_at: null,
    last_error: null,
    dispatcher_status: "active",
    dispatcher_stale_after_seconds: 180,
    dispatcher_active_worker_count: 1,
    dispatcher_stale_worker_count: 0,
    dispatcher_last_seen_at: "2026-07-04T00:02:00.000Z",
    dispatcher_last_success_at: "2026-07-04T00:02:00.000Z",
    dispatcher_last_error: null,
    receipt_tracking_enabled: false,
    receipt_received_count: 0,
    receipt_duplicate_count: 0,
    sent_with_receipt_count: 0,
    sent_without_receipt_count: 0,
    recent_sent_pending_receipt_count: 0,
    receipt_grace_seconds: null,
    last_receipt_at: null,
    oldest_unconfirmed_sent_at: null,
    ...overrides
  };
}

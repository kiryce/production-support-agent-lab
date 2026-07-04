import { describe, expect, it } from "vitest";
import {
  buildIncidentBrief,
  buildKnowledgeSearchStats,
  buildOpsMetrics,
  buildRunSearchStats,
  buildToolAuditStats,
  filterAndSortAlerts
} from "../src/shared/ops";
import type { ConsoleSnapshot, KnowledgeSearchResponse, MonitorAlert } from "../src/shared/types";

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

describe("ops workbench helpers", () => {
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
      triageEvents: [],
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
});

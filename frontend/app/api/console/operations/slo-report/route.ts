import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { SloReportResponse } from "@/src/shared/types";

export const dynamic = "force-dynamic";

const SOURCES = new Set(["event_store", "live"]);

export async function GET(request: NextRequest) {
  const sourceParam = request.nextUrl.searchParams.get("source");
  const source = sourceParam && SOURCES.has(sourceParam) ? sourceParam : "event_store";
  const deep = request.nextUrl.searchParams.get("deep") === "true";
  const windowHours = clampNumber(request.nextUrl.searchParams.get("window_hours"), 1, 168, 24);
  const minGroundedRate = clampFloat(request.nextUrl.searchParams.get("min_grounded_rate"), 0, 1, 0.95);
  const minPolicyComplianceRate = clampFloat(
    request.nextUrl.searchParams.get("min_policy_compliance_rate"),
    0,
    1,
    0.99
  );
  const maxHumanReviewRate = clampFloat(request.nextUrl.searchParams.get("max_human_review_rate"), 0, 1, 0.4);
  const maxActiveP0P1Alerts = clampNumber(request.nextUrl.searchParams.get("max_active_p0p1_alerts"), 0, 100, 0);
  const maxToolFailureRate = clampFloat(request.nextUrl.searchParams.get("max_tool_failure_rate"), 0, 1, 0.05);
  const maxFeedbackNegativeRate = clampFloat(
    request.nextUrl.searchParams.get("max_feedback_negative_rate"),
    0,
    1,
    0.4
  );
  const maxEvalAgeHours = clampNumber(request.nextUrl.searchParams.get("max_eval_age_hours"), 1, 720, 24);
  const maxMttaSeconds = clampNumber(request.nextUrl.searchParams.get("max_mtta_seconds"), 1, 86400, 900);
  const maxAlertDeliveryDeadCount = clampNumber(
    request.nextUrl.searchParams.get("max_alert_delivery_dead_count"),
    0,
    1000,
    0
  );
  const maxAutomationFailureRate = clampFloat(
    request.nextUrl.searchParams.get("max_automation_failure_rate"),
    0,
    1,
    0.1
  );
  const minToolCalls = clampNumber(request.nextUrl.searchParams.get("min_tool_calls"), 0, 10000, 1);
  const minFeedbackCount = clampNumber(request.nextUrl.searchParams.get("min_feedback_count"), 0, 10000, 5);
  const minAutomationExecutions = clampNumber(
    request.nextUrl.searchParams.get("min_automation_executions"),
    0,
    10000,
    1
  );
  try {
    const report = await agentFetch<SloReportResponse>("/api/v1/admin/operations/slo-report", {
      query: {
        source,
        deep,
        window_hours: windowHours,
        min_grounded_rate: minGroundedRate,
        min_policy_compliance_rate: minPolicyComplianceRate,
        max_human_review_rate: maxHumanReviewRate,
        max_active_p0p1_alerts: maxActiveP0P1Alerts,
        max_tool_failure_rate: maxToolFailureRate,
        max_feedback_negative_rate: maxFeedbackNegativeRate,
        max_eval_age_hours: maxEvalAgeHours,
        max_mtta_seconds: maxMttaSeconds,
        max_alert_delivery_dead_count: maxAlertDeliveryDeadCount,
        max_automation_failure_rate: maxAutomationFailureRate,
        min_tool_calls: minToolCalls,
        min_feedback_count: minFeedbackCount,
        min_automation_executions: minAutomationExecutions
      }
    });
    return NextResponse.json(report);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function clampNumber(value: string | null, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function clampFloat(value: string | null, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, parsed));
}

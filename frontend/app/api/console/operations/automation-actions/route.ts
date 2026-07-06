import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type {
  JsonRecord,
  JsonValue,
  OperationsAutomationAction,
  OperationsAutomationCommand,
  OperationsAutomationExecutionRecord,
  OperationsAutomationExecutionResult,
  OperationsAutomationPlan
} from "@/src/shared/types";

export const dynamic = "force-dynamic";

const SOURCES = new Set(["event_store", "live"]);

type QueryValue = string | number | boolean | null | undefined;

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json().catch(() => ({}));
    const actionId = typeof payload.actionId === "string" ? payload.actionId : "";
    if (!actionId) {
      return NextResponse.json({ detail: "actionId is required" }, { status: 400 });
    }

    const plan = await loadAutomationPlan(payload);
    const action = plan.actions.find((item) => item.id === actionId);
    if (!action) {
      return NextResponse.json({ detail: "Automation action was not found in the current plan" }, { status: 404 });
    }
    if (!action.command) {
      return NextResponse.json({ detail: "Automation action has no executable command" }, { status: 409 });
    }
    if (!action.safe_to_auto_execute) {
      return NextResponse.json(
        { detail: "Manual automation actions require an operator workflow and cannot be executed here" },
        { status: 409 }
      );
    }
    if (!isAllowedAutomationCommand(action, action.command)) {
      return NextResponse.json(
        { detail: "Automation command is outside the console execution allowlist" },
        { status: 409 }
      );
    }

    let result: JsonValue;
    try {
      result = await executeAutomationCommand(action.command);
    } catch (error) {
      const issue = issueFrom(error);
      await recordAutomationExecution({
        action,
        command: action.command,
        status: "failed",
        resultSummary: "Automation action failed.",
        errorDetail: issue.detail
      });
      return NextResponse.json({ detail: issue.detail }, { status: issue.status });
    }
    const resultSummary = summarizeResult(action, result);
    const audit = await recordAutomationExecution({
      action,
      command: action.command,
      status: "completed",
      resultSummary,
      errorDetail: null
    });
    const response: OperationsAutomationExecutionResult = {
      schema_version: "ops_action_execution.v1",
      action_id: action.id,
      action_kind: action.kind,
      title: action.title,
      safe_to_auto_execute: action.safe_to_auto_execute,
      command: action.command,
      result,
      result_summary: resultSummary,
      audit_recorded: Boolean(audit.record),
      audit_record: audit.record,
      audit_error: audit.error
    };
    return NextResponse.json(response);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

async function loadAutomationPlan(payload: JsonRecord): Promise<OperationsAutomationPlan> {
  const sourceParam = typeof payload.source === "string" ? payload.source : null;
  const source = sourceParam && SOURCES.has(sourceParam) ? sourceParam : "event_store";
  return agentFetch<OperationsAutomationPlan>("/api/v1/admin/operations/automation-plan", {
    query: {
      source,
      deep: payload.deep === true,
      window_hours: clampNumber(payload.window_hours, 1, 168, 24),
      limit: clampNumber(payload.limit, 1, 1000, 500),
      stale_after_minutes: clampNumber(payload.stale_after_minutes, 1, 1440, 60),
      max_active_p0p1_alerts: clampNumber(payload.max_active_p0p1_alerts, 0, 100, 0),
      max_active_alerts: clampNumber(payload.max_active_alerts, 0, 1000, 10),
      max_tool_failure_rate: clampFloat(payload.max_tool_failure_rate, 0, 1, 0.05),
      max_feedback_negative_rate: clampFloat(payload.max_feedback_negative_rate, 0, 1, 0.4),
      max_eval_age_hours: clampNumber(payload.max_eval_age_hours, 1, 720, 24),
      min_tool_calls: clampNumber(payload.min_tool_calls, 0, 10000, 1),
      min_feedback_count: clampNumber(payload.min_feedback_count, 0, 10000, 5)
    }
  });
}

async function recordAutomationExecution(input: {
  action: OperationsAutomationAction;
  command: OperationsAutomationCommand;
  status: "completed" | "failed" | "rejected";
  resultSummary: string;
  errorDetail: string | null;
}): Promise<{ record: OperationsAutomationExecutionRecord | null; error: string | null }> {
  try {
    const record = await agentFetch<OperationsAutomationExecutionRecord>(
      "/api/v1/admin/operations/automation-executions",
      {
        method: "POST",
        body: {
          action_id: input.action.id,
          action_kind: input.action.kind,
          title: input.action.title,
          status: input.status,
          safe_to_auto_execute: input.action.safe_to_auto_execute,
          command: input.command,
          result_summary: input.resultSummary.slice(0, 500),
          error_detail: input.errorDetail ? input.errorDetail.slice(0, 500) : null,
          source: "console"
        }
      }
    );
    return { record, error: null };
  } catch (error) {
    const issue = issueFrom(error);
    return { record: null, error: issue.detail };
  }
}

async function executeAutomationCommand(command: OperationsAutomationCommand): Promise<JsonValue> {
  return agentFetch<JsonValue>(command.path, {
    method: command.method,
    query: queryFromJson(command.query),
    body: command.method === "POST" ? command.body : undefined
  });
}

function isAllowedAutomationCommand(action: OperationsAutomationAction, command: OperationsAutomationCommand) {
  if (action.kind === "dispatch_alert_deliveries") {
    return command.method === "POST" && command.path === "/api/v1/admin/monitor/alert-deliveries/dispatch";
  }
  if (action.kind === "generate_incident_brief") {
    return command.method === "GET" && /^\/api\/v1\/admin\/incidents\/runs\/[^/]+\/brief$/.test(command.path);
  }
  if (action.kind === "create_regression_draft") {
    return command.method === "POST" && command.path === "/api/v1/admin/evals/regression-drafts";
  }
  if (action.kind === "block_promotion" || action.kind === "review_promotion_gate") {
    return command.method === "GET" && command.path === "/api/v1/admin/promotion/gate";
  }
  if (action.kind === "inspect_tool_audit") {
    return command.method === "GET" && command.path === "/api/v1/admin/tools/audit";
  }
  if (action.kind === "inspect_missing_alert_receipts") {
    return command.method === "GET" && command.path === "/api/v1/admin/monitor/alert-deliveries/receipt-gaps";
  }
  if (action.kind === "review_feedback") {
    return command.method === "GET" && command.path === "/api/v1/admin/feedback";
  }
  if (action.kind === "run_retrieval_diagnostics") {
    return command.method === "POST" && command.path === "/api/v1/admin/knowledge/search";
  }
  return false;
}

function queryFromJson(record: JsonRecord): Record<string, QueryValue> {
  const query: Record<string, QueryValue> = {};
  for (const [key, value] of Object.entries(record)) {
    if (
      value === null ||
      value === undefined ||
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      query[key] = value;
      continue;
    }
    throw new Error(`Automation query field ${key} must be a scalar value`);
  }
  return query;
}

function summarizeResult(action: OperationsAutomationAction, result: JsonValue) {
  if (action.kind === "dispatch_alert_deliveries" && isRecord(result)) {
    return `${numberField(result, "sent_count")}/${numberField(result, "attempted_count")} delivery attempt(s) sent; ${numberField(result, "dead_count")} dead-lettered.`;
  }
  if (action.kind === "generate_incident_brief" && isRecord(result)) {
    return `Incident brief ready: ${stringField(result, "title") || action.title}`;
  }
  if (action.kind === "create_regression_draft" && isRecord(result)) {
    return `Regression draft ready for ${stringField(result, "target_file") || "the suggested eval suite"}.`;
  }
  if ((action.kind === "block_promotion" || action.kind === "review_promotion_gate") && isRecord(result)) {
    return `Promotion gate returned ${stringField(result, "status") || "unknown"}.`;
  }
  if (action.kind === "inspect_tool_audit" && Array.isArray(result)) {
    return `${result.length} tool audit record(s) loaded.`;
  }
  if (action.kind === "inspect_missing_alert_receipts" && Array.isArray(result)) {
    return `${result.length} sent delivery receipt gap(s) loaded.`;
  }
  if (action.kind === "review_feedback" && Array.isArray(result)) {
    return `${result.length} feedback record(s) loaded.`;
  }
  if (action.kind === "run_retrieval_diagnostics" && isRecord(result)) {
    const selected = Array.isArray(result.selected_context) ? result.selected_context.length : 0;
    return `${selected} retrieval chunk(s) selected for diagnostics.`;
  }
  return "Automation action executed.";
}

function clampNumber(value: JsonValue | undefined, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function clampFloat(value: JsonValue | undefined, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, parsed));
}

function isRecord(value: JsonValue): value is JsonRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function stringField(record: JsonRecord, key: string) {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function numberField(record: JsonRecord, key: string) {
  const value = record[key];
  return typeof value === "number" ? value : 0;
}

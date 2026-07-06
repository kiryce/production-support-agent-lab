import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { OperationsAutomationExecutionSummary } from "@/src/shared/types";

export const dynamic = "force-dynamic";

const SOURCES = new Set(["console", "cron", "on_call_bot", "api"]);

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const actionKind = safeString(searchParams.get("action_kind"), 80);
    const source = safeEnum(searchParams.get("source"), SOURCES);
    const createdAfter = safeString(searchParams.get("created_after"), 64);
    const createdBefore = safeString(searchParams.get("created_before"), 64);
    const windowHours = clampNumber(searchParams.get("window_hours"), 1, 168, 24);

    const summary = await agentFetch<OperationsAutomationExecutionSummary>(
      "/api/v1/admin/operations/automation-executions/summary",
      {
        query: {
          action_kind: actionKind,
          source,
          created_after: createdAfter,
          created_before: createdBefore,
          window_hours: windowHours
        }
      }
    );
    return NextResponse.json(summary);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function safeString(value: string | null, maxLength: number) {
  const trimmed = value?.trim() ?? "";
  return trimmed ? trimmed.slice(0, maxLength) : undefined;
}

function safeEnum(value: string | null, allowed: Set<string>) {
  const trimmed = value?.trim() ?? "";
  return allowed.has(trimmed) ? trimmed : undefined;
}

function clampNumber(value: string | null, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

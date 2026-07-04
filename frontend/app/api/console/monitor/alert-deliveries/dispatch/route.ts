import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { AlertDispatchReport } from "@/src/shared/types";

export const dynamic = "force-dynamic";

const SOURCES = new Set(["event_store", "live"]);

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json().catch(() => ({}));
    const source = typeof payload.source === "string" && SOURCES.has(payload.source) ? payload.source : "event_store";
    const monitorLimit = clampNumber(payload.monitorLimit, 1, 500, 500);
    const dispatchLimit = clampNumber(payload.dispatchLimit, 1, 100, 25);
    const response = await agentFetch<AlertDispatchReport>(
      "/api/v1/admin/monitor/alert-deliveries/dispatch",
      {
        method: "POST",
        query: {
          source,
          monitor_limit: monitorLimit,
          dispatch_limit: dispatchLimit
        }
      }
    );
    return NextResponse.json(response);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

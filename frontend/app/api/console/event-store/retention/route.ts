import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { EventStoreRetentionReport, JsonRecord } from "@/src/shared/types";

export const dynamic = "force-dynamic";

type RetentionDayField =
  | "event_retention_days"
  | "tool_audit_retention_days"
  | "idempotency_retention_days"
  | "alert_delivery_retention_days";

const LIMITS: Record<RetentionDayField, [number, number]> = {
  event_retention_days: [30, 3650],
  tool_audit_retention_days: [30, 3650],
  idempotency_retention_days: [1, 3650],
  alert_delivery_retention_days: [7, 3650]
};

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json().catch(() => ({}));
    const body: JsonRecord = {
      dry_run: payload.dry_run !== false,
      include_events: payload.include_events === true,
      vacuum: payload.vacuum === true
    };
    for (const [field, [min, max]] of Object.entries(LIMITS) as Array<
      [RetentionDayField, [number, number]]
    >) {
      const value = clampNumber(payload[field], min, max);
      if (value !== null) {
        body[field] = value;
      }
    }
    const response = await agentFetch<EventStoreRetentionReport>(
      "/api/v1/admin/event-store/retention",
      {
        method: "POST",
        body
      }
    );
    return NextResponse.json(response);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function clampNumber(value: unknown, min: number, max: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

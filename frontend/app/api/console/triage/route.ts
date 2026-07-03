import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { MonitorAlertTriageEvent } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json();
    const alertKey = typeof payload.alertKey === "string" ? payload.alertKey : "";
    if (!alertKey) {
      return NextResponse.json({ detail: "alertKey is required" }, { status: 400 });
    }

    const event = await agentFetch<MonitorAlertTriageEvent>(
      `/api/v1/admin/monitor/alerts/${encodeURIComponent(alertKey)}/triage`,
      {
        method: "POST",
        body: {
          status: payload.status ?? null,
          assignee_user_id: payload.assigneeUserId ?? null,
          note: typeof payload.note === "string" ? payload.note : ""
        }
      }
    );
    return NextResponse.json(event);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

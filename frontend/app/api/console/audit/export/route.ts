import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const query = {
    event_type: clean(searchParams.get("event_type"), 120),
    created_after: clean(searchParams.get("created_after"), 80),
    created_before: clean(searchParams.get("created_before"), 80),
    include_events: searchParams.get("include_events") !== "false",
    include_tool_audit: searchParams.get("include_tool_audit") !== "false",
    include_event_store_operations: searchParams.get("include_event_store_operations") !== "false",
    limit: clampNumber(searchParams.get("limit"), 1, 5000, 1000),
    order: searchParams.get("order") === "desc" ? "desc" : "asc"
  };

  try {
    const ndjson = await agentFetch<string>("/api/v1/admin/audit/export", {
      query,
      responseType: "text"
    });
    return new NextResponse(ndjson, {
      status: 200,
      headers: {
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Content-Disposition": "attachment; filename=support-agent-audit-export.ndjson"
      }
    });
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function clean(value: string | null, maxLength: number): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed.slice(0, maxLength) : null;
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

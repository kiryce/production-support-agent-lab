import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { AgentRunSearchResponse } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  try {
    const report = await agentFetch<AgentRunSearchResponse>("/api/v1/admin/runs", {
      query: {
        q: searchParams.get("q"),
        user_id: searchParams.get("userId"),
        conversation_id: searchParams.get("conversationId"),
        intent: searchParams.get("intent"),
        route: searchParams.get("route"),
        status: searchParams.get("status"),
        error_code: searchParams.get("errorCode"),
        created_after: searchParams.get("createdAfter"),
        created_before: searchParams.get("createdBefore"),
        limit: searchParams.get("limit") ?? 25,
        offset: searchParams.get("offset") ?? 0,
        order: searchParams.get("order") ?? "desc"
      }
    });
    return NextResponse.json(report);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

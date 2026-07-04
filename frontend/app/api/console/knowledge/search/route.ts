import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { KnowledgeSearchResponse } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const trace = await agentFetch<KnowledgeSearchResponse>("/api/v1/admin/knowledge/search", {
      method: "POST",
      body
    });
    return NextResponse.json(trace);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

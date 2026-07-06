import { NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { KnowledgeIndexSummary } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const summary = await agentFetch<KnowledgeIndexSummary>("/api/v1/admin/knowledge/summary");
    return NextResponse.json(summary);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

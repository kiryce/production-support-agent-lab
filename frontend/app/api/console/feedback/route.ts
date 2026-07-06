import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type {
  AgentFeedback,
  FeedbackReviewQueueResponse,
  FeedbackSearchResponse,
  FeedbackSummary
} from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const limit = String(clampNumber(searchParams.get("limit"), 1, 500, 50));
  const order = searchParams.get("order") === "asc" ? "asc" : "desc";
  const staleAfterHours = clampNumber(searchParams.get("staleAfterHours"), 1, 720, 48);
  const query = {
    conversation_id: searchParams.get("conversationId"),
    run_id: searchParams.get("runId"),
    user_id: searchParams.get("userId"),
    rating: searchParams.get("rating"),
    created_after: searchParams.get("createdAfter"),
    created_before: searchParams.get("createdBefore"),
    limit,
    order
  };

  try {
    const [items, summary, reviewQueue] = await Promise.all([
      agentFetch<AgentFeedback[]>("/api/v1/admin/feedback", { query }),
      agentFetch<FeedbackSummary>("/api/v1/admin/feedback/summary", { query }),
      agentFetch<FeedbackReviewQueueResponse>("/api/v1/admin/feedback/review-queue", {
        query: { ...query, stale_after_hours: staleAfterHours }
      })
    ]);
    const response: FeedbackSearchResponse = {
      items,
      summary,
      review_queue: reviewQueue,
      limit: Number(limit),
      order
    };
    return NextResponse.json(response);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function clampNumber(value: string | null, min: number, max: number, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

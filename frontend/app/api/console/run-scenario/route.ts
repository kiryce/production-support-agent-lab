import { NextRequest, NextResponse } from "next/server";
import { agentFetch, getAuthMode, issueFrom } from "@/src/server/agentApi";
import type { IncidentRunBundle, JsonRecord, MonitorSummary } from "@/src/shared/types";

export const dynamic = "force-dynamic";

type CreateSessionResponse = {
  conversation_id: string;
  user_id: string;
};

type ChatMessageResponse = {
  trace_id: string;
  handoff_required: boolean;
  citations: JsonRecord[];
};

const DEFAULT_SCENARIO =
  "My order A1001 headphones arrived broken. Can I return them or get help?";

export async function POST(request: NextRequest) {
  if (getAuthMode() !== "demo") {
    return NextResponse.json(
      {
        detail:
          "The local scenario runner is disabled in production auth mode. Use real support traffic or staging data."
      },
      { status: 409 }
    );
  }

  try {
    const payload = await request.json().catch(() => ({}));
    const content =
      typeof payload.content === "string" && payload.content.trim()
        ? payload.content.trim()
        : DEFAULT_SCENARIO;
    const userId = process.env.DEMO_ACTOR_USER_ID ?? "user_demo";
    const session = await agentFetch<CreateSessionResponse>("/api/v1/chat/sessions", {
      method: "POST",
      body: { user_id: userId }
    });
    const message = await agentFetch<ChatMessageResponse>("/api/v1/chat/messages", {
      method: "POST",
      body: {
        conversation_id: session.conversation_id,
        user_id: userId,
        content
      }
    });
    const [summary, incident] = await Promise.all([
      agentFetch<MonitorSummary>("/api/v1/admin/monitor/summary", {
        query: { source: "event_store", limit: 500 }
      }),
      agentFetch<IncidentRunBundle>(
        `/api/v1/admin/incidents/runs/${encodeURIComponent(message.trace_id)}`,
        { query: { include_memory: true, limit: 500 } }
      )
    ]);

    return NextResponse.json({
      session,
      message,
      summary,
      incident,
      content
    });
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { SQLiteBackupReport } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json().catch(() => ({}));
    const label = typeof payload.label === "string" ? payload.label.slice(0, 80) : "";
    const response = await agentFetch<SQLiteBackupReport>("/api/v1/admin/event-store/backups", {
      method: "POST",
      body: {
        label,
        overwrite: false,
        verify: true
      }
    });
    return NextResponse.json(response);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

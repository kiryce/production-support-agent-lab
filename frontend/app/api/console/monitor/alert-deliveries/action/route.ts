import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { AlertDeliveryRecord } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json();
    const deliveryId = typeof payload.deliveryId === "string" ? payload.deliveryId : "";
    const action = typeof payload.action === "string" ? payload.action : "";
    const note = typeof payload.note === "string" ? payload.note : "";
    if (!deliveryId) {
      return NextResponse.json({ detail: "deliveryId is required" }, { status: 400 });
    }
    if (action !== "replay" && action !== "close") {
      return NextResponse.json({ detail: "action must be replay or close" }, { status: 400 });
    }
    const upstreamAction = action === "replay" ? "requeue" : "close";
    const record = await agentFetch<AlertDeliveryRecord>(
      `/api/v1/admin/monitor/alert-deliveries/${encodeURIComponent(deliveryId)}/${upstreamAction}`,
      {
        method: "POST",
        body: { note }
      }
    );
    return NextResponse.json(record);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

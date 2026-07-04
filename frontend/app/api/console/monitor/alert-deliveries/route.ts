import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { AlertDeliveryRecord, AlertDeliveryStatus } from "@/src/shared/types";

export const dynamic = "force-dynamic";

const DELIVERY_STATUSES = new Set<AlertDeliveryStatus>([
  "pending",
  "in_progress",
  "sent",
  "failed",
  "dead",
  "closed"
]);

export async function GET(request: NextRequest) {
  try {
    const status = request.nextUrl.searchParams.get("status");
    if (status && status !== "all" && !DELIVERY_STATUSES.has(status as AlertDeliveryStatus)) {
      return NextResponse.json({ detail: "Unsupported delivery status" }, { status: 400 });
    }
    const records = await agentFetch<AlertDeliveryRecord[]>(
      "/api/v1/admin/monitor/alert-deliveries",
      {
        query: {
          alert_key: request.nextUrl.searchParams.get("alertKey"),
          status: status === "all" ? null : status,
          limit: request.nextUrl.searchParams.get("limit") ?? 50,
          order: request.nextUrl.searchParams.get("order") ?? "desc"
        }
      }
    );
    return NextResponse.json(records);
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

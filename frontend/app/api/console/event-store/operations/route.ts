import { NextRequest, NextResponse } from "next/server";
import { agentFetch, issueFrom } from "@/src/server/agentApi";
import type { EventStoreOperationRecord } from "@/src/shared/types";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const operation = safeString(searchParams.get("operation"), 80);
    const status = safeString(searchParams.get("status"), 40);
    const createdAfter = safeString(searchParams.get("created_after"), 64);
    const createdBefore = safeString(searchParams.get("created_before"), 64);
    const limit = clampNumber(searchParams.get("limit"), 1, 500, 50);
    const order = searchParams.get("order") === "asc" ? "asc" : "desc";
    const response = await agentFetch<EventStoreOperationRecord[]>(
      "/api/v1/admin/event-store/operations",
      {
        query: {
          operation,
          status,
          created_after: createdAfter,
          created_before: createdBefore,
          limit,
          order
        }
      }
    );
    return NextResponse.json({ records: response, limit, order });
  } catch (error) {
    const issue = issueFrom(error);
    return NextResponse.json({ detail: issue.detail }, { status: issue.status });
  }
}

function safeString(value: string | null, maxLength: number) {
  const trimmed = value?.trim() ?? "";
  return trimmed ? trimmed.slice(0, maxLength) : undefined;
}

function clampNumber(value: string | null, min: number, max: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

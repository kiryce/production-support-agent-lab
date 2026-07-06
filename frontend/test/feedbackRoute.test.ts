import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/console/feedback/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("feedback BFF route", () => {
  it("proxies feedback search, summary, and review queue with bounded params", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse({
          total_count: 0,
          positive_count: 0,
          negative_count: 0,
          negative_rate: 0,
          counts_by_reason: [],
          window_start: null,
          window_end: null
        })
      )
      .mockResolvedValueOnce(
        jsonResponse({
          schema_version: "feedback_review_queue.v1",
          generated_at: "2026-07-06T00:00:00Z",
          stale_after_hours: 720,
          limit: 500,
          order: "asc",
          summary: {
            total_count: 0,
            summary_source_count: 0,
            summary_truncated: false,
            reviewed_count: 0,
            unreviewed_count: 0,
            unresolved_count: 0,
            unassigned_unresolved_count: 0,
            stale_unresolved_count: 0,
            counts_by_status: {},
            oldest_unresolved_feedback_at: null,
            newest_review_at: null
          },
          items: []
        })
      );

    const response = await GET(
      getRequest("/api/console/feedback?limit=9999&order=asc&staleAfterHours=9999&rating=negative")
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.review_queue.schema_version).toBe("feedback_review_queue.v1");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const paths = fetchMock.mock.calls.map(([target]) => new URL(String(target)).pathname);
    expect(paths).toEqual([
      "/api/v1/admin/feedback",
      "/api/v1/admin/feedback/summary",
      "/api/v1/admin/feedback/review-queue"
    ]);
    const queueUrl = new URL(String(fetchMock.mock.calls[2][0]));
    expect(queueUrl.searchParams.get("limit")).toBe("500");
    expect(queueUrl.searchParams.get("order")).toBe("asc");
    expect(queueUrl.searchParams.get("rating")).toBe("negative");
    expect(queueUrl.searchParams.get("stale_after_hours")).toBe("720");
  });
});

function getRequest(path: string) {
  return { nextUrl: new URL(`http://console.local${path}`) } as unknown as NextRequest;
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}

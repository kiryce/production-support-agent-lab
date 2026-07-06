import { describe, expect, it, vi, afterEach } from "vitest";
import { GET } from "../app/api/console/monitor/review-worker/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("monitor review worker BFF route", () => {
  it("forwards to the backend worker summary endpoint", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "active",
          stale_after_seconds: 180,
          total_worker_count: 1,
          active_worker_count: 1,
          stale_worker_count: 0,
          last_seen_at: "2026-07-06T00:00:00.000Z",
          last_success_at: "2026-07-06T00:00:00.000Z",
          last_error: null,
          last_inspected_count: 2,
          last_reviewed_count: 1,
          last_skipped_existing_count: 1,
          last_skipped_unreviewable_count: 0,
          last_failed_count: 0
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    const response = await GET();

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.status).toBe("active");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [target] = fetchMock.mock.calls[0];
    expect(new URL(String(target)).pathname).toBe("/api/v1/admin/monitor/review-worker/summary");
  });
});

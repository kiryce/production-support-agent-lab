import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/console/monitor/alert-deliveries/dispatch/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("alert delivery dispatch BFF route", () => {
  it("forwards dispatch requests with backend query parameter names", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          webhook_enabled: true,
          enqueued_count: 1,
          existing_count: 0,
          skipped_count: 0,
          claimed_count: 1,
          attempted_count: 1,
          sent_count: 1,
          failed_count: 0,
          dead_count: 0,
          deliveries: []
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    const response = await POST(
      new Request("http://console.local/api/console/monitor/alert-deliveries/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: "invalid-source",
          monitorLimit: 999,
          dispatchLimit: 0
        })
      }) as unknown as NextRequest
    );

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.sent_count).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [target, init] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(init?.method).toBe("POST");
    expect(url.pathname).toBe("/api/v1/admin/monitor/alert-deliveries/dispatch");
    expect(url.searchParams.get("source")).toBe("event_store");
    expect(url.searchParams.get("monitor_limit")).toBe("500");
    expect(url.searchParams.get("dispatch_limit")).toBe("1");
    expect(url.searchParams.has("limit")).toBe(false);
  });
});

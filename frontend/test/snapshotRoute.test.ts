import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/console/snapshot/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("console snapshot BFF route", () => {
  it("summarizes append-only events without raw payload data", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (target) => {
      const url = new URL(String(target));
      if (url.pathname === "/api/v1/admin/monitor/summary") {
        return jsonResponse({
          alerts: [
            {
              key: "agent:order:TIMEOUT",
              sample_run_ids: ["run_private"]
            }
          ]
        });
      }
      if (url.pathname === "/api/v1/admin/incidents/runs/run_private") {
        return jsonResponse({
          run: {
            id: "run_private",
            conversation_id: "conv_private"
          },
          monitor_events: []
        });
      }
      if (url.pathname === "/api/v1/admin/events") {
        return jsonResponse([
          {
            id: "evt_private",
            tenant_id: "tenant_live",
            conversation_id: "conv_private",
            user_id: "user_private",
            run_id: "run_private",
            event_type: "message.user",
            payload: {
              content: "PRIVATE_USER_MESSAGE",
              comment: "PRIVATE_FEEDBACK_COMMENT",
              note: "PRIVATE_REVIEW_NOTE"
            },
            created_at: "2026-07-06T00:00:00Z"
          }
        ]);
      }
      if (url.pathname.endsWith("/triage") || url.pathname === "/api/v1/admin/evals/gates") {
        return jsonResponse([]);
      }
      if (url.pathname === "/api/v1/admin/tools") {
        return jsonResponse([]);
      }
      return jsonResponse({});
    });

    const response = await GET(getRequest("/api/console/snapshot"));

    expect(response.status).toBe(200);
    const body = await response.json();
    const responseText = JSON.stringify(body);
    expect(responseText).not.toContain("PRIVATE_USER_MESSAGE");
    expect(responseText).not.toContain("PRIVATE_FEEDBACK_COMMENT");
    expect(responseText).not.toContain("PRIVATE_REVIEW_NOTE");
    expect(body.rawEvents).toEqual([
      {
        id: "evt_private",
        event_type: "message.user",
        created_at: "2026-07-06T00:00:00Z"
      }
    ]);
    expect(body.rawEvents[0]).not.toHaveProperty("payload");
    expect(body.rawEvents[0]).not.toHaveProperty("user_id");
    expect(fetchMock).toHaveBeenCalled();
  });
});

function getRequest(path: string) {
  return { nextUrl: new URL(`http://console.local${path}`) } as unknown as NextRequest;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

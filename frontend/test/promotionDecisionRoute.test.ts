import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/console/promotion/decisions/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("promotion decision BFF route", () => {
  it("forwards a sanitized release decision payload", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        id: "release_123",
        tenant_id: "demo_tenant",
        environment: "staging",
        target_version: "agent-next",
        decision: "deferred",
        gate_status: "blocked",
        gate: {
          status: "blocked",
          generated_at: "2026-07-05T00:00:00Z",
          environment: "staging",
          source: "event_store",
          window_hours: 24,
          thresholds: {
            max_active_p0p1_alerts: 0,
            max_active_alerts: 10,
            max_tool_failure_rate: 0.05,
            max_feedback_negative_rate: 0.4,
            max_eval_age_hours: 24,
            min_tool_calls: 1,
            min_feedback_count: 5
          },
          checks: [],
          readiness: { status: "ok", environment: "staging", deep: true, ops: true, checks: [] },
          monitor: {},
          tool_audit: {},
          feedback: {},
          latest_eval_gate: null
        },
        note: "wait",
        override_blocked: false,
        override_reason: "",
        actor_user_id: "user_demo",
        created_at: "2026-07-05T00:00:00Z"
      })
    );

    const response = await POST(
      jsonRequest("/api/console/promotion/decisions", {
        target_version: "agent-next",
        decision: "approve please",
        note: "wait",
        override_blocked: "yes",
        override_reason: "x",
        source: "file",
        deep: false,
        ops: false,
        window_hours: 999,
        max_tool_failure_rate: -1,
        min_feedback_count: -5
      })
    );

    expect(response.status).toBe(200);
    const [target, init] = fetchMock.mock.calls[0];
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/promotion/decisions");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      target_version: "agent-next",
      decision: "deferred",
      note: "wait",
      override_blocked: false,
      override_reason: "x",
      source: "event_store",
      deep: true,
      ops: true,
      window_hours: 168,
      max_tool_failure_rate: 0,
      min_feedback_count: 0
    });
  });
});

function jsonRequest(path: string, body: unknown) {
  return new Request(`http://console.local${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }) as unknown as NextRequest;
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}

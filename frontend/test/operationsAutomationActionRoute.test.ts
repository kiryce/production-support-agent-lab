import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/console/operations/automation-actions/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("operations automation action BFF route", () => {
  it("executes the backend-plan command instead of trusting a client supplied path", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (target) => {
      const url = new URL(String(target));
      if (url.pathname === "/api/v1/admin/operations/automation-plan") {
        return Response.json(planWithAction({
          id: "ops_inspect_tool_audit_123",
          kind: "inspect_tool_audit",
          safe_to_auto_execute: true,
          command: {
            method: "GET",
            path: "/api/v1/admin/tools/audit",
            query: { status: "failed", limit: 100, order: "desc" },
            body: {}
          }
        }));
      }
      if (url.pathname === "/api/v1/admin/tools/audit") {
        return Response.json([{ id: "audit_1", tool_name: "order.get" }]);
      }
      return Response.json({ detail: `unexpected ${url.pathname}` }, { status: 500 });
    });

    const response = await POST(
      jsonRequest("/api/console/operations/automation-actions", {
        actionId: "ops_inspect_tool_audit_123",
        command: { method: "GET", path: "/api/v1/admin/events", query: {}, body: {} }
      })
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.schema_version).toBe("ops_action_execution.v1");
    expect(body.result_summary).toBe("1 tool audit record(s) loaded.");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(new URL(String(fetchMock.mock.calls[1][0])).pathname).toBe("/api/v1/admin/tools/audit");
    expect(new URL(String(fetchMock.mock.calls[1][0])).searchParams.get("status")).toBe("failed");
  });

  it("rejects manual actions even when the backend plan includes a command", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(planWithAction({
        id: "ops_requeue_dead_delivery_123",
        kind: "requeue_dead_delivery",
        safe_to_auto_execute: false,
        command: {
          method: "POST",
          path: "/api/v1/admin/monitor/alert-deliveries/del_1/requeue",
          query: {},
          body: { note: "checked" }
        }
      }))
    );

    const response = await POST(
      jsonRequest("/api/console/operations/automation-actions", {
        actionId: "ops_requeue_dead_delivery_123"
      })
    );

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      detail: "Manual automation actions require an operator workflow and cannot be executed here"
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("executes the read-only missing receipt inspection command", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (target) => {
      const url = new URL(String(target));
      if (url.pathname === "/api/v1/admin/operations/automation-plan") {
        return Response.json(planWithAction({
          id: "ops_inspect_missing_alert_receipts_123",
          kind: "inspect_missing_alert_receipts",
          safe_to_auto_execute: true,
          command: {
            method: "GET",
            path: "/api/v1/admin/monitor/alert-deliveries/receipt-gaps",
            query: { limit: 100, order: "asc" },
            body: {}
          }
        }));
      }
      if (url.pathname === "/api/v1/admin/monitor/alert-deliveries/receipt-gaps") {
        return Response.json([{ id: "deliv_missing", status: "sent" }]);
      }
      return Response.json({ detail: `unexpected ${url.pathname}` }, { status: 500 });
    });

    const response = await POST(
      jsonRequest("/api/console/operations/automation-actions", {
        actionId: "ops_inspect_missing_alert_receipts_123"
      })
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.result_summary).toBe("1 sent delivery receipt gap(s) loaded.");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const executedUrl = new URL(String(fetchMock.mock.calls[1][0]));
    expect(executedUrl.pathname).toBe("/api/v1/admin/monitor/alert-deliveries/receipt-gaps");
    expect(executedUrl.searchParams.get("order")).toBe("asc");
  });

  it("rejects auto-safe actions whose generated command is outside the allowlist", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(planWithAction({
        id: "ops_inspect_tool_audit_unsafe",
        kind: "inspect_tool_audit",
        safe_to_auto_execute: true,
        command: {
          method: "GET",
          path: "/api/v1/admin/events",
          query: {},
          body: {}
        }
      }))
    );

    const response = await POST(
      jsonRequest("/api/console/operations/automation-actions", {
        actionId: "ops_inspect_tool_audit_unsafe"
      })
    );

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      detail: "Automation command is outside the console execution allowlist"
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

function planWithAction(action: {
  id: string;
  kind: string;
  safe_to_auto_execute: boolean;
  command: {
    method: "GET" | "POST";
    path: string;
    query: Record<string, string | number | boolean>;
    body: Record<string, unknown>;
  };
}) {
  return {
    schema_version: "ops_automation.v1",
    generated_at: "2026-07-05T00:00:00.000Z",
    environment: "local",
    source: "event_store",
    window_hours: 24,
    health_status: "degraded",
    action_count: 1,
    auto_executable_count: action.safe_to_auto_execute ? 1 : 0,
    actions: [
      {
        priority: "P2",
        title: "Inspect elevated tool failure rate",
        detail: "Tool failure rate is high.",
        required_scopes: ["audit:read"],
        evidence: {},
        ...action
      }
    ],
    evidence: {},
    guardrails: ["Server-generated plan."]
  };
}

function jsonRequest(path: string, body: unknown) {
  return {
    nextUrl: new URL(`http://console.local${path}`),
    json: async () => body
  } as unknown as NextRequest;
}

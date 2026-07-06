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
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (target, init) => {
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
      if (url.pathname === "/api/v1/admin/operations/automation-executions") {
        const body = JSON.parse(String(init?.body));
        expect(body.command.path).toBe("/api/v1/admin/tools/audit");
        expect(body.command.path).not.toBe("/api/v1/admin/events");
        return Response.json(executionRecord({
          action_id: body.action_id,
          action_kind: body.action_kind,
          status: body.status,
          result_summary: body.result_summary
        }));
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
    expect(body.audit_recorded).toBe(true);
    expect(body.audit_record.status).toBe("completed");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(new URL(String(fetchMock.mock.calls[1][0])).pathname).toBe("/api/v1/admin/tools/audit");
    expect(new URL(String(fetchMock.mock.calls[1][0])).searchParams.get("status")).toBe("failed");
    expect(new URL(String(fetchMock.mock.calls[2][0])).pathname).toBe(
      "/api/v1/admin/operations/automation-executions"
    );
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
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (target, init) => {
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
      if (url.pathname === "/api/v1/admin/operations/automation-executions") {
        const body = JSON.parse(String(init?.body));
        return Response.json(executionRecord({
          action_id: body.action_id,
          action_kind: body.action_kind,
          status: body.status,
          result_summary: body.result_summary
        }));
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
    expect(body.audit_recorded).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const executedUrl = new URL(String(fetchMock.mock.calls[1][0]));
    expect(executedUrl.pathname).toBe("/api/v1/admin/monitor/alert-deliveries/receipt-gaps");
    expect(executedUrl.searchParams.get("order")).toBe("asc");
  });

  it("records failed backend command execution before returning the error", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (target, init) => {
      const url = new URL(String(target));
      if (url.pathname === "/api/v1/admin/operations/automation-plan") {
        return Response.json(planWithAction({
          id: "ops_inspect_tool_audit_failed",
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
        return Response.json({ detail: "tool audit backend unavailable" }, { status: 503 });
      }
      if (url.pathname === "/api/v1/admin/operations/automation-executions") {
        const body = JSON.parse(String(init?.body));
        expect(body.status).toBe("failed");
        expect(body.error_detail).toBe("tool audit backend unavailable");
        return Response.json(executionRecord({
          action_id: body.action_id,
          action_kind: body.action_kind,
          status: body.status,
          result_summary: body.result_summary
        }));
      }
      return Response.json({ detail: `unexpected ${url.pathname}` }, { status: 500 });
    });

    const response = await POST(
      jsonRequest("/api/console/operations/automation-actions", {
        actionId: "ops_inspect_tool_audit_failed"
      })
    );

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "tool audit backend unavailable" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
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

function executionRecord(input: {
  action_id: string;
  action_kind: string;
  status: string;
  result_summary: string;
}) {
  return {
    id: "ops_exec_123",
    tenant_id: "demo_tenant",
    actor_user_id: "console_operator",
    title: "Inspect elevated tool failure rate",
    safe_to_auto_execute: true,
    command_method: "GET",
    command_path: "/api/v1/admin/tools/audit",
    command_query: {},
    command_body_keys: [],
    command_body_hash: null,
    command_fingerprint: "fingerprint",
    error_detail: null,
    source: "console",
    created_at: "2026-07-05T00:00:01.000Z",
    ...input
  };
}

function jsonRequest(path: string, body: unknown) {
  return {
    nextUrl: new URL(`http://console.local${path}`),
    json: async () => body
  } as unknown as NextRequest;
}

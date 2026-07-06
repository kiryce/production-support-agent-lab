import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/console/operations/automation-executions/route";
import { GET as summaryGet } from "../app/api/console/operations/automation-executions/summary/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("operations automation execution ledger BFF route", () => {
  it("lists automation execution rows with sanitized filters", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse([
        {
          id: "ops_exec_1",
          tenant_id: "demo_tenant",
          actor_user_id: "console_operator",
          action_id: "ops_inspect_tool_audit_123",
          action_kind: "inspect_tool_audit",
          title: "Inspect tool failures",
          status: "completed",
          safe_to_auto_execute: true,
          command_method: "GET",
          command_path: "/api/v1/admin/tools/audit",
          command_query: { status: "failed" },
          command_body_keys: [],
          command_body_hash: null,
          command_fingerprint: "fingerprint",
          result_summary: "1 tool audit record(s) loaded.",
          error_detail: null,
          source: "console",
          created_at: "2026-07-05T00:00:00Z"
        }
      ])
    );

    const response = await GET(
      getRequest(
        "/api/console/operations/automation-executions?action_kind=inspect_tool_audit&status=completed&source=console&actor_user_id=console_operator&limit=9999&order=sideways&created_after=2026-07-05"
      )
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      records: [
        {
          id: "ops_exec_1",
          tenant_id: "demo_tenant",
          actor_user_id: "console_operator",
          action_id: "ops_inspect_tool_audit_123",
          action_kind: "inspect_tool_audit",
          title: "Inspect tool failures",
          status: "completed",
          safe_to_auto_execute: true,
          command_method: "GET",
          command_path: "/api/v1/admin/tools/audit",
          command_query: { status: "failed" },
          command_body_keys: [],
          command_body_hash: null,
          command_fingerprint: "fingerprint",
          result_summary: "1 tool audit record(s) loaded.",
          error_detail: null,
          source: "console",
          created_at: "2026-07-05T00:00:00Z"
        }
      ],
      limit: 500,
      order: "desc"
    });
    const [target] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(url.pathname).toBe("/api/v1/admin/operations/automation-executions");
    expect(url.searchParams.get("action_kind")).toBe("inspect_tool_audit");
    expect(url.searchParams.get("status")).toBe("completed");
    expect(url.searchParams.get("source")).toBe("console");
    expect(url.searchParams.get("actor_user_id")).toBe("console_operator");
    expect(url.searchParams.get("limit")).toBe("500");
    expect(url.searchParams.get("order")).toBe("desc");
    expect(url.searchParams.get("created_after")).toBe("2026-07-05");
  });

  it("drops invalid enum filters before calling the backend", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse([]));

    const response = await GET(
      getRequest(
        "/api/console/operations/automation-executions?status=raw&source=browser&limit=0&order=asc"
      )
    );

    expect(response.status).toBe(200);
    const [target] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(url.searchParams.has("status")).toBe(false);
    expect(url.searchParams.has("source")).toBe(false);
    expect(url.searchParams.get("limit")).toBe("1");
    expect(url.searchParams.get("order")).toBe("asc");
  });

  it("proxies the automation execution summary with bounded filters", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        schema_version: "ops_automation_execution_summary.v1",
        total_count: 2,
        completed_count: 1,
        failed_count: 1,
        rejected_count: 0,
        failure_rate: 0.5,
        counts_by_status: { completed: 1, failed: 1 },
        counts_by_source: { console: 1, cron: 1 },
        counts_by_action_kind: { inspect_tool_audit: 1, dispatch_alert_deliveries: 1 },
        window_start: "2026-07-05T00:00:00Z",
        window_end: "2026-07-05T01:00:00Z",
        latest_execution_at: "2026-07-05T01:00:00Z",
        latest_failure_at: "2026-07-05T01:00:00Z",
        latest_failure_action_kind: "dispatch_alert_deliveries",
        latest_failure_source: "cron"
      })
    );

    const response = await summaryGet(
      getRequest(
        "/api/console/operations/automation-executions/summary?action_kind=dispatch_alert_deliveries&source=browser&window_hours=999"
      )
    );

    expect(response.status).toBe(200);
    expect((await response.json()).failure_rate).toBe(0.5);
    const [target] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(url.pathname).toBe("/api/v1/admin/operations/automation-executions/summary");
    expect(url.searchParams.get("action_kind")).toBe("dispatch_alert_deliveries");
    expect(url.searchParams.has("source")).toBe(false);
    expect(url.searchParams.get("window_hours")).toBe("168");
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

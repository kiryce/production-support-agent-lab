import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/console/audit/export/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("audit export BFF route", () => {
  it("proxies sanitized NDJSON export queries", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"record_type":"tool_audit"}\n', {
        status: 200,
        headers: { "Content-Type": "application/x-ndjson" }
      })
    );

    const response = await GET(
      getRequest(
        "/api/console/audit/export?limit=999999&order=sideways&include_events=false&include_tool_audit=true&include_event_store_operations=false&include_operations_automation_executions=false&event_type=message.user"
      )
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/x-ndjson");
    expect(await response.text()).toContain("tool_audit");
    const [target] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(url.pathname).toBe("/api/v1/admin/audit/export");
    expect(url.searchParams.get("limit")).toBe("5000");
    expect(url.searchParams.get("order")).toBe("asc");
    expect(url.searchParams.get("include_events")).toBe("false");
    expect(url.searchParams.get("include_tool_audit")).toBe("true");
    expect(url.searchParams.get("include_event_store_operations")).toBe("false");
    expect(url.searchParams.get("include_operations_automation_executions")).toBe("false");
    expect(url.searchParams.get("event_type")).toBe("message.user");
  });
});

function getRequest(path: string) {
  return { nextUrl: new URL(`http://console.local${path}`) } as unknown as NextRequest;
}

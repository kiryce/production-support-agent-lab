import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/console/operations/slo-report/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("SLO report BFF route", () => {
  it("proxies the backend SLO report with bounded objective thresholds", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        schema_version: "slo_report.v1",
        generated_at: "2026-07-05T00:00:00.000Z",
        environment: "development",
        source: "event_store",
        window_hours: 24,
        status: "watch",
        objective_count: 1,
        met_count: 0,
        at_risk_count: 1,
        breached_count: 0,
        no_data_count: 0,
        objectives: [
          {
            name: "grounded_rate",
            status: "at_risk",
            target_type: "minimum",
            target: { min_rate: 0.95 },
            observed: { rate: 0.96, sample_count: 20 },
            error_budget_remaining: 0.2,
            detail: "Grounded answer rate is 96.0%; target is at least 95.0%.",
            evidence: {}
          }
        ],
        evidence: {},
        guardrails: ["Read-only aggregate report."]
      })
    );

    const response = await GET(
      getRequest(
        "/api/console/operations/slo-report?source=live&window_hours=999&min_grounded_rate=1.5&max_mtta_seconds=0&max_tool_failure_rate=0.25&max_automation_failure_rate=1.5&min_automation_executions=99999"
      )
    );

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.schema_version).toBe("slo_report.v1");
    const [target] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(url.pathname).toBe("/api/v1/admin/operations/slo-report");
    expect(url.searchParams.get("source")).toBe("live");
    expect(url.searchParams.get("window_hours")).toBe("168");
    expect(url.searchParams.get("min_grounded_rate")).toBe("1");
    expect(url.searchParams.get("max_mtta_seconds")).toBe("1");
    expect(url.searchParams.get("max_tool_failure_rate")).toBe("0.25");
    expect(url.searchParams.get("max_automation_failure_rate")).toBe("1");
    expect(url.searchParams.get("min_automation_executions")).toBe("10000");
  });
});

function getRequest(path: string) {
  return { nextUrl: new URL(`http://console.local${path}`) } as unknown as NextRequest;
}

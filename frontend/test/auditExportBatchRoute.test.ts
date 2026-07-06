import { describe, expect, it, vi, afterEach } from "vitest";
import { GET } from "../app/api/console/audit/export-batches/summary/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("audit export batch BFF route", () => {
  it("forwards to the backend batch summary endpoint", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "audit_export_batch_summary.v1",
          status: "fresh",
          stale_after_seconds: 86400,
          total_batch_count: 1,
          completed_batch_count: 1,
          failed_batch_count: 0,
          last_status: "completed",
          last_exported_at: "2026-07-06T00:00:00.000Z",
          last_record_count: 3,
          last_record_type_counts: { event: 1, tool_audit: 2 },
          last_bytes_written: 4096,
          last_output_file: "support-agent-audit-demo-20260706.ndjson",
          last_manifest_file: "support-agent-audit-demo-20260706.manifest.json",
          last_content_sha256: "a".repeat(64),
          last_partial: false,
          last_error_type: null
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    const response = await GET();

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.status).toBe("fresh");
    expect(payload.last_manifest_file).toContain(".manifest.json");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [target] = fetchMock.mock.calls[0];
    expect(new URL(String(target)).pathname).toBe("/api/v1/admin/audit/export-batches/summary");
  });
});
